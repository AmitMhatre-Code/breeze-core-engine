"""Strategy Group state machine.

The load-bearing case is self-fill vs manual intervention: when a Fired SG's own exit
orders fill, legs vanish, and a naive reading of "legs changed -> Reset" would reset the
SG at the exact moment it should reach Completed. Order identity is what separates the
two, so it gets the most coverage here.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import strategy_group_lifecycle as sg
from icici_breeze_backend.app.services.order_notifications import parse_order_notification
from tests.fixtures.order_notifications import ORDER_PLACED, executed, with_status

SCRIP = "NFO|NIFTY|21-Jul-2026|26000|call"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    return path


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """Silence the WS pin and Telegram — neither has a broker session here."""
    monkeypatch.setattr(sg, "release_subscription", lambda *a, **k: None)
    monkeypatch.setattr(sg, "_release_subscription", lambda *a, **k: None)
    import icici_breeze_backend.app.services.telegram_alerts as tg

    monkeypatch.setattr(tg, "notify_squareoff_reset", lambda *a, **k: None)


def _arm(user_id="VIKRAMMH", stock="NIFTY", expiry="21-Jul-2026", snapshot=None):
    return repo.arm_rule(
        user_id,
        stock_code=stock,
        expiry_display=expiry,
        exchange_code="NFO",
        profit_target_pnl=100000.0,
        loss_limit_pnl=20000.0,
        target_premium_pct=10,
        stop_loss_premium_pct=5,
        legs_snapshot=snapshot or {SCRIP: 130},
    )


def _fire(rule, order_id="202607173800017846", scrip_key=SCRIP):
    repo.mark_fired(
        rule.id,
        [{
            "scrip_key": scrip_key,
            "stock_code": "NIFTY",
            "strike_price": "26000",
            "right": "Call",
            "quantity": "130",
            "status": "success",
            "order_ids": [order_id],
            "action": "Buy",
            "price": "2.80",
        }],
    )
    return repo.get_rule(rule.id)


def _no_open_legs(monkeypatch):
    monkeypatch.setattr(sg.portfolio_pnl_engine, "group_legs_for_user", lambda *a, **k: [])


def _stub_order_book(monkeypatch, statuses: dict[str, str]):
    """statuses: order_id -> broker status."""
    class _Breeze:
        def get_orders(self, user_id, start=None, end=None):
            return {
                "Status": 200,
                "Success": [
                    {"order_id": oid, "status": st} for oid, st in statuses.items()
                ],
            }

    import icici_breeze_backend.app.services.processor as proc_mod

    monkeypatch.setattr(proc_mod, "processor", lambda: _Breeze())
    return _Breeze()


class TestOwnExitOrders:
    def test_our_exit_executing_completes_the_sg_not_resets_it(self, db_path, monkeypatch):
        """The section 6 / section 9 collision: our own fill empties the group, which must
        drive Completed rather than looking like the user closing a leg."""
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)
        _stub_order_book(monkeypatch, {"202607173800017846": "Executed"})

        sg.on_order_notification(parse_order_notification(executed()))

        assert repo.get_rule(rule.id).status == "completed"

    def test_stays_fired_while_our_exit_is_still_resting(self, db_path, monkeypatch):
        """Fired is a patient state — a resting exit order must not resolve the SG."""
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)
        sg.on_order_notification(parse_order_notification(ORDER_PLACED))  # 'Ordered'
        assert repo.get_rule(rule.id).status == "fired"

    def test_not_completed_while_legs_remain_open(self, db_path, monkeypatch):
        """Section 7 needs BOTH: every order executed AND no open positions."""
        rule = _fire(_arm())

        class _Leg:
            scrip_key = SCRIP
            quantity = 130

        monkeypatch.setattr(
            sg.portfolio_pnl_engine, "group_legs_for_user", lambda *a, **k: [_Leg()]
        )
        _stub_order_book(monkeypatch, {"202607173800017846": "Executed"})

        sg.on_order_notification(parse_order_notification(executed()))
        assert repo.get_rule(rule.id).status == "fired"

    @pytest.mark.parametrize(
        "status", ["Cancelled", "Rejected", "Expired",
                   "Partially Executed And Cancelled", "Partially Executed And Expired"]
    )
    def test_our_exit_failing_resets_with_a_reason(self, db_path, monkeypatch, status):
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)

        sg.on_order_notification(parse_order_notification(with_status(status)))

        after = repo.get_rule(rule.id)
        assert after.status == "reset"
        assert "NIFTY 26000 CE" in after.reset_reason  # names the leg, in the user's terms

    def test_not_completed_when_one_of_two_exits_is_still_working(self, db_path, monkeypatch):
        rule = _arm()
        repo.mark_fired(rule.id, [
            {"scrip_key": SCRIP, "stock_code": "NIFTY", "strike_price": "26000",
             "right": "Call", "quantity": "130", "status": "success",
             "order_ids": ["202607173800017846"], "action": "Buy", "price": "2.80"},
            {"scrip_key": "other", "stock_code": "NIFTY", "strike_price": "25800",
             "right": "Put", "quantity": "130", "status": "success",
             "order_ids": ["999"], "action": "Buy", "price": "1.10"},
        ])
        _no_open_legs(monkeypatch)
        _stub_order_book(monkeypatch, {"202607173800017846": "Executed", "999": "Ordered"})

        sg.on_order_notification(parse_order_notification(executed()))
        assert repo.get_rule(rule.id).status == "fired"


class TestFiredReconcile:
    """The REST backstop: `reconcile_fired_rules_from_orders` completes a fired SG off an
    already-fetched order book when a live WS fill event was missed."""

    def test_completes_when_book_shows_all_executed(self, db_path, monkeypatch):
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)
        sg.reconcile_fired_rules_from_orders(
            "VIKRAMMH", [{"order_id": "202607173800017846", "status": "Executed"}]
        )
        assert repo.get_rule(rule.id).status == "completed"

    def test_stays_fired_when_an_order_is_not_executed(self, db_path, monkeypatch):
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)
        sg.reconcile_fired_rules_from_orders(
            "VIKRAMMH", [{"order_id": "202607173800017846", "status": "Ordered"}]
        )
        assert repo.get_rule(rule.id).status == "fired"

    def test_stays_fired_when_legs_remain_open(self, db_path, monkeypatch):
        """The open-leg guard is what keeps a missed foreign-fill (leftover leg) from
        producing a false Completed — the fail-safe is to stay Fired, never guess Reset."""
        rule = _fire(_arm())

        class _Leg:
            scrip_key = SCRIP
            quantity = 130

        monkeypatch.setattr(
            sg.portfolio_pnl_engine, "group_legs_for_user", lambda *a, **k: [_Leg()]
        )
        sg.reconcile_fired_rules_from_orders(
            "VIKRAMMH", [{"order_id": "202607173800017846", "status": "Executed"}]
        )
        assert repo.get_rule(rule.id).status == "fired"

    def test_stays_fired_when_order_absent_from_book(self, db_path, monkeypatch):
        """An order_id missing from the fetched book must not complete — same fail-safe as
        a fresh fetch that couldn't confirm every order."""
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)
        sg.reconcile_fired_rules_from_orders(
            "VIKRAMMH", [{"order_id": "SOMETHING-ELSE", "status": "Executed"}]
        )
        assert repo.get_rule(rule.id).status == "fired"

    def test_does_not_touch_non_fired_rules(self, db_path, monkeypatch):
        rule = _arm()  # armed, not fired
        _no_open_legs(monkeypatch)
        sg.reconcile_fired_rules_from_orders(
            "VIKRAMMH", [{"order_id": "202607173800017846", "status": "Executed"}]
        )
        assert repo.get_rule(rule.id).status == "armed"

    def test_reuses_the_supplied_book_without_a_second_get_orders(self, db_path, monkeypatch):
        """Reconcile off the caller's book must not issue its own ICICI round-trip."""
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)
        calls = {"n": 0}

        class _Breeze:
            def get_orders(self, *a, **k):
                calls["n"] += 1
                return {"Status": 200, "Success": []}

        import icici_breeze_backend.app.services.processor as proc_mod

        monkeypatch.setattr(proc_mod, "processor", lambda: _Breeze())
        sg.reconcile_fired_rules_from_orders(
            "VIKRAMMH", [{"order_id": "202607173800017846", "status": "Executed"}]
        )
        assert calls["n"] == 0
        assert repo.get_rule(rule.id).status == "completed"


