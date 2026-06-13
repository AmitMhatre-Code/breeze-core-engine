"""Tests for concurrent margin_calculator batching."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.margin_async_fetch import (
    MarginFetchRequest,
    fetch_margins_concurrent,
)


class TestFetchMarginsConcurrent(unittest.TestCase):
    def test_all_succeed(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 42_000.0},
        }
        requests = [
            MarginFetchRequest(
                cache_key=("a",),
                margin_input=[{"strike_price": 23000}],
                strategy_id="iron_condor",
                phase="ic_candidate_span",
            ),
            MarginFetchRequest(
                cache_key=("b",),
                margin_input=[{"strike_price": 23100}],
                strategy_id="iron_condor",
                phase="ic_candidate_span",
            ),
        ]
        spans = asyncio.run(
            fetch_margins_concurrent(proc, "u1", "NFO", requests)
        )
        self.assertEqual(spans[("a",)], 42_000.0)
        self.assertEqual(spans[("b",)], 42_000.0)
        self.assertEqual(proc.strategy_builder_margin.call_count, 2)

    def test_partial_http_failure(self):
        proc = MagicMock()

        def margin(user_id, exchange, legs, audit=None, audit_context=None, audit_rationale=None):
            if legs[0]["strike_price"] == 22900:
                return {"Status": 400, "Error": "bad request"}
            return {"Status": 200, "Success": {"span_margin_required": 55_000.0}}

        proc.strategy_builder_margin.side_effect = margin
        requests = [
            MarginFetchRequest(
                cache_key=("fail",),
                margin_input=[{"strike_price": 22900}],
                strategy_id="short_strangle",
                phase="ss_candidate_span",
            ),
            MarginFetchRequest(
                cache_key=("ok",),
                margin_input=[{"strike_price": 22800}],
                strategy_id="short_strangle",
                phase="ss_candidate_span",
            ),
        ]
        spans = asyncio.run(
            fetch_margins_concurrent(proc, "u1", "NFO", requests)
        )
        self.assertEqual(spans[("fail",)], 0.0)
        self.assertEqual(spans[("ok",)], 55_000.0)

    def test_exception_isolation(self):
        proc = MagicMock()

        def margin(user_id, exchange, legs, audit=None, audit_context=None, audit_rationale=None):
            if legs[0]["strike_price"] == 22900:
                raise ConnectionError("network down")
            return {"Status": 200, "Success": {"span_margin_required": 33_000.0}}

        proc.strategy_builder_margin.side_effect = margin
        requests = [
            MarginFetchRequest(
                cache_key=("err",),
                margin_input=[{"strike_price": 22900}],
                strategy_id="naked_pe_short",
                phase="unit_span_sizing",
            ),
            MarginFetchRequest(
                cache_key=("ok",),
                margin_input=[{"strike_price": 22800}],
                strategy_id="naked_pe_short",
                phase="unit_span_sizing",
            ),
        ]
        spans = asyncio.run(
            fetch_margins_concurrent(proc, "u1", "NFO", requests)
        )
        self.assertEqual(spans[("err",)], 0.0)
        self.assertEqual(spans[("ok",)], 33_000.0)

    def test_skips_existing_cache_keys(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 10_000.0},
        }
        existing = {("cached",): 99_000.0}
        requests = [
            MarginFetchRequest(
                cache_key=("cached",),
                margin_input=[{"strike_price": 23000}],
                strategy_id="iron_condor",
            ),
            MarginFetchRequest(
                cache_key=("new",),
                margin_input=[{"strike_price": 23100}],
                strategy_id="iron_condor",
            ),
        ]
        spans = asyncio.run(
            fetch_margins_concurrent(
                proc, "u1", "NFO", requests, existing_cache=existing
            )
        )
        self.assertEqual(spans[("cached",)], 99_000.0)
        self.assertEqual(spans[("new",)], 10_000.0)
        self.assertEqual(proc.strategy_builder_margin.call_count, 1)
