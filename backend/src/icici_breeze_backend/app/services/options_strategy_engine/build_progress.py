"""Progress reporter for Strategy Builder propose-trades jobs."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.propose_trades_jobs import (
    mark_running,
    update_progress,
)


class BuildProgress:
    """Tracks completed/total work units and pushes updates to the job store."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._completed = 0
        self._total = 0
        self._phase = "setup"
        self._message = "Starting…"

    @property
    def completed(self) -> int:
        return self._completed

    @property
    def total(self) -> int:
        return self._total

    def register_base_units(self, *, strategy_count: int) -> None:
        """Reserve units known at job start: setup, bulk CE/PE, strategies, finalize."""
        self._total = 1 + 2 + max(0, strategy_count) + 1
        self._completed = 0
        self._push()

    def add_units(self, n: int, *, phase: str, message: str) -> None:
        if n <= 0:
            return
        self._phase = phase
        self._message = message
        self._total += n
        self._push()

    def tick(self, *, phase: str, message: str) -> None:
        self._phase = phase
        self._message = message
        self._completed += 1
        self._push()

    def set_message(self, *, phase: str, message: str) -> None:
        """Update label without advancing completed count."""
        self._phase = phase
        self._message = message
        self._push()

    def mark_running(self) -> None:
        mark_running(self.job_id)

    def _push(self) -> None:
        update_progress(
            self.job_id,
            phase=self._phase,
            message=self._message,
            progress_current=self._completed,
            progress_total=max(1, self._total),
        )
