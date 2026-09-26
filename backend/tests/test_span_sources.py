"""Tests for locating the newest published NSE/BSE SPAN archives."""
from __future__ import annotations

import datetime as dt

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.reference_data import span_sources


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, content: bytes = b"", content_type=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.closed = False
        # What both hosts actually send: an archive type for a file, HTML for a miss.
        if content_type is None:
            content_type = "application/zip" if status_code == 200 else "text/html; charset=utf-8"
        self.headers = {"Content-Type": content_type}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise span_sources.requests.HTTPError(f"status {self.status_code}")

    def close(self):
        self.closed = True


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


def test_bse_resolution_takes_the_newest_file_mode(monkeypatch, frozen_today):
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return _FakeResponse(200)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    ref = span_sources.resolve_latest_bse_span_archive()

    assert ref is not None
    assert ref.archive_name == "BSERISK20260904-FINAL.ZIP"
    assert ref.url == "https://www.bseindia.com/bsedata/Risk_Automate/BSERISK20260904-FINAL.ZIP"
    assert ref.source_version == 5  # mode Z, as BSE's page numbered it
    assert ref.exchange_code == cfg.BFO
    assert ref.label == "Final"
    assert seen == [ref.url]


def test_bse_resolution_never_calls_the_blocked_api(monkeypatch, frozen_today):
    """api.bseindia.com 403s every non-browser client since 2026-09-24; hammering it every slot
    only invites Akamai to flag the IP for the file host too."""
    hosts: set[str] = set()

    def fake_get(url, **kwargs):
        hosts.add(url.split("/")[2])
        return _FakeResponse(404)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    span_sources.resolve_latest_bse_span_archive(lookback_days=2)

    assert hosts == {"www.bseindia.com"}


def test_bse_resolution_falls_back_when_the_next_mode_is_not_up_yet(monkeypatch, frozen_today):
    """Mid-session: FINAL and 04 are not stamped yet, so a slot must take 03 rather than fail."""
    up = {"BSERISK20260904-00.ZIP", "BSERISK20260904-01.ZIP", "BSERISK20260904-02.ZIP", "BSERISK20260904-03.ZIP"}

    def fake_get(url, **kwargs):
        return _FakeResponse(200 if url.rsplit("/", 1)[-1] in up else 404)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    ref = span_sources.resolve_latest_bse_span_archive()

    assert ref is not None
    assert ref.archive_name == "BSERISK20260904-03.ZIP"
    assert ref.source_version == 3
    assert ref.label == "Intra-Day 03"


def test_bse_resolution_walks_back_over_a_holiday(monkeypatch, frozen_today):
    """Before the day's first file (or on a holiday) the previous session's Final is newest."""

    def fake_get(url, **kwargs):
        return _FakeResponse(200 if url.endswith("BSERISK20260902-FINAL.ZIP") else 404)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    ref = span_sources.resolve_latest_bse_span_archive()

    assert ref is not None
    assert ref.source_date == "20260902"
    assert ref.archive_name == "BSERISK20260902-FINAL.ZIP"


def test_an_html_200_is_not_an_archive(monkeypatch, frozen_today):
    """A bot-protection challenge served with status 200 must not be mistaken for the file."""

    def fake_get(url, **kwargs):
        if url.endswith("BSERISK20260904-FINAL.ZIP"):
            return _FakeResponse(200, content_type="text/html")
        return _FakeResponse(200 if url.endswith("BSERISK20260904-04.ZIP") else 404)

    monkeypatch.setattr(span_sources.requests, "get", fake_get)
    ref = span_sources.resolve_latest_bse_span_archive()

    assert ref is not None
    assert ref.archive_name == "BSERISK20260904-04.ZIP"


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
