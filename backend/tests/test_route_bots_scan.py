"""Bot 1 scan and approve routes (route_bots)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.api.v1 import route_bots
from icici_breeze_backend.app.api.v1.route_bots import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import ProposalLeg, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import holdings_writer as hw
from icici_breeze_backend.app.services.bots import placement

SCAN_URL = f"/bots/scan?bot_type={BOT_HOLDINGS_WRITER}"
APPROVE_URL = f"/bots/proposal/approve?bot_type={BOT_HOLDINGS_WRITER}"


def make_leg(stock_code="NTPC", right="call", strike=350.0, premium=4.25, lots=3,
             basis="bid"):
    return ProposalLeg(
        premium_basis=basis,
        stock_code=stock_code, right=right, expiry_display="24-Sep-2026",
        strike_price=strike, lots=lots, lot_size=1500, quantity=1500 * lots,
        premium_per_share=premium, premium_total=premium * 1500 * lots,
        span_margin=25000.0, elm_margin=5000.0,
        delivery_exposure=(strike * 1500 * lots) if right == "put" else None,
        selected=right == "call",
    )


class FakeProc:
    def get_strategy_builder_margin_source(self, user_id):
        return "breeze_api"


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.processor", lambda *a, **k: FakeProc()
    )

    async def _ctx():
        return RequestContext(user_id="user1", username="user1", roles=["trader"],
                              is_authenticated=True, broker_token=None)

    app = FastAPI()
    app.include_router(router, prefix="/bots")
    app.dependency_overrides[get_request_context] = _ctx
    app.dependency_overrides[require_trading_not_revoked] = lambda: None
    with TestClient(app) as c:
        yield c


def stub_scan(monkeypatch, legs, skipped=()):
    def fake(proc, user_id, *, config, prefs, margin_source):
        return hw.ScanResult(
            legs=list(legs),
            skipped=[hw.SkippedScrip(*s) for s in skipped],
            totals={"leg_count": len(legs)},
        )

    monkeypatch.setattr(hw, "scan", fake)


def stub_place(monkeypatch, results):
    captured = {}

    def fake(proc, user_id, legs, *, tolerance_pct):
        captured["legs"] = legs
        return results

    monkeypatch.setattr(placement, "place_short_legs", fake)
    return captured


def ok_result(stock_code="NTPC", right="call", strike=350.0, qty=4500):
    return placement.PlacementResult(
        stock_code=stock_code, right=right, strike_price=strike,
        expiry_display="24-Sep-2026", quantity=qty, limit_price=4.0, order_ids=["OID1"],
    )


# --- scan ----------------------------------------------------------------------------


def test_scan_creates_a_proposal_and_logs_the_run(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg()], [("IRCTC", "not_fno_eligible", "No F&O contracts.")])
    body = client.post(SCAN_URL).json()

    assert body["proposal"]["legs"][0]["stock_code"] == "NTPC"
    assert body["skipped"] == [
        {"stock_code": "IRCTC", "reason_code": "not_fno_eligible", "reason": "No F&O contracts."}
    ]
    run = repo.list_runs("user1")[0]
    assert run.status == "proposed"
    assert run.reason_code == ReasonCode.PROPOSAL_READY
    # The skipped holdings are kept on the run, so the log explains a thin scan later.
    assert run.detail["skipped"][0]["reason_code"] == "not_fno_eligible"


def test_scan_with_nothing_eligible_records_a_skip_not_a_failure(client, monkeypatch):
    stub_scan(monkeypatch, [], [("HINPET", "below_one_lot", "Under one lot.")])
    body = client.post(SCAN_URL).json()

    assert body["proposal"] is None
    run = repo.list_runs("user1")[0]
    assert run.status == "skipped"
    assert run.reason_code == ReasonCode.NOTHING_ELIGIBLE
    assert repo.get_pending_proposal("user1", BOT_HOLDINGS_WRITER) is None


def test_broker_failure_during_scan_is_502_and_logged_as_failed(client, monkeypatch):
    def boom(*a, **k):
        raise hw.BotScanError("Broker down")

    monkeypatch.setattr(hw, "scan", boom)
    r = client.post(SCAN_URL)
    assert r.status_code == 502
    run = repo.list_runs("user1")[0]
    assert run.status == "failed" and run.reason_code == ReasonCode.BROKER_ERROR


def test_unexpected_scan_error_is_500_and_logged(client, monkeypatch):
    def boom(*a, **k):
        raise ValueError("bug")

    monkeypatch.setattr(hw, "scan", boom)
    assert client.post(SCAN_URL).status_code == 500
    assert repo.list_runs("user1")[0].reason_code == ReasonCode.INTERNAL_ERROR


def test_bot_2_does_not_support_manual_scans(client):
    r = client.post(f"/bots/scan?bot_type={BOT_EXPIRY_INDEX_WRITER}")
    assert r.status_code == 400


def test_rescanning_supersedes_the_previous_proposal(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg()])
    first = client.post(SCAN_URL).json()["proposal"]["id"]
    second = client.post(SCAN_URL).json()["proposal"]["id"]
    assert first != second
    assert repo.get_proposal("user1", first).status == "superseded"


# --- approve -------------------------------------------------------------------------


def test_approve_places_only_the_selected_legs(client, monkeypatch):
    call, put = make_leg(), make_leg(stock_code="GAIL", right="put", strike=160.0)
    stub_scan(monkeypatch, [call, put])
    client.post(SCAN_URL)
    captured = stub_place(monkeypatch, [ok_result()])

    r = client.post(APPROVE_URL, json={"leg_indexes": [0]})
    assert r.status_code == 200
    assert [leg["stock_code"] for leg in captured["legs"]] == ["NTPC"]
    assert r.json()["all_succeeded"] is True


def test_approve_can_select_the_put_leg_the_scan_left_unselected(client, monkeypatch):
    """Delivery-cash allocation is expressed by which legs the user keeps."""
    call, put = make_leg(), make_leg(stock_code="GAIL", right="put", strike=160.0)
    stub_scan(monkeypatch, [call, put])
    client.post(SCAN_URL)
    captured = stub_place(monkeypatch, [ok_result("GAIL", "put", 160.0)])

    client.post(APPROVE_URL, json={"leg_indexes": [1]})
    assert [leg["stock_code"] for leg in captured["legs"]] == ["GAIL"]


def test_approve_marks_the_proposal_placed_and_logs_the_run(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg()])
    proposal_id = client.post(SCAN_URL).json()["proposal"]["id"]
    stub_place(monkeypatch, [ok_result()])

    client.post(APPROVE_URL, json={"leg_indexes": [0]})
    assert repo.get_proposal("user1", proposal_id).status == "placed"
    run = repo.list_runs("user1")[0]
    assert run.status == "completed" and run.reason_code == ReasonCode.ORDERS_PLACED


def test_a_rejected_leg_makes_the_run_a_failure_with_detail(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg()])
    client.post(SCAN_URL)
    bad = ok_result()
    bad.order_ids = []
    bad.error = "Insufficient margin"
    stub_place(monkeypatch, [bad])

    body = client.post(APPROVE_URL, json={"leg_indexes": [0]}).json()
    assert body["all_succeeded"] is False
    assert body["placed"][0]["error"] == "Insufficient margin"
    run = repo.list_runs("user1")[0]
    assert run.status == "failed" and run.reason_code == ReasonCode.ORDER_REJECTED


def test_material_price_drift_blocks_placement_and_re_proposes(client, monkeypatch):
    """The proposal is a priced snapshot; a collapsed bid must not fill silently."""
    stub_scan(monkeypatch, [make_leg(premium=4.25)])
    client.post(SCAN_URL)
    stub_scan(monkeypatch, [make_leg(premium=2.00)])  # bid fell 53%
    captured = stub_place(monkeypatch, [ok_result()])

    r = client.post(APPROVE_URL, json={"leg_indexes": [0]})
    assert r.status_code == 409
    assert "Prices moved" in r.json()["detail"]
    assert "legs" not in captured, "nothing may be placed on a drifted proposal"
    # A fresh proposal is waiting, so the user re-approves against live prices.
    assert repo.get_pending_proposal("user1", BOT_HOLDINGS_WRITER) is not None


def test_a_bid_that_improved_does_not_block_placement(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg(premium=4.25)])
    client.post(SCAN_URL)
    stub_scan(monkeypatch, [make_leg(premium=9.00)])
    stub_place(monkeypatch, [ok_result()])
    assert client.post(APPROVE_URL, json={"leg_indexes": [0]}).status_code == 200


def test_indicative_pricing_blocks_placement(client, monkeypatch):
    """Off-market there is no order book, so an indicative premium must never be sold into."""
    stub_scan(monkeypatch, [make_leg(basis="ltp_indicative")])
    client.post(SCAN_URL)
    captured = stub_place(monkeypatch, [ok_result()])

    r = client.post(APPROVE_URL, json={"leg_indexes": [0]})
    assert r.status_code == 409
    assert "No live bid" in r.json()["detail"]
    assert "legs" not in captured


def test_a_leg_priced_off_a_real_bid_places_normally(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg(basis="bid")])
    client.post(SCAN_URL)
    stub_place(monkeypatch, [ok_result()])
    assert client.post(APPROVE_URL, json={"leg_indexes": [0]}).status_code == 200


def test_a_leg_that_vanished_blocks_placement(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg()])
    client.post(SCAN_URL)
    stub_scan(monkeypatch, [])
    r = client.post(APPROVE_URL, json={"leg_indexes": [0]})
    assert r.status_code == 409
    assert "no longer available" in r.json()["detail"]


def test_approving_with_no_pending_proposal_is_404(client):
    assert client.post(APPROVE_URL, json={"leg_indexes": [0]}).status_code == 404


def test_approving_nothing_is_400(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg()])
    client.post(SCAN_URL)
    assert client.post(APPROVE_URL, json={"leg_indexes": []}).status_code == 400


def test_approving_an_unknown_leg_index_is_400(client, monkeypatch):
    stub_scan(monkeypatch, [make_leg()])
    client.post(SCAN_URL)
    assert client.post(APPROVE_URL, json={"leg_indexes": [7]}).status_code == 400


def test_scan_and_approve_are_blocked_in_read_only_mode(tmp_path, monkeypatch):
    from fastapi import HTTPException

    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)

    async def _ctx():
        return RequestContext(user_id="user1", username="user1", roles=["trader"],
                              is_authenticated=True, broker_token=None)

    def _revoked():
        raise HTTPException(status_code=403, detail="Read-only mode")

    app = FastAPI()
    app.include_router(router, prefix="/bots")
    app.dependency_overrides[get_request_context] = _ctx
    app.dependency_overrides[require_trading_not_revoked] = _revoked
    with TestClient(app) as c:
        assert c.post(SCAN_URL).status_code == 403
        assert c.post(APPROVE_URL, json={"leg_indexes": [0]}).status_code == 403
