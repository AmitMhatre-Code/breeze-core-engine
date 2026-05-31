"""Breeze API Playground: catalog, risk ack, and mock invoke."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile

import pytest

from icici_breeze_backend.app.domain.breeze_api_tester_catalog import (
    ALLOWED_METHODS,
    build_invoke_args,
    get_catalog_entry,
)
from icici_breeze_backend.app.services import breeze_api_tester_risk as risk_mod


@pytest.fixture
def users_db_env(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_name = "users.sqlite3"
    db_path = data_dir / db_name
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE user_account (
                user_id TEXT PRIMARY KEY NOT NULL,
                username TEXT NOT NULL,
                email TEXT NOT NULL
            );
            INSERT INTO user_account (user_id, username, email)
            VALUES ('u_play', 'u_play', 'u@test.local');
            """
        )
    monkeypatch.setattr(risk_mod.cfg, "DATA_PATH", str(data_dir) + os.sep)
    monkeypatch.setattr(risk_mod.cfg, "USERS_DB", db_name)
    yield "u_play"


def test_catalog_has_thirty_methods():
    assert len(ALLOWED_METHODS) == 30
    assert get_catalog_entry("place_order") is not None
    assert get_catalog_entry("get_order_list") is not None


def test_build_invoke_args_unknown_method():
    with pytest.raises(ValueError, match="Unknown method"):
        build_invoke_args("not_a_real_api", {})


def test_build_invoke_args_invalid_json():
    with pytest.raises(ValueError, match="Invalid JSON"):
        build_invoke_args("margin_calculator", {"margin_list": "{not json"})


def test_build_invoke_args_margin_calculator_positional():
    legs = [{"stock_code": "NIFTY", "quantity": "75"}]
    import json

    pos, kw = build_invoke_args(
        "margin_calculator",
        {"margin_list": json.dumps(legs), "exchange_code": "NFO"},
    )
    assert len(pos) == 1
    assert pos[0] == legs
    assert kw["exchange_code"] == "NFO"


def test_risk_not_accepted_by_default(users_db_env):
    assert not risk_mod.is_breeze_api_tester_risk_accepted(users_db_env)


def test_risk_accept_and_check(users_db_env):
    ts = risk_mod.set_breeze_api_tester_risk_accepted(users_db_env)
    assert ts
    assert risk_mod.is_breeze_api_tester_risk_accepted(users_db_env)
    assert risk_mod.get_breeze_api_tester_risk_accepted_at(users_db_env) == ts


@pytest.fixture
def mock_broker_env(monkeypatch):
    monkeypatch.setenv("ICICI_BROKER_MODE", "mock")
    import icici_breeze_backend.core.config as core_config
    import icici_breeze_backend.app.core.config as app_config

    importlib.reload(core_config)
    importlib.reload(app_config)


def test_mock_invoke_get_funds(mock_broker_env):
    from icici_breeze_backend.dev.mock_broker import MockBreezeSdk

    pos, kw = build_invoke_args("get_funds", {})
    breeze = MockBreezeSdk()
    out = breeze.get_funds(*pos, **kw)
    assert out.get("Status") == 200
    assert out.get("Success") is not None


def test_mock_invoke_unknown_method_stub(mock_broker_env):
    from icici_breeze_backend.dev.mock_broker import MockBreezeSdk

    breeze = MockBreezeSdk()
    fn = getattr(breeze, "get_trade_detail")
    out = fn(exchange_code="NSE", order_id="1")
    assert out.get("Status") == 200
