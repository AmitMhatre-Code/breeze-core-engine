"""Tests for Strategy Builder (New) per-session audit logs."""
import asyncio
import json
import os
import tempfile
import time
import unittest
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bear_call_spread import (
    calc_bear_call_spread,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bull_put_spread import (
    calc_bull_put_spread,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
)
from icici_breeze_backend.app.services.nsccl_baseline import MARGIN_SOURCE_EXCHANGE
from icici_breeze_backend.audit.strategy_builder_audit import (
    StrategyBuilderAuditSession,
    _MAX_AUDIT_LOGS_PER_USER,
    audit_log_dir,
    build_audit_zip_for_user,
    enforce_audit_retention,
    list_audit_files_for_user,
    list_audit_log_index_for_user,
    quote_row_to_audit,
    resolve_audit_file_for_user,
    resolve_explainability_for_session,
)
from icici_breeze_backend.audit.user_explainability import build_user_explainability_report

def _chain_row(strike: int, right: str) -> dict:
    return {
        "strike_price": strike,
        "ltp": 50.0,
        "best_bid_price": 49.0,
        "best_offer_price": 51.0,
        "total_buy_qty": 100,
        "total_sell_qty": 100,
        "spot_price": 23500.0,
        "right": right,
    }


def _mock_fetch_option_chain_quotes_sb(*_args, **kwargs):
    audit = kwargs.get("audit")
    strike_price = kwargs.get("strike_price")
    right = kwargs.get("right", "Call")
    if strike_price is None:
        strikes = list(range(23000, 24100, 50))
        res = {"Status": 200, "Success": [_chain_row(s, right) for s in strikes]}
        req = {"right": right, "full_chain": True}
    else:
        strike = int(strike_price)
        res = {"Status": 200, "Success": [_chain_row(strike, right)]}
        req = {"strike_price": strike_price, "right": right}
    if audit:
        audit.record_icici_api_call(
            "get_option_chain_quotes",
            req,
            res,
            rationale=kwargs.get("audit_rationale"),
        )
    return res


def _mock_strategy_builder_margin(*_args, **kwargs):
    res = {"Status": 200, "Success": {"span_margin_required": 100_000}}
    audit = kwargs.get("audit")
    if audit:
        audit.record_icici_api_call(
            "margin_calculator",
            {
                "exchange_code": _args[1] if len(_args) > 1 else "NFO",
                "margin_list": _args[2] if len(_args) > 2 else [],
                **(kwargs.get("audit_context") or {}),
            },
            res,
        )
    return res


class TestStrategyBuilderAuditSession(unittest.TestCase):
    def test_writes_json_audit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="user-1",
                    request={"stock_code": "NIFTY", "strategy_category": "income"},
                    request_id="req-abc",
                )
                session.record("test", "hello", {"x": 1}, rationale="because")
                path = session.finalize({"status": "ok"})
                self.assertTrue(os.path.isfile(path))
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                self.assertEqual(doc["user_id"], "user-1")
                self.assertEqual(doc["request_id"], "req-abc")
                self.assertEqual(doc["source"], "strategy_builder_new")
                self.assertEqual(len(doc["events"]), 1)
                self.assertEqual(doc["events"][0]["rationale"], "because")
                self.assertEqual(doc["summary"]["status"], "ok")
                self.assertEqual(doc["icici_api_calls"]["total"], 0)

    def test_finalize_emits_schema_v2_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="user-1",
                    request={"stock_code": "NIFTY", "strategy_category": "income"},
                    audit_detail_level="summary",
                    strategy_ids=["bull_put_spread", "iron_condor"],
                )
                collector = session.begin_strategy_collector("bull_put_spread")
                collector.record_generated()
                collector.record("pop_floor", short_strike=23600)
                collector.set_status("skipped", "No survivors")
                session.finish_strategy_collector(collector)
                session.record_icici_api_call(
                    "get_option_chain_quotes",
                    {"strike": 23300},
                    {"Status": 200},
                    latency_ms=12.5,
                )
                path = session.finalize({"status": "ok"})
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                self.assertEqual(doc["audit_schema_version"], "2.0")
                self.assertEqual(doc["audit_detail_level"], "summary")
                self.assertIn("configuration_snapshot", doc)
                self.assertIn("telemetry", doc)
                self.assertIn("strategy_evaluations", doc)
                self.assertIn("bull_put_spread", doc["strategy_evaluations"])
                bps = doc["strategy_evaluations"]["bull_put_spread"]
                self.assertIn("strategy_summary", bps)
                self.assertIn("rejection_funnel", bps)
                self.assertIn("pop_policy", bps)
                self.assertEqual(doc["telemetry"]["quote_calls"], 1)
                self.assertEqual(doc["telemetry"]["quote_latency_ms"]["count"], 1)

    def test_debug_detail_level_includes_candidate_traces(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="user-1",
                    request={"stock_code": "NIFTY"},
                    audit_detail_level="debug",
                )
                collector = session.begin_strategy_collector("bear_call_spread")
                collector.record_evaluation(
                    outcome="rejected",
                    reject_reason="pop_floor",
                    pop_pct=60.0,
                )
                session.finish_strategy_collector(collector)
                path = session.finalize({"status": "ok"})
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                bcs = doc["strategy_evaluations"]["bear_call_spread"]
                self.assertIn("candidate_traces", bcs)
                self.assertGreater(len(bcs["candidate_traces"]), 0)

    def test_api_call_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="u1",
                    request={"stock_code": "NIFTY"},
                )
                session.record_icici_api_call(
                    "get_option_chain_quotes", {"strike": 23300}, {"Status": 200}
                )
                session.record_icici_api_call(
                    "get_option_chain_quotes", {"strike": 23350}, {"Status": 503}
                )
                session.record_icici_api_call(
                    "margin_calculator", {"legs": []}, {"Status": 200}
                )
                session.set_temp_liquid_cache(
                    {
                        "liquid_ce_strikes": [23300],
                        "options": [],
                    }
                )
                path = session.finalize({"status": "ok"})
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                stats = doc["icici_api_calls"]
                self.assertEqual(stats["total"], 3)
                self.assertEqual(stats["total_success"], 2)
                self.assertEqual(stats["total_failed"], 1)
                self.assertEqual(stats["by_api"]["get_option_chain_quotes"]["total"], 2)
                self.assertEqual(stats["by_api"]["get_option_chain_quotes"]["success"], 1)
                self.assertEqual(stats["by_api"]["get_option_chain_quotes"]["failed"], 1)
                self.assertEqual(stats["by_api"]["margin_calculator"]["total"], 1)
                self.assertEqual(stats["by_api"]["margin_calculator"]["success"], 1)
                self.assertEqual(doc["temp_liquid_cache"]["liquid_ce_strikes"], [23300])
                api_events = [e for e in doc["events"] if e["category"] == "api_call"]
                self.assertTrue(all("success" in e["data"] for e in api_events))
                self.assertEqual(api_events[0]["data"]["api"], "get_option_chain_quotes")

    def test_icici_api_event_records_success_flag(self):
        session = StrategyBuilderAuditSession(user_id="u1", request={"stock_code": "NIFTY"})
        session.record_icici_api_call("margin_calculator", {}, {"Status": 400, "Error": "bad"})
        event = session.events[-1]
        self.assertFalse(event["data"]["success"])
        self.assertEqual(session.icici_api_call_stats["total_failed"], 1)

    def test_quote_row_to_audit_includes_liquid_flag(self):
        q = QuoteRow(
            strike=23300,
            right="Call",
            ltp=100.0,
            best_bid_price=99.0,
            best_offer_price=101.0,
            total_buy_qty=10,
            total_sell_qty=20,
            buy_sell_ratio=0.5,
        )
        out = quote_row_to_audit(q)
        self.assertTrue(out["liquid"])


