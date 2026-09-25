"""NSCCL SPAN baseline loader and lookup utilities."""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.reference_data.symbol_registry import aliases_for, exchange_symbol_for
from icici_breeze_backend.app.core.strike import Strike, parse_strike
from icici_breeze_backend.app.core.timezone import ist_timestamp
from icici_breeze_backend.app.services.reference_data import span_sources

_logger = logging.getLogger(__name__)

MARGIN_SOURCE_BREEZE = "breeze_api"
MARGIN_SOURCE_EXCHANGE = "exchange_baseline"

# BSE SPAN uses pfCode BSXOPT / BKXOPT; scrip_master / Strategy Builder use BSESEN and BANKEX on BFO (ICICI codes).
BSE_BASELINE_PF_CODES = frozenset({"BSXOPT", "BKXOPT"})
BSE_SPAN_PF_CODE_TO_SHORT_NAME = {"BSXOPT": "BSESEN", "BKXOPT": "BANKEX"}
# The same two underlyings under the codes the physical portfolio and combined commodity use
# (the option portfolio appends OPT; `phyPf`/`ccDef` do not).
BSE_SPAN_UNDERLYING_TO_SHORT_NAME = {"BSX": "BSESEN", "BKX": "BANKEX"}


_BASELINE_DB_COLUMNS = (
    "exchange_code",
    "short_name",
    "expiry_date",
    "strike_price",
    "option_type",
    "margin_per_lot",
    "lot_size",
    "risk_array",
    "settle_price",
    "source_file",
    "source_date",
    "source_version",
    "refreshed_at",
)

_EXCHANGE_MARGIN_BASELINE_DDL = """
CREATE TABLE {name} (
    exchange_code TEXT NOT NULL,
    short_name TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    strike_price REAL NOT NULL,
    option_type TEXT NOT NULL,
    margin_per_lot REAL NOT NULL,
    lot_size INTEGER,
    risk_array TEXT,
    settle_price REAL,
    source_file TEXT NOT NULL,
    source_date TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (exchange_code, short_name, expiry_date, strike_price, option_type)
)
"""


