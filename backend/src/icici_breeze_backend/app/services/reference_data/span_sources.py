"""Locate and download the newest published NSE and BSE SPAN risk-parameter archives.

Both exchanges republish the risk file several times a session. This module only resolves
*which* archive is newest and fetches its bytes; parsing and ingestion stay in
``nsccl_baseline``. Resolution is deliberately separate from download so a scheduled slot can
compare the newest archive against the one already ingested and skip an 9 MB transfer it does
not need.

NSE is a plain archive directory: ``nsccl.{yyyymmdd}.i{n}.zip``, ``n`` counting up through the
day (i1 lands the previous evening, i5 around 15:30 IST).

BSE has no such directory. Its risk-parameter page is an Angular app -- the date dropdowns and
the file-mode radio buttons are client-side, and the HTML holds no form to post -- so the
selections a user makes there are reproduced here as the two JSON calls the page itself makes.
``LoadData`` returns one row per file mode (B, I, J, K, L, Z, oldest to newest) with a
``File_Path`` on ``notices.bseindia.com``, which does not resolve publicly; the page rewrites
it to ``www.bseindia.com/bsedata/`` before downloading, and so do we.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import requests

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import (
    BSE_HTTP_HEADERS,
    NSE_ARCHIVES_HTTP_HEADERS,
    request_timeout,
)

_logger = logging.getLogger(__name__)

MARKET_NSE = "nse"
MARKET_BSE = "bse"

# BSE file modes, oldest to newest within a day. The labels are the page's own.
_BSE_MODE_ORDER: dict[str, int] = {"B": 0, "I": 1, "J": 2, "K": 3, "L": 4, "Z": 5}
_BSE_MODE_LABELS: dict[str, str] = {
    "B": "Beginning of the Day",
    "I": "Intra-Day 01",
    "J": "Intra-Day 02",
    "K": "Intra-Day 03",
    "L": "Intra-Day 04",
    "Z": "Final",
}
_BSE_XML_FLAG = 0  # flag=1 is the binary PC-SPAN set, which this app cannot parse.


@dataclass(frozen=True)
class SpanArchiveRef:
    """A published SPAN archive, identified without having downloaded it."""

    market: str
    exchange_code: str
    archive_name: str
    url: str
    source_date: str  # YYYYMMDD
    source_version: int  # NSE i-version; BSE file-mode ordinal
    label: str = ""


def _bse_headers() -> dict[str, str]:
    headers = dict(BSE_HTTP_HEADERS)
    headers["Accept"] = "application/json,text/plain,*/*"
    return headers


def _market_headers(market: str) -> dict[str, str]:
    return dict(NSE_ARCHIVES_HTTP_HEADERS) if market == MARKET_NSE else dict(BSE_HTTP_HEADERS)


def _url_exists(url: str, market: str) -> bool:
    """Probe without paying for the body.

    Streamed, so the status line is available before any of the ~9 MB payload is read, and the
    connection is dropped straight after. HEAD is not usable: bseindia answers HEAD with 404
    for files it serves happily on GET.
    """
    try:
        resp = requests.get(
            url,
            headers=_market_headers(market),
            timeout=request_timeout(),
            stream=True,
        )
    except requests.RequestException as exc:
        _logger.debug("SPAN archive probe failed for %s: %s", url, exc)
        return False
    try:
        return resp.status_code == 200
    finally:
        resp.close()


def download_span_archive(ref: SpanArchiveRef) -> bytes | None:
    try:
        resp = requests.get(ref.url, headers=_market_headers(ref.market), timeout=request_timeout())
        resp.raise_for_status()
    except requests.RequestException as exc:
        _logger.warning("SPAN archive download failed for %s: %s", ref.url, exc)
        return None
    return resp.content


def resolve_latest_nse_span_archive(*, lookback_days: int | None = None) -> SpanArchiveRef | None:
    """Newest ``nsccl.{yyyymmdd}.i{n}.zip``, walking days back and versions down."""
    lookback = max(1, int(lookback_days or cfg.REFERENCE_DATA_LOOKBACK_DAYS))
    max_version = max(1, int(cfg.NSE_SPAN_MAX_INTRADAY_VERSION))
    today = today_ist_date()
    for day_offset in range(lookback):
        day = today - dt.timedelta(days=day_offset)
        ymd = day.strftime("%Y%m%d")
        for version in range(max_version, 0, -1):
            url = cfg.NSE_SPAN_ARCHIVE_URL_TEMPLATE.format(yyyymmdd=ymd, version=version)
            if not _url_exists(url, MARKET_NSE):
                continue
            return SpanArchiveRef(
                market=MARKET_NSE,
                exchange_code=cfg.NFO,
                archive_name=url.rsplit("/", 1)[-1],
                url=url,
                source_date=ymd,
                source_version=version,
                label=f"Intra-Day {version:02d}",
            )
    return None


def _bse_max_date() -> dt.date | None:
    try:
        resp = requests.get(
            cfg.BSE_SPAN_MAXDATE_API_URL, headers=_bse_headers(), timeout=request_timeout()
        )
        resp.raise_for_status()
        table = (resp.json() or {}).get("Table") or []
    except (requests.RequestException, ValueError) as exc:
        _logger.debug("BSE SPAN max-date lookup failed: %s", exc)
        return None
    for row in table:
        raw = str((row or {}).get("MaxDT") or "").strip()
        try:
            return dt.datetime.strptime(raw, "%d/%m/%Y").date()
        except ValueError:
            continue
    return None


def _bse_file_url(file_path: str) -> str:
    path = str(file_path or "").strip()
    if not path:
        return ""
    if path.lower().startswith(cfg.BSE_SPAN_NOTICES_PREFIX.lower()):
        return cfg.BSE_SPAN_DOWNLOAD_PREFIX + path[len(cfg.BSE_SPAN_NOTICES_PREFIX) :]
    return path


def _bse_index_for_date(day: dt.date) -> list[dict[str, Any]]:
    """The rows behind the page's file-mode radio buttons for one date."""
    try:
        resp = requests.get(
            cfg.BSE_SPAN_INDEX_API_URL,
            params={"date": day.strftime("%Y%m%d"), "flag": _BSE_XML_FLAG},
            headers=_bse_headers(),
            timeout=request_timeout(),
        )
        resp.raise_for_status()
        table = (resp.json() or {}).get("Table") or []
    except (requests.RequestException, ValueError) as exc:
        _logger.debug("BSE SPAN index lookup failed for %s: %s", day.isoformat(), exc)
        return []
    return [row for row in table if isinstance(row, dict)]