class TestEngineAuditIntegration(unittest.TestCase):
    def _ctx(self, audit: StrategyBuilderAuditSession) -> EngineContext:
        strikes = list(range(23000, 24100, 50))
        cache = {}
        for s in strikes:
            call_delta = min(0.35, 0.08 + (s - 23500) / 5000.0)
            put_delta = -min(0.35, 0.08 + (23500 - s) / 5000.0)
            call_bid = max(1.0, 30.0 - (s - 23000) / 50.0)
            call_ask = call_bid + 0.5
            put_bid = max(1.0, 30.0 - (23500 - s) / 50.0)
            put_ask = put_bid + 0.5
            cache[(s, "Call")] = QuoteRow(
                strike=s,
                right="Call",
                ltp=(call_bid + call_ask) / 2,
                best_bid_price=call_bid,
                best_offer_price=call_ask,
                total_buy_qty=100,
                total_sell_qty=100,
                buy_sell_ratio=1.0,
                spot_price=23500.0,
                delta=call_delta,
            )
            cache[(s, "Put")] = QuoteRow(
                strike=s,
                right="Put",
                ltp=(put_bid + put_ask) / 2,
                best_bid_price=put_bid,
                best_offer_price=put_ask,
                total_buy_qty=100,
                total_sell_qty=100,
                buy_sell_ratio=1.0,
                spot_price=23500.0,
                delta=put_delta,
            )
        return EngineContext(
            processor=MagicMock(),
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            min_pop_pct=1.0,
            provision_elm=False,
            strategy_category="income",
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=23500,
            atm_strike=23500,
            atm_iv=0.18,
            cache=cache,
            audit=audit,
        )

    def test_bear_call_audit_records_wing_search(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 50_000.0}
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                with patch(
                    "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bear_call_spread.MIN_BCS_ANNUALIZED_RETURN_PCT",
                    0.0,
                ):
                    audit = StrategyBuilderAuditSession(
                        user_id="u1",
                        request={"stock_code": "NIFTY"},
                    )
                    ctx = self._ctx(audit)
                    ctx.processor = proc
                    results = asyncio.run(calc_bear_call_spread(ctx))
                    res = results[0]
                    audit.finalize({"status": "ok", "strategy": res.strategy_id})
                calc_titles = [
                    e["message"]
                    for e in audit.events
                    if e["category"] == "calculation"
                ]
                self.assertIn("Bear call spread candidate search", calc_titles)
                self.assertIn("Bear call spread SPAN refinement", calc_titles)

    def test_bull_put_audit_records_wing_search(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 50_000.0}
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                with patch(
                    "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bull_put_spread.MIN_BPS_ANNUALIZED_RETURN_PCT",
                    0.0,
                ):
                    audit = StrategyBuilderAuditSession(
                        user_id="u1",
                        request={"stock_code": "NIFTY"},
                    )
                    ctx = self._ctx(audit)
                    ctx.processor = proc
                    results = asyncio.run(calc_bull_put_spread(ctx))
                    res = results[0]
                    audit.finalize({"status": "ok", "strategy": res.strategy_id})
                calc_titles = [
                    e["message"]
                    for e in audit.events
                    if e["category"] == "calculation"
                ]
                self.assertIn("Bull put spread candidate search", calc_titles)
                self.assertIn("Bull put spread SPAN refinement", calc_titles)

    def test_run_propose_trades_emits_audit_metadata(self):
        from icici_breeze_backend.app.services.options_strategy_engine import run_propose_trades

        strikes = list(range(23000, 24100, 50))
        proc = MagicMock()
        proc.fetch_lot_size.return_value = 75
        proc.list_option_strikes.return_value = strikes
        proc.strike_interval.return_value = 50
        proc.search_interval.return_value = 50
        proc.fetch_option_chain_quotes_sb.side_effect = _mock_fetch_option_chain_quotes_sb
        proc.strategy_builder_margin.side_effect = _mock_strategy_builder_margin

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                import asyncio

                out = asyncio.run(
                    run_propose_trades(
                        proc,
                        "u1",
                        exchange_code="NFO",
                        stock_code="NIFTY",
                        expiry_date="09-Jun-2025",
                        margin_lacs=5.0,
                        max_loss_lacs=2.0,
                        min_pop_pct=1.0,
                        provision_elm=False,
                        strategy_category="income",
                        risk_reward_profile="moderate",
                        request_id="req-1",
                    )
                )
                self.assertEqual(out["Status"], 200)
                success = out["Success"]
                self.assertIn("audit_session_id", success)
                self.assertNotIn("audit_log_path", success)
                path = resolve_audit_file_for_user(
                    success["audit_session_id"], "u1"
                )
                self.assertIsNotNone(path)
                self.assertTrue(os.path.isfile(path))
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                chain_stats = doc["icici_api_calls"]["by_api"]["get_option_chain_quotes"]
                self.assertGreaterEqual(chain_stats["total"], 2)
                self.assertLess(chain_stats["total"], 20)
                self.assertIn("temp_liquid_cache", doc)


