"""Retention of the raw SPAN archives, and the lookup the margin harness uses to find them.

The archives exist so a margin figure can be re-derived against the exact snapshot it came from.
SPAN is republished six times a trading day and revisions drift by a percent or two, so the lookup
has to say whether it found the exact revision or only a neighbour -- a silent substitution would
turn snapshot drift into an apparent engine disagreement.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from icici_breeze_backend.app.services import nsccl_baseline as nb


class SpanArchiveRetention(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = patch.object(nb.cfg, "DATA_PATH", self._tmp.name + os.sep)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_archive_family_splits_nse_from_bse(self):
        self.assertEqual(nb._archive_family("nsccl.20260909.i3.zip"), "nsccl.")
        self.assertEqual(nb._archive_family("BSERISK20260909-02.ZIP"), "BSERISK")
        self.assertNotEqual(
            nb._archive_family("nsccl.20260909.i3.zip"),
            nb._archive_family("BSERISK20260909-02.ZIP"),
        )

    def test_retained_archive_is_found_exactly(self):
        nb.retain_span_archive(b"payload", source_date="20260909", archive_name="nsccl.20260909.i3.zip")
        found = nb.find_span_archive("20260909", "nsccl.20260909.i3.zip")
        self.assertIsNotNone(found)
        path, exact = found
        self.assertTrue(exact)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), b"payload")

    def test_other_revision_of_same_day_is_flagged_inexact(self):
        nb.retain_span_archive(b"x", source_date="20260909", archive_name="nsccl.20260909.i5.zip")
        found = nb.find_span_archive("20260909", "nsccl.20260909.i3.zip")
        self.assertIsNotNone(found)
        path, exact = found
        self.assertFalse(exact, "a neighbouring revision must not be reported as the exact snapshot")
        self.assertTrue(path.endswith("nsccl.20260909.i5.zip"))

    def test_bse_case_never_falls_back_to_the_nse_archive(self):
        nb.retain_span_archive(b"x", source_date="20260909", archive_name="nsccl.20260909.i3.zip")
        self.assertIsNone(nb.find_span_archive("20260909", "BSERISK20260909-02.ZIP"))

    def test_missing_date_returns_none(self):
        self.assertIsNone(nb.find_span_archive("20200101", "nsccl.20200101.i3.zip"))

    def test_purge_keeps_only_the_most_recent_dates(self):
        for day in ("20260901", "20260902", "20260903", "20260904", "20260905", "20260908", "20260909"):
            nb.retain_span_archive(b"x", source_date=day, archive_name=f"nsccl.{day}.i3.zip")
        kept = sorted(os.listdir(nb.span_archive_dir()))
        self.assertEqual(len(kept), nb.SPAN_ARCHIVE_RETAIN_DATES)
        self.assertEqual(kept[-1], "20260909")
        self.assertNotIn("20260901", kept)

    def test_retention_failure_never_raises(self):
        self.assertIsNone(nb.retain_span_archive(b"", source_date="20260909", archive_name="a.zip"))
        self.assertIsNone(nb.retain_span_archive(b"x", source_date="", archive_name="a.zip"))
        self.assertIsNone(nb.retain_span_archive(b"x", source_date="20260909", archive_name=""))

    def test_no_partial_files_are_left_behind(self):
        nb.retain_span_archive(b"x", source_date="20260909", archive_name="nsccl.20260909.i3.zip")
        names = os.listdir(os.path.join(nb.span_archive_dir(), "20260909"))
        self.assertEqual(names, ["nsccl.20260909.i3.zip"])


if __name__ == "__main__":
    unittest.main()