def _baseline_strike_column_is_integer(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(exchange_margin_baseline)").fetchall()
    if not rows:
        return False
    for _cid, name, col_type, *_rest in rows:
        if str(name) == "strike_price":
            return str(col_type or "").upper() == "INTEGER"
    return False


_LEGACY_BASELINE_DB_COLUMNS = (
    "exchange_code",
    "short_name",
    "expiry_date",
    "strike_price",
    "option_type",
    "margin_per_lot",
    "lot_size",
    "source_file",
    "source_date",
    "source_version",
    "refreshed_at",
)


def _migrate_exchange_margin_baseline_strike_to_real(conn: sqlite3.Connection) -> None:
    if not _baseline_strike_column_is_integer(conn):
        return
    _logger.info("Migrating exchange_margin_baseline.strike_price from INTEGER to REAL")
    conn.execute("ALTER TABLE exchange_margin_baseline RENAME TO exchange_margin_baseline_legacy")
    conn.execute(_EXCHANGE_MARGIN_BASELINE_DDL.format(name="exchange_margin_baseline"))
    legacy_cols = ", ".join(_LEGACY_BASELINE_DB_COLUMNS)
    new_cols = ", ".join(_LEGACY_BASELINE_DB_COLUMNS + ("risk_array",))
    conn.execute(
        f"""
        INSERT INTO exchange_margin_baseline ({new_cols})
        SELECT {legacy_cols}, NULL
        FROM exchange_margin_baseline_legacy
        """
    )
    conn.execute("DROP TABLE exchange_margin_baseline_legacy")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_exchange_margin_baseline_source
        ON exchange_margin_baseline(source_date, source_version)
        """
    )
    conn.commit()
    _logger.info("exchange_margin_baseline strike_price migration complete")


def _baseline_has_risk_array_column(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(exchange_margin_baseline)").fetchall()
    return any(str(name) == "risk_array" for _cid, name, *_rest in rows)


def _migrate_exchange_margin_baseline_add_risk_array(conn: sqlite3.Connection) -> None:
    if _baseline_has_risk_array_column(conn):
        return
    _logger.info("Adding exchange_margin_baseline.risk_array column")
    conn.execute("ALTER TABLE exchange_margin_baseline ADD COLUMN risk_array TEXT")
    conn.commit()


def _baseline_has_column(conn: sqlite3.Connection, column: str) -> bool:
    rows = conn.execute("PRAGMA table_info(exchange_margin_baseline)").fetchall()
    return any(str(name) == column for _cid, name, *_rest in rows)


def _migrate_exchange_margin_baseline_add_settle_price(conn: sqlite3.Connection) -> None:
    """The SPAN file's own settlement premium per option (`<p>`).

    Net Option Value is a premium sum, and the exchange states the premium it used. Pricing it
    with Black-Scholes at an assumed vol -- which is what this app did before the column
    existed -- introduces an error the file was never missing.
    """
    if _baseline_has_column(conn, "settle_price"):
        return
    _logger.info("Adding exchange_margin_baseline.settle_price column")
    conn.execute("ALTER TABLE exchange_margin_baseline ADD COLUMN settle_price REAL")
    conn.commit()


_EXCHANGE_MARGIN_UNDERLYING_DDL = """
CREATE TABLE IF NOT EXISTS exchange_margin_underlying (
    exchange_code TEXT NOT NULL,
    short_name TEXT NOT NULL,
    spot_price REAL,
    som_rate REAL,
    price_scan REAL,
    source_file TEXT NOT NULL,
    source_date TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (exchange_code, short_name)
)
"""


def ensure_exchange_margin_underlying_table() -> None:
    """Per-underlying SPAN facts that do not belong on a contract row.

    Spot comes from the `phyPf` portfolio and the short-option-minimum rate from the combined
    commodity's `somTiers`; both are stated once per underlying, not per strike. Spot is what
    exposure margin is a percentage of, so without it ELM has to be reconstructed from a live
    quote the SPAN file already answered.
    """
    with _scrip_conn() as conn:
        conn.execute(_EXCHANGE_MARGIN_UNDERLYING_DDL)
        conn.commit()


def ensure_exchange_margin_baseline_table() -> None:
    with _scrip_conn() as conn:
        conn.execute(_EXCHANGE_MARGIN_BASELINE_DDL.format(name="IF NOT EXISTS exchange_margin_baseline"))
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_exchange_margin_baseline_source
            ON exchange_margin_baseline(source_date, source_version)
            """
        )
        conn.commit()
        _migrate_exchange_margin_baseline_strike_to_real(conn)
        _migrate_exchange_margin_baseline_add_risk_array(conn)
        _migrate_exchange_margin_baseline_add_settle_price(conn)
        conn.execute(_EXCHANGE_MARGIN_UNDERLYING_DDL)
        conn.commit()


def _scrip_conn() -> sqlite3.Connection:
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _underlying_lookup_names(short_name: Any, exchange_ticker: Any = None) -> set[str]:
    """Every name one underlying can be addressed by across scrip master and SPAN.

    `ShortName` is ICICI's code, `ExchangeCode` the exchange symbol a SPAN pfCode uses, and the
    alias table bridges the indices where the two share nothing.
    """
    names = {str(short_name or "").strip().upper(), str(exchange_ticker or "").strip().upper()}
    names.update(aliases_for(short_name))
    names.discard("")
    return names


def _ymd_to_display(ymd: str) -> str:
    if len(ymd) != 8 or not ymd.isdigit() or ymd == "00000000":
        return ""
    try:
        d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    except ValueError:
        return ""
    return d.strftime("%d-%b-%Y")


def _default_source_date_ymd() -> str:
    return dt.datetime.now(dt.timezone.utc).date().strftime("%Y%m%d")


def _sniff_span_created_ymd(xml_head: bytes, fallback: str) -> str:
    text = xml_head.decode("utf-8", errors="replace")
    m = re.search(r"<created>\s*(\d{8})", text)
    if m:
        return m.group(1)
    return fallback


