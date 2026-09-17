"""The durable per-day audit trail for the scalping bots (audit.bot_audit).

The gap this closes: a scalper's run row holds only the LAST verdict, and the log line that
carried the rest lives in a container whose logs rotate and are lost when the instance is
recreated overnight. A paper day that produced no trades was therefore unexplainable the
following morning. These tests pin the properties that make it explainable.
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.audit import bot_audit

USER = "user1"
BOT = "momentum_long_scalper"


@pytest.fixture
def audit_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(bot_audit.cfg, "DATA_PATH", str(root) + os.sep)
    # Retention prunes against the wall clock while every record here is dated September 2026,
    # so an unpinned clock silently deletes the older fixtures as the real date moves on: this
    # file began failing on 2026-09-16, the day the 8th fell outside the 7-day window. Pinned to
    # the newest day the tests use, so nothing they write is ever already expired. The tests
    # that exercise retention itself pass `today=` explicitly and are unaffected.
    monkeypatch.setattr(
        bot_audit, "now_ist", lambda: datetime.datetime(2026, 9, 9, 17, 0, tzinfo=IST)
    )
    bot_audit._last_signature.clear()
    bot_audit._last_pruned.clear()
    return str(root / "bots-audit")


def _detail(*, candles=50, resets=0, stale=0, action="idle"):
    return {
        "action": action,
        "feed": {
            "warm": True, "stale": False, "subscribed": True, "ticks_seen": 100,
            "candles": candles, "candles_required": 20,
            "counter_resets": resets, "stale_ticks": stale,
        },
        "gates": {"trading_allowed": True},
        "decision": {"volume_x": 0.9},
    }


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _at(hour, minute=0, day=9):
    return datetime.datetime(2026, 9, day, hour, minute, tzinfo=IST)


# --------------------------------------------------------------------------- cadence


def test_in_window_every_pass_is_recorded(audit_root):
    """Full fidelity inside a trading window.

    Collapsing repeats here would destroy the answer the trail exists to give: that the
    signal was evaluated all afternoon and never fired once.
    """
    for i in range(5):
        bot_audit.record_pass(
            USER, BOT, "run-1", detail=_detail(), reason_code="signal_no_trade",
            reason_text="no signal", in_window=True, now=_at(10, 30) + datetime.timedelta(seconds=2 * i),
        )
    rows = _read(os.path.join(audit_root, bot_audit.file_name(USER, BOT, _at(10).date())))
    assert len(rows) == 5
    assert all(r["reason_code"] == "signal_no_trade" for r in rows)
    assert all(r["in_window"] is True for r in rows)


def test_out_of_window_collapses_identical_passes(audit_root):
    """A bot standing down at 03:00 has nothing new to say on each pass."""
    for i in range(5):
        bot_audit.record_pass(
            USER, BOT, "run-1", detail=_detail(), reason_code="outside_window",
            reason_text="outside every window", in_window=False,
            now=_at(3, 0) + datetime.timedelta(seconds=2 * i),
        )
    rows = _read(os.path.join(audit_root, bot_audit.file_name(USER, BOT, _at(3).date())))
    assert len(rows) == 1


def test_out_of_window_still_records_a_change(audit_root):
    bot_audit.record_pass(
        USER, BOT, "run-1", detail=_detail(), reason_code="outside_window",
        reason_text="x", in_window=False, now=_at(3, 0),
    )
    bot_audit.record_pass(
        USER, BOT, "run-1", detail=_detail(resets=1), reason_code="outside_window",
        reason_text="x", in_window=False, now=_at(3, 1),
    )
    rows = _read(os.path.join(audit_root, bot_audit.file_name(USER, BOT, _at(3).date())))
    assert len(rows) == 2
    assert [r["feed"]["counter_resets"] for r in rows] == [0, 1]


def test_the_record_carries_the_feed_counters(audit_root):
    """The numbers that name a reset-heavy session, kept where logs cannot rotate them away."""
    bot_audit.record_pass(
        USER, BOT, "run-1", detail=_detail(candles=3, resets=12, stale=47),
        reason_code="not_warm", reason_text="warming up", in_window=True, now=_at(11, 0),
    )
    row = _read(os.path.join(audit_root, bot_audit.file_name(USER, BOT, _at(11).date())))[0]
    assert row["feed"]["counter_resets"] == 12
    assert row["feed"]["stale_ticks"] == 47
    assert row["decision"] == {"volume_x": 0.9}


def test_an_event_is_never_throttled(audit_root):
    for _ in range(3):
        bot_audit.record_event(USER, BOT, "run-1", "cycle_opened", {"cycle_no": 1})
    day = bot_audit.now_ist().date()
    rows = _read(os.path.join(audit_root, bot_audit.file_name(USER, BOT, day)))
    assert len(rows) == 3
    assert all(r["event"] == "cycle_opened" for r in rows)


# --------------------------------------------------------------------------- retention


def test_retention_prunes_by_filename_date_not_mtime(audit_root):
    """A file is named for the day it describes; an append after midnight must not save it."""
    os.makedirs(audit_root, exist_ok=True)
    today = datetime.date(2026, 9, 9)
    for delta in (0, 3, 7, 30):
        day = today - datetime.timedelta(days=delta)
        open(os.path.join(audit_root, bot_audit.file_name(USER, BOT, day)), "w").close()

    removed = bot_audit.enforce_retention(days=7, today=today)

    assert removed == 1  # only the 30-day-old file
    kept = sorted(os.listdir(audit_root))
    assert len(kept) == 3
    assert not any("2026-08-10" in name for name in kept)


def test_retention_ignores_foreign_files(audit_root):
    os.makedirs(audit_root, exist_ok=True)
    open(os.path.join(audit_root, "README.txt"), "w").close()
    bot_audit.enforce_retention(days=0, today=datetime.date(2026, 9, 9))
    assert "README.txt" in os.listdir(audit_root)


# --------------------------------------------------------------------------- reading


def test_index_lists_one_row_per_bot_per_day(audit_root):
    for day in (_at(10, 0, day=8), _at(10, 0, day=9)):
        bot_audit.record_pass(
            USER, BOT, "r", detail=_detail(), reason_code="x", reason_text="y",
            in_window=True, now=day,
        )
    rows = bot_audit.list_index_for_user(USER)
    assert [r["trading_date"] for r in rows] == ["2026-09-09", "2026-09-08"]  # newest first
    assert all(r["bot_type"] == BOT and r["records"] == 1 for r in rows)
    assert all(r["size_bytes"] > 0 for r in rows)


def test_index_does_not_leak_another_users_files(audit_root):
    bot_audit.record_pass(
        USER, BOT, "r", detail=_detail(), reason_code="x", reason_text="y",
        in_window=True, now=_at(10),
    )
    assert bot_audit.list_index_for_user("someone-else") == []


@pytest.mark.parametrize(
    "name",
    ["../../../etc/passwd", "/etc/passwd", "user1__momentum_long_scalper__../x.jsonl", "nope.jsonl"],
)
def test_resolve_refuses_anything_that_is_not_this_users_file(audit_root, name):
    """A caller-supplied string reaches the filesystem here."""
    assert bot_audit.resolve_file_for_user(name, USER) is None


def test_resolve_refuses_another_users_file(audit_root):
    bot_audit.record_pass(
        USER, BOT, "r", detail=_detail(), reason_code="x", reason_text="y",
        in_window=True, now=_at(10),
    )
    name = bot_audit.list_index_for_user(USER)[0]["name"]
    assert bot_audit.resolve_file_for_user(name, USER) is not None
    assert bot_audit.resolve_file_for_user(name, "someone-else") is None


def test_find_for_run_maps_a_fragmented_day_to_one_file(audit_root):
    """Eleven interrupted session rows on one day must all resolve to the same record."""
    bot_audit.record_pass(
        USER, BOT, "r", detail=_detail(), reason_code="x", reason_text="y",
        in_window=True, now=_at(10),
    )
    first = bot_audit.find_for_run(USER, BOT, "2026-09-09 08:02:35")
    last = bot_audit.find_for_run(USER, BOT, "2026-09-09 14:12:54")
    assert first is not None and first == last


def test_find_for_run_returns_none_when_nothing_was_written(audit_root):
    assert bot_audit.find_for_run(USER, BOT, "2026-09-09 08:02:35") is None
    assert bot_audit.find_for_run(USER, BOT, None) is None
    assert bot_audit.find_for_run(USER, BOT, "garbage") is None


def test_zip_bundles_only_this_users_files(audit_root):
    import io
    import zipfile

    bot_audit.record_pass(
        USER, BOT, "r", detail=_detail(), reason_code="x", reason_text="y",
        in_window=True, now=_at(10),
    )
    payload, filename = bot_audit.build_zip_for_user(USER)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = zf.namelist()
    assert len(names) == 1 and names[0].startswith("user1__")
    assert filename.endswith(".zip")

    with pytest.raises(FileNotFoundError):
        bot_audit.build_zip_for_user("someone-else")


def test_a_write_failure_never_propagates(audit_root, monkeypatch):
    """The trail is diagnostic; the bot outranks it."""
    monkeypatch.setattr(bot_audit, "_append", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    bot_audit.record_pass(
        USER, BOT, "r", detail=_detail(), reason_code="x", reason_text="y",
        in_window=True, now=_at(10),
    )  # must not raise
    bot_audit.record_event(USER, BOT, "r", "cycle_opened", {})