class TestAuditRetention(unittest.TestCase):
    def _write_session(self, user_id: str, stock: str, tag: str) -> str:
        session = StrategyBuilderAuditSession(
            user_id=user_id,
            request={"stock_code": stock, "tag": tag},
        )
        session.record("test", tag)
        return session.finalize({"status": "ok", "tag": tag})

    def test_retention_keeps_last_ten_for_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                paths = []
                for i in range(11):
                    path = self._write_session("user-a", "NIFTY", f"run-{i}")
                    paths.append(path)
                    if i < 10:
                        time.sleep(0.01)
                remaining = list_audit_files_for_user("user-a")
                self.assertEqual(len(remaining), _MAX_AUDIT_LOGS_PER_USER)
                self.assertNotIn(paths[0], remaining)
                self.assertIn(paths[-1], remaining)

    def test_retention_does_not_delete_other_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                user_b_path = self._write_session("user-b", "BANKNIFTY", "b-only")
                for i in range(11):
                    self._write_session("user-a", "NIFTY", f"a-{i}")
                    time.sleep(0.01)
                self.assertEqual(len(list_audit_files_for_user("user-a")), 10)
                self.assertEqual(list_audit_files_for_user("user-b"), [user_b_path])

    def test_list_audit_files_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                first = self._write_session("user-a", "NIFTY", "first")
                time.sleep(0.02)
                second = self._write_session("user-a", "NIFTY", "second")
                ordered = list_audit_files_for_user("user-a")
                self.assertEqual(ordered, [second, first])

    def test_list_audit_log_index_includes_request_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="user-a",
                    request={
                        "stock_code": "NIFTY",
                        "expiry_date": "16-Jun-2026",
                        "margin_lacs": 500.0,
                        "max_loss_lacs": 10.0,
                        "min_pop_pct": 65.0,
                        "provision_elm": True,
                        "strategy_category": "income",
                        "risk_reward_profile": "moderate",
                    },
                )
                session.record("test", "inputs")
                session.strategy_evaluations = {"iron_condor": {"rejection_funnel": {}}}
                session.finalize({"status": "ok"})
                rows = list_audit_log_index_for_user("user-a")
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["stock_code"], "NIFTY")
                self.assertEqual(row["expiry_date"], "16-Jun-2026")
                self.assertEqual(row["margin_lacs"], 500.0)
                self.assertEqual(row["max_loss_lacs"], 10.0)
                self.assertEqual(row["min_pop_pct"], 65.0)
                self.assertTrue(row["provision_elm"])
                self.assertEqual(row["strategy_category"], "income")
                self.assertEqual(row["risk_reward_profile"], "moderate")
                self.assertTrue(row["explainability_available"])
                self.assertTrue(row["level_4_available"])

    def test_finalize_persists_explainability_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="user-a",
                    request={
                        "stock_code": "NIFTY",
                        "expiry_date": "16-Jun-2026",
                        "margin_lacs": 5.0,
                        "max_loss_lacs": 2.0,
                        "min_pop_pct": 65.0,
                        "strategy_category": "income",
                    },
                )
                user_report = build_user_explainability_report(
                    request=session.request,
                    strategy_evaluations={},
                    trades=[],
                    summary={"strategies_ok": [], "strategies_skipped": []},
                )
                session.finalize({"status": "ok"}, user_explainability=user_report)
                with open(session.file_path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                self.assertIn("user_explainability", doc)
                self.assertIn("explainability_levels", doc)
                self.assertIn("level_1", doc["explainability_levels"])
                self.assertIn("level_2", doc["explainability_levels"])
                self.assertIn("level_3", doc["explainability_levels"])

    def test_resolve_explainability_lazy_persists_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="user-a",
                    request={
                        "stock_code": "NIFTY",
                        "margin_lacs": 5.0,
                        "max_loss_lacs": 2.0,
                        "min_pop_pct": 65.0,
                        "strategy_category": "income",
                    },
                )
                session.strategy_evaluations = {
                    "iron_condor": {
                        "strategy_summary": {"generated": 1, "returned": 1},
                        "pop_policy": {"used_for_filtering": True, "ignored": False},
                        "rejection_funnel": {},
                        "winners": [],
                    }
                }
                session.finalize(
                    {
                        "status": "ok",
                        "strategies_ok": ["iron_condor"],
                        "strategies_skipped": [],
                    }
                )
                payload = resolve_explainability_for_session(
                    session.session_id,
                    "user-a",
                )
                self.assertIsNotNone(payload)
                assert payload is not None
                self.assertEqual(payload["session_id"], session.session_id)
                self.assertIn("level_1", payload)
                with open(session.file_path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                self.assertIn("explainability_levels", doc)

    def test_build_audit_zip_contains_expected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                p1 = self._write_session("user-a", "NIFTY", "one")
                p2 = self._write_session("user-a", "NIFTY", "two")
                payload, filename = build_audit_zip_for_user("user-a")
                self.assertTrue(filename.startswith("strategy-builder-audits-"))
                with zipfile.ZipFile(BytesIO(payload)) as zf:
                    names = set(zf.namelist())
                self.assertEqual(names, {os.path.basename(p1), os.path.basename(p2)})

    def test_enforce_retention_repair_when_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                for i in range(12):
                    self._write_session("user-a", "NIFTY", f"run-{i}")
                    time.sleep(0.01)
                enforce_audit_retention("user-a")
                self.assertEqual(len(list_audit_files_for_user("user-a")), 9)


class TestProcessorIciciAudit(unittest.TestCase):
    def test_baseline_only_margin_skips_icici_audit(self):
        from icici_breeze_backend.app.services.processor import processor

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="u1",
                    request={"stock_code": "NIFTY"},
                )
                proc = processor()
                legs = [
                    {
                        "stock_code": "NIFTY",
                        "exchange_code": "NFO",
                        "expiry_date": "09-Jun-2025",
                        "product_type": "Options",
                        "right": "Call",
                        "strike_price": "23500",
                        "quantity": "75",
                        "action": "Sell",
                    }
                ]
                mock_breeze = MagicMock()
                with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
                    proc,
                    "get_strategy_builder_margin_source",
                    return_value=MARGIN_SOURCE_EXCHANGE,
                ), patch(
                    "icici_breeze_backend.app.services.processor.resolve_exchange_baseline_margin",
                    return_value={"found": True, "span_margin_required": 50_000.0},
                ):
                    res = proc.strategy_builder_margin(
                        "u1",
                        "NFO",
                        legs,
                        audit=session,
                        audit_context={"strategy_id": "naked_ce_short"},
                    )
                self.assertEqual(res["Status"], 200)
                self.assertEqual(session.icici_api_call_stats["total"], 0)
                mock_breeze.margin_calculator.assert_not_called()

    def test_margin_calculator_recorded_on_breeze_path(self):
        from icici_breeze_backend.app.services.processor import processor

        session = StrategyBuilderAuditSession(user_id="u1", request={"stock_code": "NIFTY"})
        proc = processor()
        legs = [
            {
                "stock_code": "NIFTY",
                "exchange_code": "NFO",
                "expiry_date": "09-Jun-2025",
                "product_type": "Options",
                "right": "Call",
                "strike_price": "23500",
                "quantity": "75",
                "action": "Sell",
            }
        ]
        mock_breeze = MagicMock()
        mock_breeze.margin_calculator.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 100_000},
        }
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc,
            "get_strategy_builder_margin_source",
            return_value="breeze_api",
        ):
            proc.strategy_builder_margin("u1", "NFO", legs, audit=session)
        stats = session.icici_api_call_stats
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["by_api"]["margin_calculator"]["success"], 1)
        mock_breeze.margin_calculator.assert_called_once()


if __name__ == "__main__":
    unittest.main()