def _span_xml_members_from_zip(payload: bytes) -> tuple[str, io.BytesIO] | None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        return None
    with zf:
        names = zf.namelist()
        xml_names = [n for n in names if n.lower().endswith(".xml") or n.lower().endswith(".spn")]
        if not xml_names:
            first_name = names[0] if names else ""
            if first_name:
                try:
                    with zf.open(first_name) as f0:
                        first_prefix = f0.read(120).decode("utf-8", errors="replace")
                except Exception:
                    first_prefix = ""
                if first_prefix.lstrip().startswith("<?xml"):
                    xml_names = [first_name]
        if not xml_names:
            return None
        inner = xml_names[0]
        return inner, io.BytesIO(zf.read(inner))


def open_span_xml_payload(
    payload: bytes, logical_name: str
) -> tuple[io.BytesIO, str, str] | None:
    """Return (stream, display_source_file, inner_member_name or '')."""
    lowered = logical_name.lower()
    if lowered.endswith(".zip"):
        members = _span_xml_members_from_zip(payload)
        if not members:
            return None
        inner_name, bio = members
        return bio, f"{logical_name}:{inner_name}", inner_name
    if lowered.endswith((".xml", ".spn")):
        return io.BytesIO(payload), logical_name, ""
    head = payload[:4096].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<spanFile"):
        return io.BytesIO(payload), logical_name, ""
    members = _span_xml_members_from_zip(payload)
    if members:
        inner_name, bio = members
        return bio, f"{logical_name}:{inner_name}", inner_name
    return None


# How many distinct source dates of raw SPAN archives to keep on disk, and within each date only the
# latest revision per exchange (a new revision replaces the one before it). The files are only
# needed to re-run a margin comparison against the snapshot a figure came from; they are
# rebuildable from the exchange, so this is disposable state on the same volume as the databases.
# Keeping every revision of five dates cost ~400 MB of a 16 GiB volume (decided 2026-09-25).
SPAN_ARCHIVE_RETAIN_DATES = 2


def span_archive_dir() -> str:
    """Directory holding retained raw SPAN archives, one sub-directory per source date."""
    return os.path.join(cfg.DATA_PATH, "span")


def _archive_family(archive_name: str) -> str:
    """The leading non-numeric part of an archive name -- `nsccl.` / `BSERISK`.

    Distinguishes the NSE and BSE archives for one date without hard-coding either filename, so a
    same-day fallback never hands an NFO case the BSE file.
    """
    name = os.path.basename(str(archive_name or "").strip())
    for idx, ch in enumerate(name):
        if ch.isdigit():
            return name[:idx]
    return name


def find_span_archive(source_date: str, archive_name: str | None = None) -> tuple[str, bool] | None:
    """(path, is_exact) for a retained archive of `source_date`, or None if none was kept.

    An exact `archive_name` match wins. Failing that, another revision of the *same* exchange's file
    for that date is returned with `is_exact=False` -- SPAN is revised six times a day and the
    revisions differ by a percent or two, so a caller comparing figures has to be told it is looking
    at a neighbouring snapshot rather than the one a number actually came from.
    """
    day_dir = os.path.join(span_archive_dir(), str(source_date or "").strip())
    if not os.path.isdir(day_dir):
        return None
    try:
        names = sorted(n for n in os.listdir(day_dir) if os.path.isfile(os.path.join(day_dir, n)))
    except OSError:
        return None
    if not names:
        return None
    if archive_name:
        if archive_name in names:
            return os.path.join(day_dir, archive_name), True
        family = _archive_family(archive_name)
        for name in names:
            if family and _archive_family(name) == family:
                return os.path.join(day_dir, name), False
        return None
    return os.path.join(day_dir, names[0]), False


def _purge_span_archives(keep_dates: int = SPAN_ARCHIVE_RETAIN_DATES) -> None:
    root = span_archive_dir()
    if not os.path.isdir(root):
        return
    try:
        dates = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))), reverse=True)
    except OSError:
        return
    for stale in dates[keep_dates:]:
        shutil.rmtree(os.path.join(root, stale), ignore_errors=True)
    for kept in dates[:keep_dates]:
        _keep_latest_revision_per_exchange(os.path.join(root, kept))


