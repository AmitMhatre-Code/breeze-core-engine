"""Routes for signal variants (#38): Settings -> Index Signal, and a bot set to a variant."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.api.v1.route_bots import router as bots_router
from icici_breeze_backend.app.api.v1.route_settings import router as settings_router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.index_signal import shadow_log, variants

TEN_FADE = "nifty-w10-oi20-h10-fade"


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(shadow_log, "_db_path", lambda: path)
    ensure_bots_tables(path)

    async def _ctx():
        return RequestContext(
            user_id="user1", username="user1", roles=["trader"], is_authenticated=True, broker_token=None
        )

    app = FastAPI()
    app.include_router(settings_router)
    app.include_router(bots_router, prefix="/bots")
    app.dependency_overrides[get_request_context] = _ctx
    app.dependency_overrides[require_trading_not_revoked] = lambda: None
    with TestClient(app) as c:
        yield c


def _create(client, **kw):
    body = {"name": "10m fade", "window_minutes": 10, "oi_window_minutes": 20, "hold_minutes": 10, "direction": "fade"}
    body.update(kw)
    return client.post("/api/settings/index-signal/variants", json=body)


def test_the_list_carries_each_variants_verdict_and_the_form_bounds(client):
    r = client.get("/api/settings/index-signal/variants")
    assert r.status_code == 200
    data = r.json()
    assert [v["id"] for v in data["variants"]] == [v.id for v in variants.BUILTINS]
    assert all(v["readiness"]["status"] == "too_early" for v in data["variants"])
    assert data["bounds"]["oi_window_minutes"][0] == 15


def test_create_refuses_a_duplicate_and_a_thin_oi_window(client):
    assert _create(client).json()["id"] == TEN_FADE
    assert _create(client, name="again").status_code == 400
    assert _create(client, name="thin", oi_window_minutes=10).status_code == 400


def test_a_variant_a_bot_is_set_to_cannot_be_deleted(client):
    _create(client)
    assert client.patch(
        f"/bots/config?bot_type={BOT_MOMENTUM_LONG_SCALPER}", json={"config": {"entry_signal": TEN_FADE}}
    ).status_code == 200
    listed = {v["id"]: v for v in client.get("/api/settings/index-signal/variants").json()["variants"]}
    assert listed[TEN_FADE]["used_by"] == [BOT_MOMENTUM_LONG_SCALPER]
    assert client.delete(f"/api/settings/index-signal/variants/{TEN_FADE}").status_code == 409

    client.patch(f"/bots/config?bot_type={BOT_MOMENTUM_LONG_SCALPER}", json={"config": {"entry_signal": "momentum"}})
    assert client.delete(f"/api/settings/index-signal/variants/{TEN_FADE}").status_code == 200
    assert client.delete(f"/api/settings/index-signal/variants/{variants.FADE_15_ID}").status_code == 400


def test_a_bot_cannot_be_set_to_a_variant_that_does_not_exist(client):
    r = client.patch(
        f"/bots/config?bot_type={BOT_MOMENTUM_LONG_SCALPER}", json={"config": {"entry_signal": TEN_FADE}}
    )
    assert r.status_code == 400 and "no signal variant" in r.json()["detail"]
    r = client.patch(
        f"/bots/config?bot_type={BOT_IRON_FLY_SCALPER}",
        json={"config": {"entry_filter": {"kind": "expansion_neutral", "variant": TEN_FADE}}},
    )
    assert r.status_code == 400


def test_a_variants_flips_are_readable_but_an_unknown_label_is_not(client):
    assert client.get(
        "/api/settings/index-signal/flips", params={"label": f"nifty:expansion:{variants.FADE_15_ID}"}
    ).status_code == 200
    assert client.get("/api/settings/index-signal/flips", params={"label": "nifty:bogus"}).status_code == 400
