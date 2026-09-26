"""Tests for the intraday SPAN baseline refresh: dedupe, Redis versioning, and the slots."""
from __future__ import annotations

import io
import sqlite3
import zipfile

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services import nsccl_baseline
from icici_breeze_backend.app.services.reference_data import span_scheduler, span_sources
from icici_breeze_backend.app.services.reference_data.keys import CURRENT_VERSION_KEY, version_prefix
from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
    publish_span_baseline_from_db,
)

_SPAN_XML = """<?xml version="1.0"?>
<spanFile>
<fileFormat>4.00</fileFormat>
<created>202609041531</created>
<oopPf>
<pfCode>NIFTY</pfCode>
<series>
<pe>20260924</pe>
<opt><o>C</o><k>24000</k><ra><a>-1000.0</a><a>-900.0</a></ra></opt>
<opt><o>P</o><k>24000</k><ra><a>-800.0</a><a>-700.0</a></ra></opt>
</series>
</oopPf>
</spanFile>
"""


def _archive_bytes(inner_name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(inner_name, _SPAN_XML)
    return buf.getvalue()


def _ref(archive_name: str, version: int) -> span_sources.SpanArchiveRef:
    return span_sources.SpanArchiveRef(
        market=span_sources.MARKET_NSE,
        exchange_code=cfg.NFO,
        archive_name=archive_name,
        url=f"https://nsearchives.nseindia.com/archives/nsccl/span/{archive_name}",
        source_date="20260904",
        source_version=version,
        label=f"Intra-Day {version:02d}",
    )


@pytest.fixture
def scrip_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    conn = sqlite3.connect(tmp_path / "scrips.sqlite3")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS scrip_master "
        "(ShortName TEXT, ExchangeCode TEXT, ExpiryDate TEXT, StrikePrice TEXT, "
        "OptionType TEXT, LotSize INTEGER)"
    )
    conn.commit()
    conn.close()
    nsccl_baseline.ensure_exchange_margin_baseline_table()
    return tmp_path


@pytest.fixture
def stub_nse_only(monkeypatch):
    """BSE resolves to nothing so each test exercises one market."""
    monkeypatch.setattr(span_sources, "resolve_latest_bse_span_archive", lambda **kw: None)


def test_refresh_ingests_the_newest_archive(scrip_db, stub_nse_only, monkeypatch):
    monkeypatch.setattr(
        span_sources, "resolve_latest_nse_span_archive", lambda **kw: _ref("nsccl.20260904.i5.zip", 5)
    )
    monkeypatch.setattr(
        span_sources, "download_span_archive", lambda ref: _archive_bytes("nsccl.20260904.i05.spn")
    )

    out = nsccl_baseline.refresh_span_baseline("nse")

    assert out["Status"] == 200
    assert out["Success"]["skipped"] is False
    assert out["Success"]["inserted_rows"] == 2
    assert out["Success"]["source_version"] == 5
    assert nsccl_baseline.current_baseline_archive_name(cfg.NFO) == "nsccl.20260904.i5.zip"


def test_refresh_skips_the_download_when_the_archive_is_already_loaded(scrip_db, stub_nse_only, monkeypatch):
    """The expected outcome for most slots -- and what makes a slot that fires seconds before
    the exchange stamps its file harmless rather than wasteful."""
    ref = _ref("nsccl.20260904.i5.zip", 5)
    monkeypatch.setattr(span_sources, "resolve_latest_nse_span_archive", lambda **kw: ref)
    downloads: list[str] = []

    def counting_download(r):
        downloads.append(r.archive_name)
        return _archive_bytes("nsccl.20260904.i05.spn")

    monkeypatch.setattr(span_sources, "download_span_archive", counting_download)

    first = nsccl_baseline.refresh_span_baseline("nse")
    second = nsccl_baseline.refresh_span_baseline("nse")

    assert first["Success"]["skipped"] is False
    assert second["Status"] == 200
    assert second["Success"]["skipped"] is True
    assert downloads == ["nsccl.20260904.i5.zip"]


