"""Order-book read budget for the SG surfaces.

These assert *call counts*, not just behaviour. The defect being fixed was not wrong
output — the hazard banner rendered correctly all along — it was that rendering it cost
4236 of 4730 daily broker calls on 2026-07-31. A behavioural test would have passed
throughout. Only counting catches a regression here.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import order_book_cache
from icici_breeze_backend.app.services import strategy_group_lifecycle as sg

USER = "VIKRAMMH"
EXPIRY = "21-Jul-2026"
SCRIP = f"NFO|NIFTY|{EXPIRY}|26000|call"


class FakeBreeze:
    """Counts `get_orders` calls and records the exchange scoping it was asked for."""

    def __init__(self, rows=None):
        self.calls: list[dict] = []
        self.rows = rows if rows is not None else []

    def get_orders(self, user_id, start, end, *, exchange_codes=None):
        self.calls.append({"start": start, "end": end, "exchange_codes": exchange_codes})
        return {"Status": 200, "Success": list(self.rows), "Error": None}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    return path


@pytest.fixture(autouse=True)
def _clean_cache():
    order_book_cache.clear()
    yield
    order_book_cache.clear()


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr(sg, "release_subscription", lambda *a, **k: None)
    monkeypatch.setattr(sg, "_release_subscription", lambda *a, **k: None)


def _reset_rule(stock="NIFTY", order_id="ORD1", exchange="NFO"):
    rule = repo.arm_rule(
        USER,
        stock_code=stock,
        expiry_display=EXPIRY,
        exchange_code=exchange,
        profit_target_pnl=100000.0,
        loss_limit_pnl=20000.0,
        target_premium_pct=10,
        stop_loss_premium_pct=5,
        legs_snapshot={SCRIP: 130},
    )
    repo.mark_fired(
        rule.id,
        [
            {
                "scrip_key": SCRIP,
                "stock_code": stock,
                "strike_price": "26000",
                "right": "Call",
                "quantity": "130",
                "action": "Buy",
                "status": "success",
                "price": "1.75",
                "order_ids": [order_id],
            }
        ],
    )
    repo.mark_reset(rule.id, "test")
    return repo.get_rule(rule.id)


# ------------------------------------------------------------------ cache behaviour


def test_repeated_reads_within_ttl_cost_one_fetch(db_path):
    breeze = FakeBreeze()
    rule = _reset_rule()
    for _ in range(20):
        sg.attach_reset_details(USER, breeze, [rule])
    assert len(breeze.calls) == 1, (
        f"20 polls should share one cached book, made {len(breeze.calls)} broker calls"
    )


def test_errors_are_not_cached(db_path):
    """Caching a failure would pin one transient broker error in place for the whole TTL
    and make every SG surface look broken."""
    calls = []

    def fetch():
        calls.append(1)
        return {"Status": 500, "Error": "boom"}

    for _ in range(3):
        order_book_cache.get_or_fetch(USER, ("2026-07-31", "2026-08-03"), ["NFO"], fetch)
    assert len(calls) == 3


def test_invalidation_forces_a_refetch(db_path):
    breeze = FakeBreeze()
    rule = _reset_rule()
    sg.attach_reset_details(USER, breeze, [rule])
    order_book_cache.invalidate_user(USER)
    sg.attach_reset_details(USER, breeze, [rule])
    assert len(breeze.calls) == 2


def test_invalidation_is_scoped_to_one_user(db_path):
    fetches = []
    order_book_cache.get_or_fetch(
        "A", ("d1", "d2"), ["NFO"], lambda: fetches.append("A") or {"Status": 200}
    )
    order_book_cache.get_or_fetch(
        "B", ("d1", "d2"), ["NFO"], lambda: fetches.append("B") or {"Status": 200}
    )
    order_book_cache.invalidate_user("A")
    order_book_cache.get_or_fetch(
        "A", ("d1", "d2"), ["NFO"], lambda: fetches.append("A") or {"Status": 200}
    )
    order_book_cache.get_or_fetch(
        "B", ("d1", "d2"), ["NFO"], lambda: fetches.append("B") or {"Status": 200}
    )
    assert fetches == ["A", "B", "A"], "B's entry must survive A's invalidation"


def test_scoped_read_does_not_satisfy_an_unscoped_one(db_path):
    """An NFO-only book answering a question about BFO orders would return 'not found',
    which the caller reads as 'not live' — the dangerous direction."""
    fetches = []

    def fetch():
        fetches.append(1)
        return {"Status": 200, "Success": []}

    order_book_cache.get_or_fetch(USER, ("d1", "d2"), ["NFO"], fetch)
    order_book_cache.get_or_fetch(USER, ("d1", "d2"), ["ALL"], fetch)
    assert len(fetches) == 2


# ------------------------------------------------------------------ the N+1


def test_many_reset_rules_still_read_the_book_once(db_path):
    breeze = FakeBreeze()
    rules = [
        _reset_rule(stock=s, order_id=f"ORD{i}")
        for i, s in enumerate(["NIFTY", "BANKNIFTY", "FINNIFTY"])
    ]
    sg.attach_reset_details(USER, breeze, rules)
    assert len(breeze.calls) == 1, (
        f"3 reset rules made {len(breeze.calls)} broker calls — the N+1 is back"
    )


def test_no_reset_rules_makes_no_broker_call(db_path):
    breeze = FakeBreeze()
    rule = _reset_rule()
    rule.status = "armed"
    sg.attach_reset_details(USER, breeze, [rule])
    assert breeze.calls == []


# ------------------------------------------------------------------ exchange scoping


def test_read_is_scoped_to_the_rules_own_exchange(db_path):
    """Each exchange is a separate `get_order_list`; querying BFO for an NFO-only SG
    doubled the cost of every poll."""
    breeze = FakeBreeze()
    rule = _reset_rule(exchange="NFO")
    sg.attach_reset_details(USER, breeze, [rule])
    assert breeze.calls[0]["exchange_codes"] == ["NFO"]


def test_mixed_exchanges_query_the_union(db_path):
    breeze = FakeBreeze()
    rules = [
        _reset_rule(stock="NIFTY", order_id="ORD1", exchange="NFO"),
        _reset_rule(stock="SENSEX", order_id="ORD2", exchange="BFO"),
    ]
    sg.attach_reset_details(USER, breeze, rules)
    assert breeze.calls[0]["exchange_codes"] == ["BFO", "NFO"]


# ------------------------------------------------------- stale reset rules cost nothing


def _backdate_fired_at(rule_id: str, date_str: str) -> None:
    import sqlite3

    with sqlite3.connect(repo._db_path()) as conn:
        conn.execute(
            "UPDATE portfolio_squareoff_rules SET fired_at = ? WHERE id = ?",
            (f"{date_str} 14:20:00", rule_id),
        )
        conn.commit()


def test_reset_rule_from_an_earlier_day_makes_no_broker_call(db_path):
    """The 2026-08-03 incident: one `reset` rule left over from the previous Friday
    exhausted a 5000-call daily quota in ~2 hours. Its exit orders were `validity="day"`
    and had expired on Friday, so every one of those calls was spent rediscovering that
    there was nothing to find."""
    breeze = FakeBreeze()
    rule = _reset_rule()
    _backdate_fired_at(rule.id, "2026-07-31")
    fetched = repo.get_rule(rule.id)

    for _ in range(50):
        sg.attach_reset_details(USER, breeze, [fetched])

    assert breeze.calls == [], (
        f"a stale reset rule cost {len(breeze.calls)} broker calls to learn nothing"
    )
    assert fetched.orphan_orders == []
    assert fetched.rearm_blocked is False


def test_todays_reset_rule_is_still_read(db_path):
    from icici_breeze_backend.app.core.timezone import today_ist_date

    breeze = FakeBreeze()
    rule = _reset_rule()
    _backdate_fired_at(rule.id, today_ist_date().isoformat())
    sg.attach_reset_details(USER, breeze, [repo.get_rule(rule.id)])
    assert len(breeze.calls) == 1


def test_unparseable_fired_at_still_reads(db_path):
    """Fail-safe: reporting 'settled' when we cannot tell would clear `rearm_blocked` and
    let a new SG stack on top of an order that is still live."""
    breeze = FakeBreeze()
    rule = _reset_rule()
    _backdate_fired_at(rule.id, "not-a-date")
    sg.attach_reset_details(USER, breeze, [repo.get_rule(rule.id)])
    assert len(breeze.calls) == 1


def test_stale_and_fresh_rules_together_read_once_for_the_fresh_one(db_path):
    from icici_breeze_backend.app.core.timezone import today_ist_date

    breeze = FakeBreeze()
    stale = _reset_rule(stock="NIFTY", order_id="OLD")
    fresh = _reset_rule(stock="BANKNIFTY", order_id="NEW")
    _backdate_fired_at(stale.id, "2026-07-31")
    _backdate_fired_at(fresh.id, today_ist_date().isoformat())
    sg.attach_reset_details(
        USER, breeze, [repo.get_rule(stale.id), repo.get_rule(fresh.id)]
    )
    assert len(breeze.calls) == 1


# ------------------------------------------------------------------ correctness kept


def test_live_orphan_is_still_detected_through_the_cache(db_path):
    breeze = FakeBreeze(
        rows=[{"order_id": "ORD1", "status": "Ordered", "exchange_code": "NFO"}]
    )
    rule = _reset_rule(order_id="ORD1")
    sg.attach_reset_details(USER, breeze, [rule])
    assert [o.order_id for o in rule.orphan_orders] == ["ORD1"]
    assert rule.rearm_blocked is True
    assert rule.hazard_tier in ("orders_live", "contra_risk")


def test_executed_order_is_not_an_orphan(db_path):
    breeze = FakeBreeze(
        rows=[{"order_id": "ORD1", "status": "Executed", "exchange_code": "NFO"}]
    )
    rule = _reset_rule(order_id="ORD1")
    sg.attach_reset_details(USER, breeze, [rule])
    assert rule.orphan_orders == []
    assert rule.rearm_blocked is False
    assert rule.hazard_tier == "settled"