def _keep_latest_revision_per_exchange(day_dir: str) -> None:
    """Delete every archive in `day_dir` but the most recently retained one of each exchange.

    Most recently *retained*, not the highest-named: that is the snapshot the live baseline was
    built from, and a revision name's ordering is the exchange's convention, not ours."""
    try:
        names = [n for n in os.listdir(day_dir) if os.path.isfile(os.path.join(day_dir, n))]
    except OSError:
        return
    newest: dict[str, tuple[float, str]] = {}
    for name in names:
        try:
            mtime = os.path.getmtime(os.path.join(day_dir, name))
        except OSError:
            continue
        family = _archive_family(name)
        if family not in newest or (mtime, name) > newest[family]:
            newest[family] = (mtime, name)
    keep = {name for _mtime, name in newest.values()}
    for name in names:
        if name not in keep:
            try:
                os.remove(os.path.join(day_dir, name))
            except OSError:
                _logger.warning("Could not remove superseded SPAN archive %s", name)


def retain_span_archive(payload: bytes, *, source_date: str, archive_name: str) -> str | None:
    """Keep the raw archive exactly as downloaded, so a margin run can be reproduced against the
    snapshot it actually used. Stores the original ZIP (a few MB) rather than the ~48MB XML.

    Best-effort: a failure here must never fail a baseline refresh, which is the real work.
    """
    date_key = str(source_date or "").strip()
    name = os.path.basename(str(archive_name or "").strip())
    if not date_key or not name or not payload:
        return None
    try:
        day_dir = os.path.join(span_archive_dir(), date_key)
        os.makedirs(day_dir, exist_ok=True)
        path = os.path.join(day_dir, name)
        tmp = f"{path}.part"
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
        family = _archive_family(name)
        for other in os.listdir(day_dir):
            if other != name and _archive_family(other) == family:
                os.remove(os.path.join(day_dir, other))
        _purge_span_archives()
        return path
    except OSError as exc:
        _logger.warning("Could not retain SPAN archive %s: %s", name, exc)
        return None


def _safe_float_text(raw: Any) -> float | None:
    try:
        return float(str(raw or "").strip())
    except (TypeError, ValueError):
        return None


def _underlying_key(exchange_code: str, pf_code: str) -> str:
    """Map a SPAN portfolio/commodity code onto the short name the baseline is keyed by."""
    if exchange_code == cfg.BFO:
        return BSE_SPAN_UNDERLYING_TO_SHORT_NAME.get(pf_code, pf_code)
    return pf_code