def test_force_downloads_even_when_unchanged(scrip_db, stub_nse_only, monkeypatch):
    ref = _ref("nsccl.20260904.i5.zip", 5)
    monkeypatch.setattr(span_sources, "resolve_latest_nse_span_archive", lambda **kw: ref)
    downloads: list[str] = []
    monkeypatch.setattr(
        span_sources,
        "download_span_archive",
        lambda r: (downloads.append(r.archive_name), _archive_bytes("x.spn"))[1],
    )

    nsccl_baseline.refresh_span_baseline("nse")
    out = nsccl_baseline.refresh_span_baseline("nse", force=True)

    assert out["Success"]["skipped"] is False
    assert len(downloads) == 2


def test_a_newer_revision_replaces_the_previous_one(scrip_db, stub_nse_only, monkeypatch):
    monkeypatch.setattr(
        span_sources, "download_span_archive", lambda ref: _archive_bytes("inner.spn")
    )
    monkeypatch.setattr(
        span_sources, "resolve_latest_nse_span_archive", lambda **kw: _ref("nsccl.20260904.i4.zip", 4)
    )
    nsccl_baseline.refresh_span_baseline("nse")
    monkeypatch.setattr(
        span_sources, "resolve_latest_nse_span_archive", lambda **kw: _ref("nsccl.20260904.i5.zip", 5)
    )
    out = nsccl_baseline.refresh_span_baseline("nse")

    assert out["Success"]["skipped"] is False
    assert nsccl_baseline.current_baseline_archive_name(cfg.NFO) == "nsccl.20260904.i5.zip"
    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        versions = {r[0] for r in conn.execute("SELECT source_version FROM exchange_margin_baseline")}
    assert versions == {5}


def test_unknown_market_is_rejected(scrip_db):
    out = nsccl_baseline.refresh_span_baseline("mcx")
    assert out["Status"] == 400
    assert "market must be nse or bse" in out["Error"]


def test_standalone_publish_keeps_the_live_version_and_its_other_reference_data(scrip_db, monkeypatch):
    """A SPAN-only publish must not flip the version pointer: bumping purges the previous
    generation, and only SPAN sheets would have been written to the new one."""
    redis = get_redis()
    redis.set(CURRENT_VERSION_KEY, "7")
    scrip_key = f"{version_prefix(7)}:scrip:sentinel"
    redis.set(scrip_key, "kept")

    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO exchange_margin_baseline (
                exchange_code, short_name, expiry_date, strike_price, option_type,
                margin_per_lot, lot_size, risk_array, source_file, source_date,
                source_version, refreshed_at
            ) VALUES ('NFO', 'NIFTY', '24-Sep-2026', 24000, 'CE', 1000.0, 75, NULL,
                      'nsccl.20260904.i5.zip:inner.spn', '20260904', 5, datetime('now'))
            """
        )
        conn.commit()

    ver = publish_span_baseline_from_db()

    assert ver == 7
    assert redis.get(CURRENT_VERSION_KEY) in (b"7", "7")
    assert redis.get(scrip_key) is not None


def test_coordinated_batch_still_bumps_the_version(scrip_db):
    """The full reference-data load allocates one version for every source and flips to it."""
    redis = get_redis()
    redis.set(CURRENT_VERSION_KEY, "7")

    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO exchange_margin_baseline (
                exchange_code, short_name, expiry_date, strike_price, option_type,
                margin_per_lot, lot_size, risk_array, source_file, source_date,
                source_version, refreshed_at
            ) VALUES ('NFO', 'NIFTY', '24-Sep-2026', 24000, 'CE', 1000.0, 75, NULL,
                      'batch.zip:inner.spn', '20260904', 5, datetime('now'))
            """
        )
        conn.commit()

    publish_span_baseline_from_db(version=8)

    assert redis.get(CURRENT_VERSION_KEY) in (b"8", "8")


def test_slots_cover_both_exchanges_publish_schedules():
    slots = [f"{h:02d}:{m:02d}" for h, m in cfg.SPAN_REFRESH_SLOTS_IST]
    # 21:45 takes NSE's next-day i1, published ~21:30 the evening before (#48).
    assert slots == ["09:15", "11:15", "12:45", "14:15", "15:45", "18:00", "21:45"]


