"""Broker session token expiry notation.

`expires_at` is the one stored timestamp deliberately kept offset-aware rather than
flattened to an IST wall-clock string: it is an instant the code compares against, not
a wall clock anyone reads. These tests pin the two properties that exemption rests on --
it must still mean the coming IST midnight, and it must compare identically to the
`+00:00` form already sitting in every deployed database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.repositories.broker_session import next_midnight_ist


def test_is_the_coming_midnight_in_ist():
    before = now_ist()
    expiry = next_midnight_ist()

    assert (expiry.hour, expiry.minute, expiry.second, expiry.microsecond) == (0, 0, 0, 0)
    assert expiry.utcoffset() == IST.utcoffset(datetime.now())
    # Strictly ahead, and never more than a full day out -- asserted as a window rather
    # than against a recomputed `now_ist()` so a run that straddles midnight can't flake.
    assert timedelta(0) < expiry - before <= timedelta(days=1)


def test_reads_as_midnight_rather_than_half_past_six():
    """The whole point of the notation change: the stored string says what it means."""
    assert next_midnight_ist().isoformat().endswith("T00:00:00+05:30")


def test_compares_identically_to_the_legacy_utc_notation():
    """Rows written before the change carry `+00:00`. Nothing was migrated, so the two
    notations must be the same instant -- otherwise old sessions expire at the wrong
    time."""
    new_form = next_midnight_ist()
    legacy_form = new_form.astimezone(timezone.utc)

    assert legacy_form.isoformat() != new_form.isoformat()  # different text ...
    assert datetime.fromisoformat(legacy_form.isoformat()) == datetime.fromisoformat(
        new_form.isoformat()
    )  # ... same instant


def test_expiry_check_agrees_across_both_notations():
    """`get_broker_session_token`'s guard is `now(utc) >= expires_at`; it must reach the
    same verdict whichever notation the row was written in."""
    expiry = next_midnight_ist()
    now = datetime.now(timezone.utc)
    assert (now >= expiry) == (now >= expiry.astimezone(timezone.utc))
