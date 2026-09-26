"""Locate and download the newest published NSE and BSE SPAN risk-parameter archives.

Both exchanges republish the risk file several times a session. This module only resolves
*which* archive is newest and fetches its bytes; parsing and ingestion stay in
``nsccl_baseline``. Resolution is deliberately separate from download so a scheduled slot can
compare the newest archive against the one already ingested and skip an 9 MB transfer it does
not need.

NSE is a plain archive directory: ``nsccl.{yyyymmdd}.i{n}.zip``, ``n`` counting up through the
day (i1 lands the previous evening, i5 around 15:30 IST).

Both exchanges publish the *next* trading day's beginning-of-day file ahead of that day -- NSE's
``i1`` at about 21:30 IST the evening before, BSE's ``-00`` a little after midnight -- stamped
with the date it is for. ICICI's margin_calculator moves onto that file as soon as it exists, so
from then until the next session's first intraday file the newest file is future-dated. The
resolvers therefore probe the next trading day's beginning-of-day file before walking back from
today; without that, every weekend and every evening we priced a file behind ICICI.

BSE is resolved the same way, by probing file names:
``Risk_Automate/BSERISK{yyyymmdd}-{00..04|FINAL}.ZIP`` on ``www.bseindia.com/bsedata/``, one per
file mode (Beginning of Day, Intra-Day 01-04, Final). Until 2026-09-24 the names came from the
two JSON calls BSE's Angular risk-parameter page makes (``getmaxdate/w``, ``LoadData/w``); since
then ``api.bseindia.com`` answers every non-browser client with an Akamai 403, while the files
themselves still download. The names are fixed, so probing them needs nothing from the API.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

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

# BSE file modes, newest first: (ordinal, file-name suffix, label). Ordinals and labels are the
# ones BSE's page gave modes B, I, J, K, L, Z, so `source_version` and the ingest history read
# the same as before the API was blocked.
_BSE_MODES: tuple[tuple[int, str, str], ...] = (
    (5, "FINAL", "Final"),
    (4, "04", "Intra-Day 04"),
    (3, "03", "Intra-Day 03"),
    (2, "02", "Intra-Day 02"),
    (1, "01", "Intra-Day 01"),
    (0, "00", "Beginning of the Day"),
)


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


def _market_headers(market: str) -> dict[str, str]:
    return dict(NSE_ARCHIVES_HTTP_HEADERS) if market == MARKET_NSE else dict(BSE_HTTP_HEADERS)


def _url_exists(url: str, market: str) -> bool:
    """Probe without paying for the body.

    Streamed, so the status line is available before any of the ~9 MB payload is read, and the
    connection is dropped straight after. HEAD is not usable: bseindia answers HEAD with 404
    for files it serves happily on GET. An HTML body is never an archive: both hosts send their
    misses as HTML, and a bot-protection challenge served as 200 would be too.
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
        content_type = str(resp.headers.get("Content-Type") or "").lower()
        return resp.status_code == 200 and "text/html" not in content_type
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


def next_trading_day(today: dt.date) -> dt.date:
    """The first trading day after `today`, per the configured exchange calendar.

    Falls back to "next weekday" when the calendar cannot be read (no users DB yet), which is
    only ever wrong across a holiday -- and then the probe simply 404s and the walk back from
    today proceeds as before.
    """
    try:
        from icici_breeze_backend.app.services.market_calendar import is_trading_day
        from icici_breeze_backend.app.core.timezone import IST

        for step in range(1, 8):
            day = today + dt.timedelta(days=step)
            if is_trading_day(dt.datetime(day.year, day.month, day.day, 12, 0, tzinfo=IST)):
                return day
    except Exception:  # noqa: BLE001 - a calendar problem must not stop a SPAN refresh
        _logger.debug("SPAN resolver: exchange calendar unavailable; assuming next weekday", exc_info=True)
    day = today + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def resolve_latest_nse_span_archive(*, lookback_days: int | None = None) -> SpanArchiveRef | None:
    """Newest ``nsccl.{yyyymmdd}.i{n}.zip``: the next trading day's i1 if already published,
    else walking days back from today and versions down."""
    lookback = max(1, int(lookback_days or cfg.REFERENCE_DATA_LOOKBACK_DAYS))
    max_version = max(1, int(cfg.NSE_SPAN_MAX_INTRADAY_VERSION))
    today = today_ist_date()
    ahead = next_trading_day(today).strftime("%Y%m%d")
    url = cfg.NSE_SPAN_ARCHIVE_URL_TEMPLATE.format(yyyymmdd=ahead, version=1)
    if _url_exists(url, MARKET_NSE):
        return SpanArchiveRef(
            market=MARKET_NSE,
            exchange_code=cfg.NFO,
            archive_name=url.rsplit("/", 1)[-1],
            url=url,
            source_date=ahead,
            source_version=1,
            label="Intra-Day 01",
        )
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


def resolve_latest_bse_span_archive(*, lookback_days: int | None = None) -> SpanArchiveRef | None:
    """Newest ``BSERISK{yyyymmdd}-{mode}.ZIP``: the next trading day's beginning-of-day file if
    already published, else walking days back from today and file modes down.

    A slot firing seconds before BSE stamps a mode simply finds the previous one; a holiday
    costs six small 404s. Weekends are probed too, since special sessions do publish.
    """
    lookback = max(1, int(lookback_days or cfg.REFERENCE_DATA_LOOKBACK_DAYS))
    today = today_ist_date()
    ahead = next_trading_day(today).strftime("%Y%m%d")
    ordinal, suffix, label = _BSE_MODES[-1]
    url = cfg.BSE_SPAN_ARCHIVE_URL_TEMPLATE.format(yyyymmdd=ahead, mode=suffix)
    if _url_exists(url, MARKET_BSE):
        return SpanArchiveRef(
            market=MARKET_BSE,
            exchange_code=cfg.BFO,
            archive_name=url.rsplit("/", 1)[-1],
            url=url,
            source_date=ahead,
            source_version=ordinal,
            label=label,
        )
    for day_offset in range(lookback):
        day = today - dt.timedelta(days=day_offset)
        ymd = day.strftime("%Y%m%d")
        for ordinal, suffix, label in _BSE_MODES:
            url = cfg.BSE_SPAN_ARCHIVE_URL_TEMPLATE.format(yyyymmdd=ymd, mode=suffix)
            if not _url_exists(url, MARKET_BSE):
                continue
            return SpanArchiveRef(
                market=MARKET_BSE,
                exchange_code=cfg.BFO,
                archive_name=url.rsplit("/", 1)[-1],
                url=url,
                source_date=ymd,
                source_version=ordinal,
                label=label,
            )
    return None


def resolve_latest_span_archive(market: str, *, lookback_days: int | None = None) -> SpanArchiveRef | None:
    if market == MARKET_NSE:
        return resolve_latest_nse_span_archive(lookback_days=lookback_days)
    if market == MARKET_BSE:
        return resolve_latest_bse_span_archive(lookback_days=lookback_days)
    return None
