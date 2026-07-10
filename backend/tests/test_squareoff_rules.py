"""Tests for the Portfolio > group > Exit Rule feature: persistence
(app.repositories.squareoff_rules), routes (route_squareoff_rules), and the
rule-hit -> broker order dispatcher (squareoff_dispatcher).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table
from icici_breeze_backend.app.domain.squareoff_rule import ArmSquareOffRuleRequest
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import squareoff_dispatcher


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    return path


def _arm(user_id="u1", stock_code="NIFTY", expiry_display="30-Jun-2026", target=100000.0, stop=20000.0):
    return repo.arm_rule(
        user_id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code="NFO",
        profit_target_pnl=target,
        loss_limit_pnl=stop,
    )


class TestRepository:
    def test_arm_creates_an_armed_rule(self, db_path):
        record = _arm()
        assert record.status == "armed"
        assert record.stock_code == "NIFTY"
        assert record.profit_target_pnl == pytest.approx(100000.0)

    def test_re_arming_same_group_updates_in_place_not_duplicates(self, db_path):
        first = _arm(target=100000.0)
        second = _arm(target=150000.0)
        assert first.id == second.id
        rules = repo.list_active_rules("u1")
        assert len(rules) == 1
        assert rules[0].profit_target_pnl == pytest.approx(150000.0)

    def test_list_active_rules_includes_fired_but_excludes_disarmed(self, db_path):
        armed = _arm(stock_code="NIFTY")
        fired = _arm(stock_code="BANKNIFTY")
        disarmed = _arm(stock_code="FINNIFTY")
        repo.mark_fired(fired.id, [])
        repo.disarm_rule("u1", disarmed.id)

        active_ids = {r.id for r in repo.list_active_rules("u1")}
        assert active_ids == {armed.id, fired.id}

    def test_list_active_rules_includes_fire_failed(self, db_path):
        rule = _arm()
        leg_result = {
            "scrip_key": "x",
            "stock_code": "NIFTY",
            "strike_price": "25000.0",
            "right": "Call",
            "quantity": "50",
            "status": "failed",
            "error": "RMS:Margin Exceeds",
        }
        repo.mark_fire_failed(rule.id, [leg_result])
        active = repo.list_active_rules("u1")
        assert len(active) == 1
        assert active[0].status == "fire_failed"
        assert active[0].leg_results[0].status == "failed"

    def test_disarm_succeeds_from_fired_too(self, db_path):
        rule = _arm()
        repo.mark_fired(rule.id, [])
        assert repo.disarm_rule("u1", rule.id) is True
        assert repo.get_rule(rule.id).status == "disarmed"

    def test_disarm_fails_once_already_disarmed(self, db_path):
        rule = _arm()
        assert repo.disarm_rule("u1", rule.id) is True
        assert repo.disarm_rule("u1", rule.id) is False  # already terminal

    def test_disarm_is_scoped_to_the_owning_user(self, db_path):
        rule = _arm(user_id="u1")
        assert repo.disarm_rule("someone-else", rule.id) is False
        assert repo.get_rule(rule.id).status == "armed"

    def test_list_all_armed_rules_spans_users(self, db_path):
        _arm(user_id="u1", stock_code="NIFTY")
        _arm(user_id="u2", stock_code="BANKNIFTY")
        rows = repo.list_all_armed_rules()
        assert {r["user_id"] for r in rows} == {"u1", "u2"}


def _ctx(user_id="u1"):
    from icici_breeze_backend.app.auth.context import RequestContext

    return RequestContext(user_id=user_id, username=user_id, roles=["trader"], is_authenticated=True, broker_token="tok")


class TestRoutes:
    def test_arm_route_persists_and_registers_with_engine(self, db_path, monkeypatch):
        from icici_breeze_backend.app.services import portfolio_pnl_engine as engine
        from icici_breeze_backend.app.api.v1 import route_squareoff_rules as route

        monkeypatch.setattr(engine, "_group_rules", {})
        body = ArmSquareOffRuleRequest(
            stock_code="nifty", expiry_date="2026-06-30T06:00:00.000Z", profit_target_pnl=100000, loss_limit_pnl=20000
        )
        record = asyncio.run(route.arm_rule(body, _ctx()))
        assert record.status == "armed"

        group_rules = engine._group_rules.get("u1", {})
        assert len(group_rules) == 1
        rule = next(iter(group_rules.values()))
        assert rule.target_pnl == pytest.approx(100000.0)
        assert rule.rule_id == record.id

    def test_list_route_returns_only_this_users_rules(self, db_path):
        from icici_breeze_backend.app.api.v1 import route_squareoff_rules as route

        _arm(user_id="u1")
        _arm(user_id="u2")
        resp = asyncio.run(route.list_rules(_ctx("u1")))
        assert len(resp.rules) == 1

    def test_disarm_route_clears_engine_rule(self, db_path, monkeypatch):
        from icici_breeze_backend.app.services import portfolio_pnl_engine as engine
        from icici_breeze_backend.app.api.v1 import route_squareoff_rules as route

        monkeypatch.setattr(engine, "_group_rules", {})
        rule = _arm(user_id="u1")
        engine.set_group_rule("u1", rule.id, stock_code="NIFTY", expiry_display="30-Jun-2026", target_pnl=100000.0)

        asyncio.run(route.disarm_rule(rule.id, _ctx("u1")))

        assert engine._group_rules.get("u1", {}) == {}
        assert repo.get_rule(rule.id).status == "disarmed"

    def test_disarm_route_404s_for_unknown_rule(self, db_path):
        from icici_breeze_backend.app.api.v1 import route_squareoff_rules as route

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(route.disarm_rule("does-not-exist", _ctx("u1")))
        assert exc_info.value.status_code == 404


class TestDispatcher:
    def _leg(self, **overrides):
        base = {
            "scrip_key": "NFO|NIFTY|30-Jun-2026|25000|CE",
            "product_type": "Options",
            "stock_code": "NIFTY",
            "exchange_code": "NFO",
            "expiry_display": "30-Jun-2026",
            "right": "Call",
            "strike_price": "25000.0",
            "quantity": "50",
            "action": "Sell",
            "pnl": 500.0,
        }
        base.update(overrides)
        return base

    def _payload(self, *, reason="group_target_hit", legs=None, rule_id="rule-1"):
        return {
            "user_id": "u1",
            "rule_id": rule_id,
            "reason": reason,
            "stock_code": "NIFTY",
            "expiry_display": "30-Jun-2026",
            "total_pnl": 500.0,
            "legs": legs if legs is not None else [self._leg()],
        }

    def test_ignores_non_group_reasons(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(squareoff_dispatcher, "trading_mutations_allowed", lambda: calls.append("checked") or True)
        squareoff_dispatcher._handle_group_rule_hit(self._payload(reason="target_hit"))
        assert calls == []  # per-leg/whole-portfolio tiers are unreachable dead code; must be a no-op

    def test_all_legs_succeed_marks_fired(self, monkeypatch):
        monkeypatch.setattr(squareoff_dispatcher, "trading_mutations_allowed", lambda: True)
        fired_calls = []
        monkeypatch.setattr(squareoff_dispatcher.repo, "mark_fired", lambda rid, results: fired_calls.append((rid, results)))
        monkeypatch.setattr(
            squareoff_dispatcher.repo,
            "mark_fire_failed",
            lambda rid, results: (_ for _ in ()).throw(AssertionError("should not fail")),
        )

        class _FakeBreeze:
            def place_order(self, **kwargs):
                return {"Status": 200, "Success": {"order_id": "abc"}}

        monkeypatch.setattr(squareoff_dispatcher, "processor", lambda: _FakeBreeze())

        squareoff_dispatcher._handle_group_rule_hit(self._payload())

        assert len(fired_calls) == 1
        rule_id, results = fired_calls[0]
        assert rule_id == "rule-1"
        assert results[0]["status"] == "success"

    def test_one_leg_fails_marks_fire_failed_with_per_leg_detail(self, monkeypatch):
        monkeypatch.setattr(squareoff_dispatcher, "trading_mutations_allowed", lambda: True)
        failed_calls = []
        monkeypatch.setattr(squareoff_dispatcher.repo, "mark_fired", lambda rid, results: (_ for _ in ()).throw(AssertionError))
        monkeypatch.setattr(squareoff_dispatcher.repo, "mark_fire_failed", lambda rid, results: failed_calls.append((rid, results)))

        responses = iter([{"Status": 200, "Success": {}}, {"Status": 400, "Error": "RMS:Margin Exceeds"}])

        class _FakeBreeze:
            def place_order(self, **kwargs):
                return next(responses)

        monkeypatch.setattr(squareoff_dispatcher, "processor", lambda: _FakeBreeze())

        legs = [self._leg(scrip_key="leg-1", strike_price="25000.0"), self._leg(scrip_key="leg-2", strike_price="24500.0")]
        squareoff_dispatcher._handle_group_rule_hit(self._payload(legs=legs))

        assert len(failed_calls) == 1
        _, results = failed_calls[0]
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "failed"
        assert "Margin Exceeds" in results[1]["error"]

    def test_one_leg_raising_does_not_abort_the_remaining_legs(self, monkeypatch):
        monkeypatch.setattr(squareoff_dispatcher, "trading_mutations_allowed", lambda: True)
        failed_calls = []
        monkeypatch.setattr(squareoff_dispatcher.repo, "mark_fire_failed", lambda rid, results: failed_calls.append((rid, results)))

        class _FakeBreeze:
            def place_order(self, **kwargs):
                if kwargs["strike_price"] == "25000.0":
                    raise RuntimeError("network blip")
                return {"Status": 200, "Success": {}}

        monkeypatch.setattr(squareoff_dispatcher, "processor", lambda: _FakeBreeze())

        legs = [self._leg(scrip_key="leg-1", strike_price="25000.0"), self._leg(scrip_key="leg-2", strike_price="24500.0")]
        squareoff_dispatcher._handle_group_rule_hit(self._payload(legs=legs))

        assert len(failed_calls) == 1
        _, results = failed_calls[0]
        assert len(results) == 2  # both legs attempted despite the first raising
        assert results[0]["status"] == "failed"
        assert results[1]["status"] == "success"

    def test_read_only_license_skips_order_placement_entirely(self, monkeypatch):
        monkeypatch.setattr(squareoff_dispatcher, "trading_mutations_allowed", lambda: False)
        failed_calls = []
        monkeypatch.setattr(squareoff_dispatcher.repo, "mark_fire_failed", lambda rid, results: failed_calls.append((rid, results)))

        def _unexpected_processor():
            raise AssertionError("place_order must not be reached when trading is read-only")

        monkeypatch.setattr(squareoff_dispatcher, "processor", _unexpected_processor)

        squareoff_dispatcher._handle_group_rule_hit(self._payload())

        assert len(failed_calls) == 1
        assert "read-only" in failed_calls[0][1][0]["error"].lower()
