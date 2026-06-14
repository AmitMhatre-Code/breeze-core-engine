"""Tests for Strategy Builder build progress reporter."""
import unittest

from icici_breeze_backend.app.services.options_strategy_engine import propose_trades_jobs as jobs
from icici_breeze_backend.app.services.options_strategy_engine.build_progress import BuildProgress


class TestBuildProgress(unittest.TestCase):
    def setUp(self) -> None:
        jobs._jobs.clear()

    def test_register_base_units_and_ticks(self) -> None:
        job = jobs.create_job("user-a")
        progress = BuildProgress(job.job_id)
        progress.register_base_units(strategy_count=2)
        progress.tick(phase="setup", message="Loading scrip master…")
        progress.tick(phase="fetch_chain", message="Fetching call option chain…")

        stored = jobs.get_job_for_user(job.job_id, "user-a")
        assert stored is not None
        self.assertEqual(stored.progress_current, 2)
        self.assertEqual(stored.progress_total, 6)
        self.assertGreater(stored.progress_pct, 0)

    def test_add_units_extends_total(self) -> None:
        job = jobs.create_job("user-a")
        progress = BuildProgress(job.job_id)
        progress.register_base_units(strategy_count=1)
        progress.add_units(3, phase="margins", message="Calculating margins…")
        progress.tick(phase="setup", message="Loading scrip master…")

        stored = jobs.get_job_for_user(job.job_id, "user-a")
        assert stored is not None
        self.assertEqual(stored.progress_total, 8)


if __name__ == "__main__":
    unittest.main()