def resolve_latest_bse_span_archive(*, lookback_days: int | None = None) -> SpanArchiveRef | None:
    """Newest BSE SPAN XML archive: latest published date, then highest file mode on it."""
    lookback = max(1, int(lookback_days or cfg.REFERENCE_DATA_LOOKBACK_DAYS))
    start = _bse_max_date() or today_ist_date()
    for day_offset in range(lookback):
        day = start - dt.timedelta(days=day_offset)
        candidates: list[tuple[int, str, str, str]] = []
        for row in _bse_index_for_date(day):
            mode = str(row.get("File_Mode") or "").strip().upper()
            if mode not in _BSE_MODE_ORDER:
                continue
            url = _bse_file_url(str(row.get("File_Path") or ""))
            if not url:
                continue
            candidates.append((_BSE_MODE_ORDER[mode], mode, url, url.rsplit("/", 1)[-1]))
        # Newest first, but the index lists a mode as soon as it is scheduled, so fall through
        # to the previous mode when the file itself is not up yet.
        for ordinal, mode, url, name in sorted(candidates, reverse=True):
            if not _url_exists(url, MARKET_BSE):
                continue
            return SpanArchiveRef(
                market=MARKET_BSE,
                exchange_code=cfg.BFO,
                archive_name=name,
                url=url,
                source_date=day.strftime("%Y%m%d"),
                source_version=ordinal,
                label=_BSE_MODE_LABELS.get(mode, mode),
            )
    return None


def resolve_latest_span_archive(market: str, *, lookback_days: int | None = None) -> SpanArchiveRef | None:
    if market == MARKET_NSE:
        return resolve_latest_nse_span_archive(lookback_days=lookback_days)
    if market == MARKET_BSE:
        return resolve_latest_bse_span_archive(lookback_days=lookback_days)
    return None
