"""Tests for propose-trades in-memory job store."""
import unittest

from icici_breeze_backend.app.services.options_strategy_engine import propose_trades_jobs as jobs


class TestProposeTradesJobs(unittest.TestCase):
    def setUp(self) -> None:
        jobs._jobs.clear()

    def test_create_and_get_job_for_user(self) -> None:
        job = jobs.create_job("user-a")
        fetched = jobs.get_job_for_user(job.job_id, "user-a")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.job_id, job.job_id)
        self.assertEqual(fetched.status, "queued")

    def test_get_job_wrong_user_returns_none(self) -> None:
        job = jobs.create_job("user-a")
        self.assertIsNone(jobs.get_job_for_user(job.job_id, "user-b"))

    def test_update_progress_and_complete(self) -> None:
        job = jobs.create_job("user-a")
        jobs.mark_running(job.job_id)
        jobs.update_progress(
            job.job_id,
            phase="fetch_chain",
            message="Fetching call option chain…",
            progress_current=1,
            progress_total=4,
        )
        updated = jobs.get_job_for_user(job.job_id, "user-a")
        assert updated is not None
        self.assertEqual(updated.status, "running")
        self.assertEqual(updated.progress_pct, 25)

        jobs.complete_job(job.job_id, {"trades": []})
        done = jobs.get_job_for_user(job.job_id, "user-a")
        assert done is not None
        self.assertEqual(done.status, "done")
        self.assertEqual(done.progress_pct, 100)
        self.assertEqual(done.result, {"trades": []})

    def test_fail_job(self) -> None:
        job = jobs.create_job("user-a")
        jobs.fail_job(job.job_id, "Insufficient market depth.")
        failed = jobs.get_job_for_user(job.job_id, "user-a")
        assert failed is not None
        self.assertEqual(failed.status, "error")
        self.assertEqual(failed.error, "Insufficient market depth.")

    def test_job_to_status_dict_includes_result_when_done(self) -> None:
        job = jobs.create_job("user-a")
        jobs.complete_job(job.job_id, {"spot_price": 100.0})
        payload = jobs.job_to_status_dict(job)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["result"], {"spot_price": 100.0})


if __name__ == "__main__":
    unittest.main()
