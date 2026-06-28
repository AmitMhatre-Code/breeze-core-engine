"""NSCCL SPAN baseline loader and lookup utilities."""
from __future__ import annotations

import datetime as dt
import io
import logging
import math
import re
import sqlite3
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike

_logger = logging.getLogger(__name__)

MARGIN_SOURCE_BREEZE = "breeze_api"
MARGIN_SOURCE_EXCHANGE = "exchange_baseline"

# BSE SPAN uses pfCode BSXOPT / BKXOPT; scrip_master / Strategy Builder use BSESEN and BANKEX on BFO (ICICI codes).
BSE_BASELINE_PF_CODES = frozenset({"BSXOPT", "BKXOPT"})
BSE_SPAN_PF_CODE_TO_SHORT_NAME = {"BSXOPT": "BSESEN", "BKXOPT": "BANKEX"}

_MAX_BASELINE_UPLOAD_BYTES = 120 * 1024 * 1024

_BASELINE_DB_COLUMNS = (
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

_EXCHANGE_MARGIN_BASELINE_DDL = """
CREATE TABLE {name} (
    exchange_code TEXT NOT NULL,
    short_name TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    strike_price REAL NOT NULL,
    option_type TEXT NOT NULL,
    margin_per_lot REAL NOT NULL,
    lot_size INTEGER,
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


def _migrate_exchange_margin_baseline_strike_to_real(conn: sqlite3.Connection) -> None:
    if not _baseline_strike_column_is_integer(conn):
        return
    _logger.info("Migrating exchange_margin_baseline.strike_price from INTEGER to REAL")
    conn.execute("ALTER TABLE exchange_margin_baseline RENAME TO exchange_margin_baseline_legacy")
    conn.execute(_EXCHANGE_MARGIN_BASELINE_DDL.format(name="exchange_margin_baseline"))
    conn.execute(
        f"""
        INSERT INTO exchange_margin_baseline ({", ".join(_BASELINE_DB_COLUMNS)})
        SELECT {", ".join(_BASELINE_DB_COLUMNS)}
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


def _scrip_conn() -> sqlite3.Connection:
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _ymd_to_display(ymd: str) -> str:
    if len(ymd) != 8 or not ymd.isdigit() or ymd == "00000000":
        return ""
    try:
        d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    except ValueError:
        return ""
    return d.strftime("%d-%b-%Y")


def _fetch_zip(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise


def _latest_archive(max_lookback_days: int = 14) -> tuple[str, str, int, bytes] | None:
    today = dt.datetime.now(dt.timezone.utc).date()
    for day_offset in range(max_lookback_days + 1):
        d = today - dt.timedelta(days=day_offset)
        ymd = d.strftime("%Y%m%d")
        for ver in (4, 3, 2, 1):
            filename = f"nsccl.{ymd}.i{ver}.zip"
            url = f"https://nsearchives.nseindia.com/archives/nsccl/span/{filename}"
            payload = _fetch_zip(url)
            if payload:
                return filename, ymd, ver, payload
    return None


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
    lot_rows = conn.execute(
        """
        SELECT ShortName, ExpiryDate, StrikePrice, OptionType, LotSize
        FROM scrip_master
        """
    ).fetchall()
    lot_by_contract: dict[tuple[str, str, Strike, str], int] = {}
    for sn, ed, sp, ot, ls in lot_rows:
        try:
            strike_f = parse_strike(sp)
            if strike_f is None:
                continue
            key = (str(sn).strip().upper(), str(ed).strip(), strike_f, str(ot).strip().upper())
            lot_by_contract[key] = int(ls)
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
    for event, elem in ET.iterparse(fh, events=("start", "end")):
        tag = elem.tag
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
                batch_rows.append(
                    (
                        exchange_code,
                        short_name,
                        current_series_expiry,
                        strike_price,
                        option_type,
                        float(margin_per_lot),
                        lot_size,
                        source_file,
                        source_date,
                        int(source_version),
                        dt.datetime.now().isoformat(timespec="seconds"),
                    )
                )
                inserted += 1
                if len(batch_rows) >= 5000:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO exchange_margin_baseline (
                            exchange_code, short_name, expiry_date, strike_price, option_type, margin_per_lot,
                            lot_size, source_file, source_date, source_version, refreshed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                lot_size, source_file, source_date, source_version, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch_rows,
        )
    conn.commit()
    _logger.info(
        "Exchange baseline ingested %s %s: inserted=%s skipped=%s matched_lot=%s missing_lot=%s",
        exchange_code,
        source_file,
        inserted,
        skipped,
        matched_lot,
        missing_lot,
    )
    return inserted, skipped


def ingest_exchange_baseline_upload(
    payload: bytes,
    original_filename: str,
    *,
    market: str,
) -> dict:
    """Load SPAN XML (or ZIP containing XML) from user upload. ``market`` is ``nse`` or ``bse``."""
    market_l = (market or "").strip().lower()
    if market_l not in ("nse", "bse"):
        return {"Status": 400, "Error": "market must be nse or bse", "Success": None}
    if len(payload) > _MAX_BASELINE_UPLOAD_BYTES:
        return {"Status": 400, "Error": "File too large (max 120MB).", "Success": None}
    try:
        ensure_exchange_margin_baseline_table()
    except Exception as e:
        return {"Status": 400, "Error": f"Baseline table init failed: {e}", "Success": None}
    try:
        with _scrip_conn() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            quick_check = str(row[0]) if row and row[0] is not None else "unknown"
        if quick_check.lower() != "ok":
            return {
                "Status": 400,
                "Error": "scrips.sqlite3 is corrupted (quick_check failed). Rebuild master data before uploading.",
                "Success": None,
            }
    except Exception as e:
        return {"Status": 400, "Error": f"Scrip DB check failed: {e}", "Success": None}

    base_name = (original_filename or "upload").rsplit("/", 1)[-1].strip() or "upload"
    opened = open_span_xml_payload(payload, base_name)
    if not opened:
        return {"Status": 400, "Error": "Could not read SPAN XML from file (ZIP/XML expected).", "Success": None}
    stream, source_file, _inner = opened
    head = stream.read(65536)
    stream.seek(0)
    source_date = _sniff_span_created_ymd(head, _default_source_date_ymd())
    exchange_code = cfg.NFO if market_l == "nse" else cfg.BFO
    allowed: frozenset[str] | None = None if market_l == "nse" else BSE_BASELINE_PF_CODES

    try:
        with _scrip_conn() as conn:
            inserted, skipped = _ingest_span_xml_stream(
                conn,
                stream,
                exchange_code=exchange_code,
                source_file=source_file[:512],
                source_date=source_date,
                source_version=1,
                allowed_pf_codes=allowed,
            )
    except Exception as e:
        return {"Status": 400, "Error": f"Baseline upload failed: {e}", "Success": None}

    if inserted == 0:
        return {
            "Status": 400,
            "Error": "No option margin rows were ingested. Check file format and (for BSE) that BSXOPT/BKXOPT portfolios are present.",
            "Success": None,
        }

    _publish_span_baseline_to_redis()

    return {
        "Status": 200,
        "Error": "",
        "Success": {
            "source_file": source_file,
            "source_date": source_date,
            "source_version": 1,
            "exchange_code": exchange_code,
            "inserted_rows": inserted,
            "skipped_rows": skipped,
            "market": market_l,
        },
    }


def refresh_exchange_risk_baseline() -> dict:
    try:
        ensure_exchange_margin_baseline_table()
    except Exception as e:
        return {"Status": 400, "Error": f"Baseline table init failed: {e}", "Success": None}
    try:
        with _scrip_conn() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            quick_check = str(row[0]) if row and row[0] is not None else "unknown"
        if quick_check.lower() != "ok":
            return {
                "Status": 400,
                "Error": "scrips.sqlite3 is corrupted (quick_check failed). Rebuild master data before refreshing baseline.",
                "Success": None,
            }
    except Exception as e:
        return {"Status": 400, "Error": f"Scrip DB check failed: {e}", "Success": None}
    latest = _latest_archive()
    if not latest:
        return {
            "Status": 400,
            "Error": "Could not find NSCCL SPAN archive in recent days.",
            "Success": None,
        }
    archive_name, source_date, source_version, payload = latest
    display_file = archive_name
    inserted = skipped = 0
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = zf.namelist()
            xml_names = [
                n for n in zf.namelist() if n.lower().endswith(".xml") or n.lower().endswith(".spn")
            ]
            if not xml_names:
                first_name = names[0] if names else ""
                first_prefix = ""
                if first_name:
                    try:
                        with zf.open(first_name) as f0:
                            first_prefix = f0.read(120).decode("utf-8", errors="replace")
                    except Exception:
                        first_prefix = ""
                if first_name and first_prefix.lstrip().startswith("<?xml"):
                    xml_names = [first_name]
                else:
                    return {"Status": 400, "Error": "SPAN ZIP does not contain XML/SPN.", "Success": None}
            xml_name = xml_names[0]
            display_file = f"{archive_name}:{xml_name}"
            with zf.open(xml_name) as fh, _scrip_conn() as conn:
                inserted, skipped = _ingest_span_xml_stream(
                    conn,
                    fh,
                    exchange_code=cfg.NFO,
                    source_file=display_file[:512],
                    source_date=source_date,
                    source_version=int(source_version),
                    allowed_pf_codes=None,
                )
    except Exception as e:
        return {"Status": 400, "Error": f"Baseline refresh failed: {e}", "Success": None}
    _logger.info(
        "Exchange baseline refreshed from %s: inserted=%s skipped=%s",
        display_file,
        inserted,
        skipped,
    )
    _publish_span_baseline_to_redis()
    return {
        "Status": 200,
        "Error": "",
        "Success": {
            "source_file": display_file,
            "source_date": source_date,
            "source_version": source_version,
            "inserted_rows": inserted,
            "skipped_rows": skipped,
        },
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
    with _scrip_conn() as conn:
        row = conn.execute(
            """
            SELECT margin_per_lot, lot_size
            FROM exchange_margin_baseline
            WHERE exchange_code = ? AND short_name = ? AND expiry_date = ?
              AND strike_price = ? AND option_type = ?
            LIMIT 1
            """,
            (exchange_code, stock_code, expiry_display, parse_strike(strike_price), option_type),
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