def test_scheduler_logs_ingests_but_not_no_ops(monkeypatch):
    """Six slots across two exchanges would bury the daily bhavcopy and scrip entries if every
    already-current check were logged."""
    recorded: list[dict] = []
    monkeypatch.setattr(span_scheduler, "append_ingest_history", recorded.append)

    span_scheduler._record(
        "nse",
        {"Status": 200, "Success": {"skipped": True, "source_file": "nsccl.20260904.i5.zip"}},
        slot="11:15",
    )
    assert recorded == []

    span_scheduler._record(
        "bse",
        {
            "Status": 200,
            "Success": {
                "skipped": False,
                "source_file": "BSERISK20260904-04.ZIP",
                "source_date": "20260904",
                "inserted_rows": 12,
                "source_url": "https://www.bseindia.com/bsedata/x.ZIP",
            },
        },
        slot="15:45",
    )
    assert len(recorded) == 1
    assert recorded[0]["kind"] == "bse_span_baseline"
    assert recorded[0]["row_count"] == 12
    assert "15:45" in recorded[0]["notes"]


def test_scheduler_logs_failures(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(span_scheduler, "append_ingest_history", recorded.append)

    span_scheduler._record("nse", {"Status": 400, "Error": "Download failed", "Success": None}, slot="18:00")

    assert len(recorded) == 1
    assert recorded[0]["ok"] is False
    assert "Download failed" in recorded[0]["notes"]


_STOCK_SPAN_XML = """<?xml version="1.0"?>
<spanFile>
<fileFormat>4.00</fileFormat>
<created>202609071531</created>
<oopPf>
<pfCode>ADANIENSOL</pfCode>
<series>
<pe>20260929</pe>
<opt><o>C</o><k>1820</k><ra><a>-89.9</a><a>0.9</a></ra></opt>
</series>
</oopPf>
</spanFile>
"""


def test_stock_lot_size_joins_through_exchange_code(scrip_db, monkeypatch):
    """The SPAN pfCode is the NSE symbol; scrip_master carries it as ExchangeCode, never as
    ShortName. Without that bridge the row lands lot-less and every downstream margin lookup
    rejects it, so the whole Exchange Risk Baseline source is dead for stocks."""
    conn = sqlite3.connect(scrip_db / "scrips.sqlite3")
    conn.execute(
        "INSERT INTO scrip_master (ShortName, ExchangeCode, ExpiryDate, StrikePrice, OptionType, LotSize)"
        " VALUES ('ADATRA', 'ADANIENSOL', '29-Sep-2026', '1820', 'CE', 675)"
    )
    conn.commit()

    with conn:
        inserted, _skipped = nsccl_baseline._ingest_span_xml_stream(
            conn,
            io.BytesIO(_STOCK_SPAN_XML.encode()),
            exchange_code=cfg.NFO,
            source_file="nsccl.20260907.i1.zip:nsccl.20260907.i01.spn",
            source_date="20260907",
            source_version=1,
            allowed_pf_codes=None,
        )
    assert inserted == 1

    row = conn.execute(
        "SELECT short_name, lot_size, margin_per_lot FROM exchange_margin_baseline"
    ).fetchone()
    conn.close()
    # Stored under the pfCode, with the lot the ICICI code owns, and priced per lot not per unit.
    assert row == ("ADANIENSOL", 675, 89.9 * 675)


def test_index_lot_size_joins_through_alias(scrip_db, monkeypatch):
    """BANKNIFTY (pfCode) / CNXBAN (ICICI) / NIFTY BANK (ExchangeCode) share no spelling."""
    conn = sqlite3.connect(scrip_db / "scrips.sqlite3")
    conn.execute(
        "INSERT INTO scrip_master (ShortName, ExchangeCode, ExpiryDate, StrikePrice, OptionType, LotSize)"
        " VALUES ('CNXBAN', 'NIFTY BANK', '24-Sep-2026', '54000', 'PE', 35)"
    )
    conn.commit()

    xml = _STOCK_SPAN_XML.replace("ADANIENSOL", "BANKNIFTY").replace(
        "<pe>20260929</pe>", "<pe>20260924</pe>"
    ).replace("<o>C</o><k>1820</k>", "<o>P</o><k>54000</k>")
    with conn:
        nsccl_baseline._ingest_span_xml_stream(
            conn,
            io.BytesIO(xml.encode()),
            exchange_code=cfg.NFO,
            source_file="nsccl.zip:nsccl.spn",
            source_date="20260907",
            source_version=1,
            allowed_pf_codes=None,
        )
    row = conn.execute("SELECT short_name, lot_size FROM exchange_margin_baseline").fetchone()
    conn.close()
    assert row == ("BANKNIFTY", 35)
