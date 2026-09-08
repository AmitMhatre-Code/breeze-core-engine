"""Bot trading windows are validated against the configured exchange calendar.

The close used to be a module constant, so a session the exchange lengthens (SEBI's
F&O extension being the live example) would have rejected every window ending in the
new final minutes until someone shipped a code change — even though the close time is
already operator-editable in Settings for the rest of the app.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.domain import bots as bots_domain
from icici_breeze_backend.app.domain.bots import SessionWindow, validate_session_windows
from icici_breeze_backend.app.services import market_calendar as mc


def _win(start: str, end: str) -> SessionWindow:
    return SessionWindow(start=start, end=end)


def _calendar(close_hour: int, close_minute: int):
    """Build the replacement from the *real* config, resolved before patching — a
    lazy `mc.get_calendar_config()` inside the patched callable would recurse into
    itself and land in `market_close_ist`'s fallback, quietly testing nothing."""
    cfg = mc.get_calendar_config()

    def _cfg():
        return mc.CalendarConfig(
            open_hour=cfg.open_hour,
            open_minute=cfg.open_minute,
            close_hour=close_hour,
            close_minute=close_minute,
            holidays=cfg.holidays,
        )

    return _cfg


def test_window_past_the_configured_close_is_refused(monkeypatch):
    monkeypatch.setattr(mc, "get_calendar_config", _calendar(15, 30))
    with pytest.raises(ValueError, match="runs past the 15:30 market close"):
        validate_session_windows([_win("09:35", "15:35")], "15:20")


def test_a_lengthened_session_accepts_the_later_window(monkeypatch):
    """The whole point: a Settings change, not a deploy."""
    monkeypatch.setattr(mc, "get_calendar_config", _calendar(15, 40))
    windows = validate_session_windows([_win("09:35", "15:35")], "15:40")
    assert windows[0].end == "15:35"


def test_error_text_quotes_the_configured_close_not_the_constant(monkeypatch):
    monkeypatch.setattr(mc, "get_calendar_config", _calendar(15, 40))
    with pytest.raises(ValueError, match="runs past the 15:40 market close"):
        validate_session_windows([_win("09:35", "15:45")], "15:40")


def test_falls_back_to_the_constant_when_the_calendar_is_unreadable(monkeypatch):
    def _boom():
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(mc, "get_calendar_config", _boom)
    assert bots_domain.market_close_ist() == bots_domain.MARKET_CLOSE_IST
