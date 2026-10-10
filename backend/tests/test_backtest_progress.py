"""The backtest progress bar's numbers (`bots/backtest_jobs`): what it counts, when it stops
counting, and when it is willing to say how long is left."""
from __future__ import annotations

import datetime
import threading

import pytest

from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots.backtest_service import SessionCounter

D = datetime.date


class Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def job(monkeypatch):
    """A job holding the slot until the test lets it go, on a clock the test moves."""
    clock = Clock()
    monkeypatch.setattr(jobs.time, "monotonic", clock)
    release = threading.Event()
    jobs._start("test", lambda: release.wait(5))
    yield clock
    release.set()
    jobs._thread.join(5)


def test_a_replay_counts_sessions_across_its_settings(job):
    jobs._begin_step(1, 3)
    assert jobs.state()["total"] is None  # sessions not known until the first one starts
    jobs._on_day(D(2026, 3, 9), 1, 4)
    jobs._on_day(D(2026, 3, 10), 2, 4)
    s = jobs.state()
    assert (s["done"], s["total"], s["unit"], s["session"], s["sessions"]) == (1, 12, "sessions", 2, 4)
    jobs._begin_step(2, 3)
    s = jobs.state()
    assert (s["done"], s["total"], s["session"]) == (4, 12, None)
    jobs._on_day(D(2026, 3, 9), 1, 4)
    assert jobs.state()["done"] == 4


def test_a_new_phase_without_a_count_clears_the_old_one(job):
    jobs._update(phase="fetching")
    jobs._progress(40, 40, "option windows")
    jobs._update(phase="sizing")
    s = jobs.state()
    assert (s["done"], s["total"], s["unit"]) == (None, None, None)


def test_time_left_waits_for_a_rate_worth_quoting(job):
    jobs._update(phase="replaying")
    jobs._progress(0, 100, "sessions")
    job.t += 5
    jobs._progress(10, 100, "sessions")
    assert jobs.state()["eta_seconds"] is None  # too soon to quote
    job.t += 15
    jobs._progress(20, 100, "sessions")
    # 20 sessions in 20 s: 80 left at one a second.
    assert jobs.state()["eta_seconds"] == 80


def test_time_left_keeps_its_rate_across_a_fetch_round(job):
    """The condor replays, fetches, then replays on: one rate for the replay, fetch time and
    all, rather than a fresh guess after every round."""
    jobs._update(phase="replaying", done=0, total=100, unit="checks")
    job.t += 20
    jobs._update(phase="replaying", done=10, total=100, unit="checks")
    jobs._update(phase="fetching")
    jobs._progress(0, 5, "option windows")
    job.t += 20
    jobs._update(phase="replaying", done=20, total=100, unit="checks")
    assert jobs.state()["eta_seconds"] == 160  # 20 checks in 40 s


def test_session_counter_numbers_sessions_across_indices():
    seen = []
    counted = SessionCounter.wrap(lambda day, n, of: seen.append((day, n, of)), 3)
    for day in (D(2026, 3, 10), D(2026, 3, 12), D(2026, 3, 17)):
        counted(day)
    assert [(n, of) for _d, n, of in seen] == [(1, 3), (2, 3), (3, 3)]
    assert SessionCounter.wrap(None, 3) is None