class TestForeignOrders:
    def test_a_manual_fill_resets_the_sg(self, db_path, monkeypatch):
        """Spec section 10 — an order we did not place moved the position."""
        rule = _fire(_arm(), order_id="OUR-ORDER")
        _no_open_legs(monkeypatch)

        sg.on_order_notification(parse_order_notification(executed()))  # different id

        after = repo.get_rule(rule.id)
        assert after.status == "reset"
        assert "traded" in after.reset_reason

    def test_a_resting_manual_order_does_not_reset(self, db_path, monkeypatch):
        """A merely resting manual order changes no composition until it fills — tearing
        down protection the user is relying on because they *placed* an order would be
        wrong."""
        rule = _fire(_arm(), order_id="OUR-ORDER")
        _no_open_legs(monkeypatch)

        sg.on_order_notification(parse_order_notification(ORDER_PLACED))  # Ordered, not ours

        assert repo.get_rule(rule.id).status == "fired"

    def test_orders_for_another_group_are_ignored(self, db_path, monkeypatch):
        rule = _fire(_arm(stock="BANKNIFTY"))
        _no_open_legs(monkeypatch)
        sg.on_order_notification(parse_order_notification(executed()))  # NIFTY
        assert repo.get_rule(rule.id).status == "fired"

    def test_no_live_sg_is_a_no_op(self, db_path, monkeypatch):
        _no_open_legs(monkeypatch)
        sg.on_order_notification(parse_order_notification(executed()))  # must not raise


