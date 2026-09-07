"""Tests for locating the newest published NSE/BSE SPAN archives."""
from __future__ import annotations

import datetime as dt

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.reference_data import span_sources


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, content: bytes = b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise span_sources.requests.HTTPError(f"status {self.status_code}")

    def close(self):
        self.closed = True


def _bse_index_rows(ymd: str, modes: tuple[str, ...]) -> dict:
    return {
        "Table": [
            {
                "File_Mode": mode,
                "FILE_TYPE": "XML_FILE",
                "File_Path": f"http://notices.bseindia.com/Risk_Automate/BSERISK{ymd}-{suffix}.ZIP",
            }
            for mode, suffix in zip(modes, ("00", "01", "02", "03", "04", "FINAL"))
        ]
    }


@pytest.fixture
def frozen_today(monkeypatch):
    day = dt.date(2026, 9, 4)
    monkeypatch.setattr(span_sources, "today_ist_date", lambda: day)
    return day


def test_nse_resolution_prefers_the_latest_intraday_revision(monkeypatch, frozen_today):
    """i5 exists on a normal session day; stopping the probe at i4 would serve 14:00 margins
    from a 15:45 slot."""
    available = {
        f"nsccl.20260904.i{v}.zip" for v in (1, 2, 3, 4, 5)
    }
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        name = url.rsplit("/", 1)[-1]
        return _FakeResponse(200 if name in available else 404)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    ref = span_sources.resolve_latest_nse_span_archive()

    assert ref is not None
    assert ref.archive_name == "nsccl.20260904.i5.zip"
    assert ref.source_version == 5
    assert ref.source_date == "20260904"
    assert ref.exchange_code == cfg.NFO
    # Probed i6 first and abandoned it, rather than assuming i4 was the ceiling.
    assert seen[0].endswith("i6.zip")


def test_nse_resolution_walks_back_to_the_previous_session(monkeypatch, frozen_today):
    def fake_get(url, **kwargs):
        return _FakeResponse(200 if "20260903.i2" in url else 404)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    ref = span_sources.resolve_latest_nse_span_archive()

    assert ref is not None
    assert ref.archive_name == "nsccl.20260903.i2.zip"


def test_bse_resolution_takes_the_newest_file_mode_and_rewrites_the_host(monkeypatch, frozen_today):
    """notices.bseindia.com does not resolve publicly; the page rewrites it before download."""

    def fake_get(url, **kwargs):
        if url == cfg.BSE_SPAN_MAXDATE_API_URL:
            return _FakeResponse(payload={"Table": [{"MaxDT": "04/09/2026"}]})
        if url == cfg.BSE_SPAN_INDEX_API_URL:
            return _FakeResponse(payload=_bse_index_rows("20260904", ("B", "I", "J", "K", "L", "Z")))
        return _FakeResponse(200)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    ref = span_sources.resolve_latest_bse_span_archive()

    assert ref is not None
    assert ref.archive_name == "BSERISK20260904-FINAL.ZIP"
    assert ref.url == "https://www.bseindia.com/bsedata/Risk_Automate/BSERISK20260904-FINAL.ZIP"
    assert "notices.bseindia.com" not in ref.url
    assert ref.source_version == 5  # Z, the highest file mode
    assert ref.exchange_code == cfg.BFO
    assert ref.label == "Final"


def test_bse_resolution_falls_back_when_the_newest_listed_file_is_not_up_yet(monkeypatch, frozen_today):
    """The index lists a mode as soon as it is scheduled, so a slot firing seconds early must
    fall through to the previous mode rather than failing outright."""

    def fake_get(url, **kwargs):
        if url == cfg.BSE_SPAN_MAXDATE_API_URL:
            return _FakeResponse(payload={"Table": [{"MaxDT": "04/09/2026"}]})
        if url == cfg.BSE_SPAN_INDEX_API_URL:
            return _FakeResponse(payload=_bse_index_rows("20260904", ("B", "I", "J", "K", "L")))
        return _FakeResponse(404 if url.endswith("-04.ZIP") else 200)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    ref = span_sources.resolve_latest_bse_span_archive()

    assert ref is not None
    assert ref.archive_name == "BSERISK20260904-03.ZIP"
    assert ref.source_version == 3


def test_bse_resolution_uses_the_index_only_for_the_xml_file_set(monkeypatch, frozen_today):
    """flag=1 is the binary PC-SPAN set, which the XML ingest cannot read."""
    flags: list = []

    def fake_get(url, **kwargs):
        if url == cfg.BSE_SPAN_MAXDATE_API_URL:
            return _FakeResponse(payload={"Table": [{"MaxDT": "04/09/2026"}]})
        if url == cfg.BSE_SPAN_INDEX_API_URL:
            flags.append((kwargs.get("params") or {}).get("flag"))
            return _FakeResponse(payload=_bse_index_rows("20260904", ("B",)))
        return _FakeResponse(200)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    span_sources.resolve_latest_bse_span_archive()

    assert flags == [0]


def test_probe_does_not_read_the_body(monkeypatch, frozen_today):
    """A probe abandons the ~9 MB payload; only the chosen archive is downloaded."""
    responses: list[_FakeResponse] = []

    def fake_get(url, **kwargs):
        resp = _FakeResponse(200)
        responses.append(resp)
        assert kwargs.get("stream") is True
        return resp

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    span_sources.resolve_latest_nse_span_archive()

    assert responses and all(r.closed for r in responses)


def test_resolution_returns_none_when_nothing_is_published(monkeypatch, frozen_today):
    monkeypatch.setattr(span_sources.requests, "get", lambda url, **kw: _FakeResponse(404))
    assert span_sources.resolve_latest_nse_span_archive(lookback_days=2) is None