def _replace_underlying_facts(
    conn: sqlite3.Connection,
    *,
    exchange_code: str,
    source_file: str,
    source_date: str,
    spot_by_underlying: dict[str, float],
    scan_by_underlying: dict[str, float],
    som_by_underlying: dict[str, float],
    keep_only: set[str] | None,
) -> int:
    conn.execute("DELETE FROM exchange_margin_underlying WHERE exchange_code = ?", (exchange_code,))
    names = set(spot_by_underlying) | set(scan_by_underlying) | set(som_by_underlying)
    if keep_only is not None:
        names &= keep_only
    # Servers run in UTC; a naive datetime.now() here rendered ~5.5h behind the IST-stamped
    # ingest-history row for the same refresh. Store the IST wall-clock like every other
    # timestamp column so the two tally.
    now = ist_timestamp()
    rows = [
        (
            exchange_code,
            name,
            spot_by_underlying.get(name),
            som_by_underlying.get(name),
            scan_by_underlying.get(name),
            source_file,
            source_date,
            now,
        )
        for name in sorted(names)
    ]
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO exchange_margin_underlying (
                exchange_code, short_name, spot_price, som_rate, price_scan,
                source_file, source_date, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def get_underlying_facts(exchange_code: str, short_name: str) -> dict[str, Any] | None:
    """Spot, short-option-minimum rate and price-scan range for one underlying."""
    try:
        with _scrip_conn() as conn:
            row = conn.execute(
                """
                SELECT spot_price, som_rate, price_scan, source_file, source_date
                FROM exchange_margin_underlying
                WHERE exchange_code = ? AND short_name = ?
                """,
                (exchange_code.upper(), short_name.upper()),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {
        "spot_price": row[0],
        "som_rate": row[1],
        "price_scan": row[2],
        "source_file": row[3],
        "source_date": row[4],
    }


def _ingest_span_xml_stream(
    conn: sqlite3.Connection,
    fh,
    *,
    exchange_code: str,
    source_file: str,
    source_date: str,
    source_version: int,
    allowed_pf_codes: frozenset[str] | None,
) -> tuple[int, int]:
    """Replace all baseline rows for ``exchange_code`` from one SPAN XML stream."""
    conn.execute("DELETE FROM exchange_margin_baseline WHERE exchange_code = ?", (exchange_code,))
    # ExchangeCode is written by every scrip-master load, but guard anyway: losing the column
    # must degrade to the old ShortName-only join, not fail the whole ingest.
    scrip_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(scrip_master)")}
    ticker_expr = "ExchangeCode" if "ExchangeCode" in scrip_columns else "NULL"
    lot_rows = conn.execute(
        f"""
        SELECT ShortName, {ticker_expr}, ExpiryDate, StrikePrice, OptionType, LotSize
        FROM scrip_master
        """
    ).fetchall()
    # A SPAN pfCode is the exchange's own symbol for the underlying (NSE: HDFCBANK, ADANIENT),
    # which is `scrip_master.ExchangeCode` -- not `ShortName`, ICICI's private code (HDFBAN,
    # ADAENT). Index pfCodes match neither (BANKNIFTY vs CNXBAN vs "NIFTY BANK"), so the alias
    # table covers those. Registering every name a row can be reached by is what lets this join
    # hit for stocks at all; keyed on ShortName alone it only ever matched the ~28 underlyings
    # whose two codes happen to be identical, and every other contract was stored lot-less and
    # therefore unusable downstream.
    lot_by_contract: dict[tuple[str, str, Strike, str], int] = {}
    for sn, ex, ed, sp, ot, ls in lot_rows:
        try:
            strike_f = parse_strike(sp)
            if strike_f is None:
                continue
            lot_size = int(ls)
            expiry = str(ed).strip()
            option_type = str(ot).strip().upper()
            for name in _underlying_lookup_names(sn, ex):
                lot_by_contract[(name, expiry, strike_f, option_type)] = lot_size
        except Exception:
            continue
    current_underlying = ""
    current_series_expiry = ""
    inside_oop = False
    inserted = 0
    skipped = 0
    batch_rows: list[tuple] = []
    matched_lot = 0
    missing_lot = 0
    # Underlying-level facts, collected in the same pass. `phyPf` (spot, price-scan range) sits
    # near the top of the file and `ccDef` (short-option-minimum rate) at the very bottom, so
    # both are free here and would each cost a second 50 MB parse if collected separately.
    spot_by_underlying: dict[str, float] = {}
    scan_by_underlying: dict[str, float] = {}
    som_by_underlying: dict[str, float] = {}
    inside_phy_pf = False
    phy_pf_code = ""
    inside_cc_def = False
    cc_code = ""
    inside_som_tiers = False
    for event, elem in ET.iterparse(fh, events=("start", "end")):
        tag = elem.tag
        if event == "start" and tag == "phyPf":
            inside_phy_pf = True
            phy_pf_code = ""
            continue
        if event == "start" and tag == "ccDef":
            inside_cc_def = True
            cc_code = ""
            continue
        if event == "start" and tag == "somTiers":
            inside_som_tiers = True
            continue
        if inside_phy_pf:
            # `<pfCode>` recurs inside nested `undPf`/`pfLink` blocks; the portfolio's own code
            # is the first one, so only take it while the slot is still empty.
            if event == "end" and tag == "pfCode" and not phy_pf_code:
                phy_pf_code = _underlying_key(exchange_code, (elem.text or "").strip().upper())
                elem.clear()
                continue
            if event == "end" and tag == "p" and phy_pf_code and phy_pf_code not in spot_by_underlying:
                spot = _safe_float_text(elem.text)
                if spot is not None and spot > 0:
                    spot_by_underlying[phy_pf_code] = spot
                elem.clear()
                continue
            if event == "end" and tag == "priceScan" and phy_pf_code and phy_pf_code not in scan_by_underlying:
                scan = _safe_float_text(elem.text)
                if scan is not None and scan > 0:
                    scan_by_underlying[phy_pf_code] = scan
                elem.clear()
                continue
            if event == "end" and tag == "phyPf":
                inside_phy_pf = False
                phy_pf_code = ""
                elem.clear()
                continue
        if inside_cc_def:
            # `<cc>` recurs in every `dSpread`'s `pLeg`; the commodity's own code comes first.
            if event == "end" and tag == "cc" and not cc_code:
                cc_code = _underlying_key(exchange_code, (elem.text or "").strip().upper())
                elem.clear()
                continue
            if event == "end" and tag == "val" and inside_som_tiers and cc_code:
                rate = _safe_float_text(elem.text)
                if rate is not None:
                    som_by_underlying[cc_code] = max(som_by_underlying.get(cc_code, 0.0), rate)
                elem.clear()
                continue
            if event == "end" and tag == "somTiers":
                inside_som_tiers = False
                elem.clear()
                continue
            if event == "end" and tag == "ccDef":
                inside_cc_def = False
                cc_code = ""
                elem.clear()
                continue
        if event == "start" and tag == "oopPf":
            inside_oop = True
            current_underlying = ""
            continue
        if event == "end" and tag == "pfCode" and inside_oop and not current_underlying:
            current_underlying = (elem.text or "").strip().upper()
            elem.clear()
            continue
        if event == "end" and tag == "pe":
            pe = (elem.text or "").strip()
            current_series_expiry = _ymd_to_display(pe) if len(pe) == 8 and pe.isdigit() else ""
            elem.clear()
            continue
        if event == "end" and tag == "opt":
            try:
                short_name = current_underlying
                if not short_name:
                    skipped += 1
                    elem.clear()
                    continue
                if allowed_pf_codes is not None and short_name not in allowed_pf_codes:
                    skipped += 1
                    elem.clear()
                    continue
                if allowed_pf_codes is not None:
                    short_name = BSE_SPAN_PF_CODE_TO_SHORT_NAME.get(short_name, short_name)
                o = (elem.findtext("o") or "").strip().upper()
                option_type = "CE" if o == "C" else "PE" if o == "P" else ""
                if not option_type or not current_series_expiry:
                    skipped += 1
                    elem.clear()
                    continue
                strike_raw = (elem.findtext("k") or "").strip()
                strike_price = parse_strike(strike_raw)
                if strike_price is None:
                    skipped += 1
                    elem.clear()
                    continue
                ra = elem.find("ra")
                if ra is None:
                    skipped += 1
                    elem.clear()
                    continue
                a_vals = []
                for a in ra.findall("a"):
                    try:
                        a_vals.append(float((a.text or "0").strip()))
                    except ValueError:
                        continue
                if not a_vals:
                    skipped += 1
                    elem.clear()
                    continue
                worst = min(a_vals)
                per_unit = max(0.0, -worst)
                lot_size = lot_by_contract.get((short_name, current_series_expiry, strike_price, option_type))
                lot_size = int(lot_size) if lot_size is not None else None
                if lot_size and lot_size > 0:
                    matched_lot += 1
                else:
                    missing_lot += 1
                margin_per_lot = per_unit * (lot_size if lot_size and lot_size > 0 else 1)
                risk_array_json = json.dumps(a_vals)
                # The exchange's own settlement premium for this option. Net Option Value is a
                # sum of these; deriving it from a pricing model instead is avoidable error.
                settle_price = _safe_float_text(elem.findtext("p"))
                batch_rows.append(
                    (
                        exchange_code,
                        short_name,
                        current_series_expiry,
                        strike_price,
                        option_type,
                        float(margin_per_lot),
                        lot_size,
                        risk_array_json,
                        settle_price,
                        source_file,
                        source_date,
                        int(source_version),
                        ist_timestamp(),
                    )
                )
                inserted += 1
                if len(batch_rows) >= 5000:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO exchange_margin_baseline (
                            exchange_code, short_name, expiry_date, strike_price, option_type, margin_per_lot,
                            lot_size, risk_array, settle_price, source_file, source_date, source_version,
                            refreshed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        batch_rows,
                    )
                    conn.commit()
                    batch_rows.clear()
            except Exception:
                skipped += 1
            elem.clear()
            continue
        if event == "end" and tag == "series":
            current_series_expiry = ""
            elem.clear()
            continue
        if event == "end" and tag == "oopPf":
            inside_oop = False
            current_underlying = ""
            current_series_expiry = ""
            elem.clear()
            continue
    if batch_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO exchange_margin_baseline (
                exchange_code, short_name, expiry_date, strike_price, option_type, margin_per_lot,
                lot_size, risk_array, settle_price, source_file, source_date, source_version,
                refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch_rows,
        )
    # For BSE only the two index portfolios are ingested, so the underlying table is scoped to
    # match -- carrying spot for ~590 underlyings whose contracts were skipped would invite a
    # lookup that silently succeeds against a baseline that has no rows.
    keep_only = (
        {BSE_SPAN_PF_CODE_TO_SHORT_NAME[c] for c in allowed_pf_codes if c in BSE_SPAN_PF_CODE_TO_SHORT_NAME}
        if allowed_pf_codes is not None
        else None
    )
    underlying_rows = _replace_underlying_facts(
        conn,
        exchange_code=exchange_code,
        source_file=source_file,
        source_date=source_date,
        spot_by_underlying=spot_by_underlying,
        scan_by_underlying=scan_by_underlying,
        som_by_underlying=som_by_underlying,
        keep_only=keep_only,
    )
    conn.commit()
    _logger.info(
        "Exchange baseline ingested %s %s: inserted=%s skipped=%s matched_lot=%s missing_lot=%s "
        "underlyings=%s",
        exchange_code,
        source_file,
        inserted,
        skipped,
        matched_lot,
        missing_lot,
        underlying_rows,
    )
    return inserted, skipped


def _baseline_db_healthy() -> str:
    """Empty string when the scrip DB is usable, else the reason it is not."""
    try:
        ensure_exchange_margin_baseline_table()
    except Exception as e:
        return f"Baseline table init failed: {e}"
    try:
        with _scrip_conn() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            quick_check = str(row[0]) if row and row[0] is not None else "unknown"
    except Exception as e:
        return f"Scrip DB check failed: {e}"
    if quick_check.lower() != "ok":
        return (
            "scrips.sqlite3 is corrupted (quick_check failed). "
            "Rebuild master data before refreshing baseline."
        )
    return ""


def current_baseline_archive_name(exchange_code: str) -> str:
    """Archive the rows currently in the baseline came from, or '' if there are none.

    ``source_file`` is stored as ``archive.zip:inner.xml``; the archive half alone identifies
    the publication (both exchanges encode the date and the intraday revision in the name).
    """
    try:
        with _scrip_conn() as conn:
            row = conn.execute(
                "SELECT source_file FROM exchange_margin_baseline WHERE exchange_code = ? LIMIT 1",
                (exchange_code.upper(),),
            ).fetchone()
    except sqlite3.Error:
        return ""
    if not row or not row[0]:
        return ""
    return str(row[0]).split(":", 1)[0].strip()


def refresh_span_baseline(market: str, *, force: bool = False) -> dict:
    """Download and ingest the newest published SPAN file for one market.

    Returns ``Status`` 200 with ``Success.skipped`` set when the newest published archive is
    already the one loaded -- the common outcome for a scheduled slot, and the reason a slot
    that fires a few seconds before the exchange stamps its file is harmless rather than
    wasteful.
    """
    market_l = (market or "").strip().lower()
    if market_l not in (span_sources.MARKET_NSE, span_sources.MARKET_BSE):
        return {"Status": 400, "Error": "market must be nse or bse", "Success": None}
    problem = _baseline_db_healthy()
    if problem:
        return {"Status": 400, "Error": problem, "Success": None}

    ref = span_sources.resolve_latest_span_archive(market_l)
    if not ref:
        return {
            "Status": 400,
            "Error": f"Could not find a published {market_l.upper()} SPAN archive in recent days.",
            "Success": None,
        }

    if not force and current_baseline_archive_name(ref.exchange_code) == ref.archive_name:
        return {
            "Status": 200,
            "Error": "",
            "Success": {
                "market": market_l,
                "skipped": True,
                "source_file": ref.archive_name,
                "source_date": ref.source_date,
                "source_version": ref.source_version,
                "source_url": ref.url,
                "inserted_rows": 0,
                "skipped_rows": 0,
            },
        }

    payload = span_sources.download_span_archive(ref)
    if not payload:
        return {
            "Status": 400,
            "Error": f"Download failed for {ref.archive_name}.",
            "Success": None,
        }
    retain_span_archive(payload, source_date=ref.source_date, archive_name=ref.archive_name)
    opened = open_span_xml_payload(payload, ref.archive_name)
    if not opened:
        return {
            "Status": 400,
            "Error": f"Could not read SPAN XML from {ref.archive_name} (ZIP/XML expected).",
            "Success": None,
        }
    stream, display_file, _inner = opened
    allowed = None if market_l == span_sources.MARKET_NSE else BSE_BASELINE_PF_CODES

    try:
        with _scrip_conn() as conn:
            inserted, skipped = _ingest_span_xml_stream(
                conn,
                stream,
                exchange_code=ref.exchange_code,
                source_file=display_file[:512],
                source_date=ref.source_date,
                source_version=int(ref.source_version),
                allowed_pf_codes=allowed,
            )
    except Exception as e:
        return {"Status": 400, "Error": f"Baseline refresh failed: {e}", "Success": None}

    if inserted == 0:
        return {
            "Status": 400,
            "Error": f"No option margin rows were ingested from {ref.archive_name}.",
            "Success": None,
        }

    _logger.info(
        "Exchange baseline refreshed from %s (%s %s): inserted=%s skipped=%s",
        display_file,
        market_l.upper(),
        ref.label or ref.source_version,
        inserted,
        skipped,
    )
    _publish_span_baseline_to_redis()
    return {
        "Status": 200,
        "Error": "",
        "Success": {
            "market": market_l,
            "skipped": False,
            "source_file": display_file,
            "source_date": ref.source_date,
            "source_version": ref.source_version,
            "source_url": ref.url,
            "source_label": ref.label,
            "exchange_code": ref.exchange_code,
            "inserted_rows": inserted,
            "skipped_rows": skipped,
        },
    }


def refresh_all_span_baselines(*, force: bool = False) -> dict[str, dict]:
    """Refresh both markets. One market failing does not stop the other."""
    return {
        market: refresh_span_baseline(market, force=force)
        for market in (span_sources.MARKET_NSE, span_sources.MARKET_BSE)
    }


def _publish_span_baseline_to_redis() -> None:
    try:
        from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
            publish_span_baseline_from_db,
        )

        publish_span_baseline_from_db()
    except Exception as exc:
        _logger.warning("SPAN baseline Redis publish failed: %s", exc)


def resolve_exchange_baseline_margin(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike_price: Strike,
    right: str,
    quantity: int,
) -> dict:
    try:
        from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
            resolve_margin_from_store,
        )

        out = resolve_margin_from_store(
            exchange_code=exchange_code,
            stock_code=stock_code,
            expiry_display=expiry_display,
            strike_price=strike_price,
            right=right,
            quantity=quantity,
        )
        if out.get("found"):
            return out
    except Exception:
        _logger.debug("SPAN baseline store lookup failed; falling back to SQLite", exc_info=True)

    option_type = "CE" if right == cfg.CALL else "PE"
    # Same bridge as the store: the row is keyed on the SPAN pfCode, not the caller's stock code.

    try:
        exchange_ticker = exchange_symbol_for(stock_code)
    except Exception:
        exchange_ticker = ""
    names = sorted(_underlying_lookup_names(stock_code, exchange_ticker))
    placeholders = ",".join("?" for _ in names)
    with _scrip_conn() as conn:
        row = conn.execute(
            f"""
            SELECT margin_per_lot, lot_size
            FROM exchange_margin_baseline
            WHERE exchange_code = ? AND short_name IN ({placeholders}) AND expiry_date = ?
              AND strike_price = ? AND option_type = ?
            LIMIT 1
            """,
            (exchange_code, *names, expiry_display, parse_strike(strike_price), option_type),
        ).fetchone()
    if not row:
        return {"found": False}
    margin_per_lot = float(row[0])
    lot_size = int(row[1]) if row[1] else None
    if not lot_size or lot_size <= 0:
        return {"found": False}
    lots = max(1, int(math.ceil(float(quantity) / float(lot_size))))
    return {
        "found": True,
        "span_margin_required": margin_per_lot * lots,
        "margin_per_lot": margin_per_lot,
        "lot_size": lot_size,
    }
