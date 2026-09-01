"""Routes for the Bots section (route_bots)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.api.v1.route_bots import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import ProposalLeg, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)

    async def _ctx():
        return RequestContext(
            user_id="user1",
            username="user1",
            roles=["trader"],
            is_authenticated=True,
            broker_token=None,
        )

    app = FastAPI()
    app.include_router(router, prefix="/bots")
    app.dependency_overrides[get_request_context] = _ctx
    app.dependency_overrides[require_trading_not_revoked] = lambda: None
    with TestClient(app) as c:
        yield c


def test_list_returns_both_bots_disabled(client):
    r = client.get("/bots/list")
    assert r.status_code == 200
    body = r.json()
    assert {b["bot_type"] for b in body} == {BOT_HOLDINGS_WRITER, BOT_EXPIRY_INDEX_WRITER}
    assert all(b["enabled"] is False for b in body)


def test_unknown_bot_type_is_404(client):
    assert client.get("/bots/config?bot_type=not_a_bot").status_code == 404
    assert client.patch("/bots/config?bot_type=not_a_bot", json={"enabled": True}).status_code == 404


def test_runs_route_is_reachable(client):
    """Static paths keep the API namespace disjoint from the `/bots` page namespace."""
    r = client.get("/bots/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_scrip_prefs_route_is_reachable(client):
    r = client.get("/bots/scrip-prefs")
    assert r.status_code == 200
    assert r.json() == []


def test_enable_and_configure_a_bot(client):
    r = client.patch(
        f"/bots/config?bot_type={BOT_HOLDINGS_WRITER}",
        json={"enabled": True, "config": {"delivery_cash_budget": 750000.0}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["config"]["delivery_cash_budget"] == 750000.0
    # Untouched fields keep their policy defaults.
    assert body["config"]["default_safety_pct_ce"] == 5.0


def test_invalid_config_is_rejected_with_400_not_silently_defaulted(client):
    r = client.patch(
        f"/bots/config?bot_type={BOT_HOLDINGS_WRITER}", json={"config": {"default_safety_pct_ce": 900}}
    )
    assert r.status_code == 400
    assert "Invalid bot configuration" in r.json()["detail"]
    # And nothing was persisted.
    assert client.get(f"/bots/config?bot_type={BOT_HOLDINGS_WRITER}").json()["config"][
        "default_safety_pct_ce"
    ] == 5.0


def test_unknown_index_in_bot2_config_is_rejected(client):
    r = client.patch(
        f"/bots/config?bot_type={BOT_EXPIRY_INDEX_WRITER}",
        json={"config": {"indices": {"BANKNIFTY": {"enabled": True}}}},
    )
    assert r.status_code == 400


def test_enabling_is_blocked_in_read_only_mode(tmp_path, monkeypatch):
    """Arming a bot is arming something that will trade, so read-only must refuse it up
    front rather than accept it and skip every run."""
    from fastapi import HTTPException

    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)

    async def _ctx():
        return RequestContext(
            user_id="user1", username="user1", roles=["trader"],
            is_authenticated=True, broker_token=None,
        )

    def _revoked():
        raise HTTPException(status_code=403, detail="Read-only mode")

    app = FastAPI()
    app.include_router(router, prefix="/bots")
    app.dependency_overrides[get_request_context] = _ctx
    app.dependency_overrides[require_trading_not_revoked] = _revoked
    with TestClient(app) as c:
        assert c.patch(f"/bots/config?bot_type={BOT_HOLDINGS_WRITER}", json={"enabled": True}).status_code == 403
        # Reading stays available -- read-only is a real state, not an outage.
        assert c.get("/bots/list").status_code == 200


def test_scrip_prefs_round_trip(client):
    r = client.put(
        "/bots/scrip-prefs",
        json={"prefs": [{"stock_code": "ITC", "ce_enabled": False, "pe_enabled": True}]},
    )
    assert r.status_code == 200
    assert r.json()[0]["stock_code"] == "ITC"
    assert client.get("/bots/scrip-prefs").json()[0]["pe_enabled"] is True


def test_run_log_is_shared_across_bots_and_filterable(client):
    a = repo.start_run("user1", BOT_HOLDINGS_WRITER, "manual")
    repo.finish_run(a, status="proposed", reason_code=ReasonCode.PROPOSAL_READY, reason_text="ok")
    b = repo.start_run("user1", BOT_EXPIRY_INDEX_WRITER, "schedule")
    repo.finish_run(
        b, status="skipped", reason_code=ReasonCode.NOT_AN_EXPIRY_DAY, reason_text="Not expiry."
    )

    all_runs = client.get("/bots/runs").json()
    assert len(all_runs) == 2
    assert {r["reason_code"] for r in all_runs} == {
        ReasonCode.PROPOSAL_READY,
        ReasonCode.NOT_AN_EXPIRY_DAY,
    }
    filtered = client.get(f"/bots/runs?bot_type={BOT_HOLDINGS_WRITER}").json()
    assert [r["id"] for r in filtered] == [a]


def test_proposal_is_null_when_there_is_nothing_to_approve(client):
    r = client.get(f"/bots/proposal?bot_type={BOT_HOLDINGS_WRITER}")
    assert r.status_code == 200
    assert r.json() is None


def test_pending_proposal_is_served_then_rejected(client):
    run_id = repo.start_run("user1", BOT_HOLDINGS_WRITER, "manual")
    repo.create_proposal(
        run_id=run_id,
        user_id="user1",
        bot_type=BOT_HOLDINGS_WRITER,
        legs=[
            ProposalLeg(
                stock_code="ITC",
                right="call",
                expiry_display="24-Sep-2026",
                strike_price=280.0,
                lots=3,
                lot_size=1725,
                quantity=5175,
                premium_per_share=4.25,
                premium_total=21993.75,
            )
        ],
    )
    served = client.get(f"/bots/proposal?bot_type={BOT_HOLDINGS_WRITER}").json()
    assert served["legs"][0]["stock_code"] == "ITC"
    assert served["legs"][0]["premium_per_share"] == 4.25

    rejected = client.post(f"/bots/proposal/reject?bot_type={BOT_HOLDINGS_WRITER}").json()
    assert rejected["status"] == "rejected"
    assert client.get(f"/bots/proposal?bot_type={BOT_HOLDINGS_WRITER}").json() is None


def test_rejecting_nothing_is_404(client):
    assert client.post(f"/bots/proposal/reject?bot_type={BOT_HOLDINGS_WRITER}").status_code == 404