class TestDrift:
    def test_added_leg_while_armed_drifts(self, db_path):
        assert sg.describe_drift({SCRIP: 130}, {SCRIP: 130, "new": 65}) is not None

    def test_removed_leg_while_armed_drifts(self, db_path):
        assert sg.describe_drift({SCRIP: 130}, {}) is not None

    def test_quantity_change_alone_drifts(self, db_path):
        """Any qty change invalidates: the thresholds were set against a specific
        exposure, so a partial add/reduce makes them mean something never agreed to."""
        d = sg.describe_drift({SCRIP: 130}, {SCRIP: 65})
        assert d is not None
        assert "quantity" in d

    def test_identical_composition_does_not_drift(self, db_path):
        assert sg.describe_drift({SCRIP: 130}, {SCRIP: 130}) is None

    def test_fired_sg_is_never_drift_checked(self, db_path, monkeypatch):
        """A fired SG's own exits change legs by design; a position diff can't attribute a
        fill, so drift-checking it would reset every successful square-off."""
        rule = _fire(_arm())
        monkeypatch.setattr(sg.portfolio_pnl_engine, "group_legs_for_user", lambda *a, **k: [])
        assert sg.check_armed_drift("VIKRAMMH", repo.get_rule(rule.id)) is None

    def test_cold_registry_does_not_spuriously_reset(self, db_path, monkeypatch):
        """An unwarmed registry looks identical to "everything closed". Staying silent is
        fail-safe: a spurious Reset would tear down real protection."""
        rule = _arm()
        monkeypatch.setattr(sg.portfolio_pnl_engine, "group_legs_for_user", lambda *a, **k: [])
        assert sg.check_armed_drift("VIKRAMMH", repo.get_rule(rule.id)) is None


class TestHazardTier:
    def _orphan(self, contra: bool):
        from icici_breeze_backend.app.domain.squareoff_rule import SquareOffRuleOrphanOrder

        return SquareOffRuleOrphanOrder(
            order_id="1", stock_code="NIFTY", strike_price="26000", right="Call",
            action="Buy", quantity="130", opens_contra_position=contra,
        )

    def test_no_orphans_is_settled(self):
        assert sg.hazard_tier([]) == "settled"

    def test_live_orphans_closing_real_legs_is_orders_live(self):
        assert sg.hazard_tier([self._orphan(False)]) == "orders_live"

    def test_any_contra_orphan_escalates_to_contra_risk(self):
        assert sg.hazard_tier([self._orphan(False), self._orphan(True)]) == "contra_risk"


class TestResetSemantics:
    def test_reset_does_not_cancel_outstanding_orders(self, db_path, monkeypatch):
        """Reset withdraws future automation; it does not retract orders already placed.
        Auto-cancelling would, in the stop-loss case, kill working exits and leave the user
        unprotected in a moving market because an unrelated leg failed."""
        cancels: list = []

        class _Breeze:
            def cancel_order_single(self, *a, **k):
                cancels.append(a)
                return {"success": True}

        monkeypatch.setattr(
            "icici_breeze_backend.app.services.processor.processor", lambda: _Breeze()
        )
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)

        sg.on_order_notification(parse_order_notification(with_status("Rejected")))

        assert repo.get_rule(rule.id).status == "reset"
        assert cancels == []  # nothing was cancelled on our behalf

    def test_reset_frees_the_key_for_a_new_sg(self, db_path, monkeypatch):
        """Spec section 11 — re-arming after a Reset creates a NEW SG, never reactivates
        the old one."""
        rule = _fire(_arm())
        _no_open_legs(monkeypatch)
        sg.on_order_notification(parse_order_notification(with_status("Cancelled")))

        fresh = _arm()
        assert fresh.id != rule.id
        assert fresh.status == "armed"
        assert repo.get_rule(rule.id).status == "reset"
