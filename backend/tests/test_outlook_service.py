import json
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import httpx

from icici_breeze_backend.app.auth.ai_provider_keys import AiProviderConfig, AiProviderKeyManager
from icici_breeze_backend.app.db.ai_provider_migrate import ensure_ai_provider_table
from icici_breeze_backend.app.services.outlook_service import OutlookError, OutlookService


def test_ai_provider_key_manager_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        ensure_ai_provider_table(path)
        import icici_breeze_backend.app.auth.ai_provider_keys as keys_mod

        prev_data_path = keys_mod.DATA_PATH
        prev_users_db = keys_mod.USERS_DB
        keys_mod.DATA_PATH = ""
        keys_mod.USERS_DB = path
        try:
            mgr = AiProviderKeyManager(encryption_key="unit-test-secret")
            mgr.upsert(user_id="u1", provider="gemini", api_key="abcd1234xyz", model="gemini-1.5-flash")
            cfg = mgr.get("u1", "gemini")
            assert cfg is not None
            assert cfg.provider == "gemini"
            assert cfg.api_key == "abcd1234xyz"
            masked = mgr.get_masked("u1", "gemini")
            assert masked is not None
            assert masked["masked_api_key"].startswith("abcd")
            mgr.upsert(user_id="u1", provider="openai", api_key="sk-openai-test", model="gpt-4o-mini")
            gem2 = mgr.get("u1", "gemini")
            oai = mgr.get("u1", "openai")
            assert gem2 is not None and oai is not None
            assert mgr.delete_provider("u1", "gemini")
            assert mgr.get("u1", "gemini") is None
            assert mgr.get("u1", "openai") is not None
            assert mgr.delete_provider("u1", "openai")
            assert mgr.get("u1", "openai") is None
        finally:
            keys_mod.DATA_PATH = prev_data_path
            keys_mod.USERS_DB = prev_users_db
    finally:
        os.unlink(path)


def test_outlook_validation_requires_sources():
    svc = OutlookService()
    payload = {
        "summary": ["Volatility can stay elevated"],
        "inference": {
            "volatility_view": "Elevated into event week",
            "movement_scenarios": ["Range-bound with downside tails"],
            "confidence": "medium",
            "caveats": ["Data surprise can shift regime"],
        },
        "strategy_ideas": [{"tag": "defined-risk", "rationale": "IV rich", "risk_note": "Gap risk remains"}],
        "sources": [],
    }
    try:
        svc._validate_payload(payload)  # noqa: SLF001
    except OutlookError as exc:
        assert exc.code == "validation_error"
    else:
        raise AssertionError("Expected validation error for missing sources")


def test_outlook_validation_blocks_imperative_advice():
    svc = OutlookService()
    payload = {
        "summary": ["Buy now for guaranteed return"],
        "inference": {
            "volatility_view": "Elevated",
            "movement_scenarios": ["Upward drift"],
            "confidence": "low",
            "caveats": ["Weak evidence"],
        },
        "strategy_ideas": [{"tag": "long-call", "rationale": "Momentum", "risk_note": "Theta decay"}],
        "sources": [{"title": "Reuters", "url": "https://example.com", "publisher": "Reuters", "published_at": None}],
    }
    try:
        svc._validate_payload(payload)  # noqa: SLF001
    except OutlookError as exc:
        assert exc.code == "validation_error"
    else:
        raise AssertionError("Expected validation error for imperative advice wording")


def test_ensure_ai_provider_table_creates_table():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        ensure_ai_provider_table(path)
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_ai_provider'"
            ).fetchone()
            assert row is not None
    finally:
        os.unlink(path)


def test_gemini_tries_fallback_after_429():
    svc = OutlookService()
    cfg = AiProviderConfig(
        user_id="u1",
        provider="gemini",
        api_key="test-key",
        model="gemini-primary",
        fallback_models=["gemini-fallback"],
        enabled=True,
    )
    inner = {
        "summary": ["Volatility can stay elevated"],
        "inference": {
            "volatility_view": "v",
            "movement_scenarios": ["m"],
            "confidence": "medium",
            "caveats": ["c"],
        },
        "strategy_ideas": [{"tag": "t", "rationale": "r", "risk_note": "n"}],
        "sources": [
            {"title": "Reuters", "url": "https://example.com", "publisher": "Reuters", "published_at": None},
        ],
    }
    ok_body = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(inner)}]}}],
    }

    def _resp(status: int, *, json_payload=None, text: str = ""):
        m = MagicMock()
        m.status_code = status
        m.text = text
        m.json.return_value = json_payload
        return m

    r429 = _resp(429, text="Resource exhausted (quota)")
    r200 = _resp(200, json_payload=ok_body)

    with patch("icici_breeze_backend.app.services.outlook_service.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.side_effect = [r429, r200]
        out = svc._call_gemini(ai_cfg=cfg, prompt="prompt", system_prompt=None)  # noqa: SLF001

    assert out["summary"] == ["Volatility can stay elevated"]
    assert inst.post.call_count == 2
    first_url = inst.post.call_args_list[0][0][0]
    second_url = inst.post.call_args_list[1][0][0]
    assert "gemini-primary" in first_url
    assert "gemini-fallback" in second_url


def test_gemini_tries_fallback_after_non_success_http():
    svc = OutlookService()
    cfg = AiProviderConfig(
        user_id="u1",
        provider="gemini",
        api_key="test-key",
        model="gemini-primary",
        fallback_models=["gemini-fallback"],
        enabled=True,
    )
    inner = {
        "summary": ["S"],
        "inference": {
            "volatility_view": "v",
            "movement_scenarios": ["m"],
            "confidence": "low",
            "caveats": ["c"],
        },
        "strategy_ideas": [{"tag": "t", "rationale": "r", "risk_note": "n"}],
        "sources": [
            {"title": "Reuters", "url": "https://example.com", "publisher": "Reuters", "published_at": None},
        ],
    }
    ok_body = {
        "candidates": [{"content": {"parts": [{"text": json.dumps(inner)}]}}],
    }

    def _resp(status: int, *, json_payload=None, text: str = ""):
        m = MagicMock()
        m.status_code = status
        m.text = text
        m.json.return_value = json_payload
        return m

    r400 = _resp(400, text="bad request")
    r200 = _resp(200, json_payload=ok_body)

    with patch("icici_breeze_backend.app.services.outlook_service.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.side_effect = [r400, r200]
        out = svc._call_gemini(ai_cfg=cfg, prompt="prompt", system_prompt=None)  # noqa: SLF001

    assert out["summary"] == ["S"]
    assert inst.post.call_count == 2


def test_gemini_does_not_chain_models_on_network_error():
    svc = OutlookService()
    cfg = AiProviderConfig(
        user_id="u1",
        provider="gemini",
        api_key="test-key",
        model="gemini-primary",
        fallback_models=["gemini-fallback"],
        enabled=True,
    )

    with patch("icici_breeze_backend.app.services.outlook_service.httpx.Client") as client_cls:
        inst = MagicMock()
        client_cls.return_value.__enter__.return_value = inst
        inst.post.side_effect = httpx.ConnectError("no route to host", request=MagicMock())
        try:
            svc._call_gemini(ai_cfg=cfg, prompt="prompt", system_prompt=None)  # noqa: SLF001
        except OutlookError as exc:
            assert exc.code == "network_error"
        else:
            raise AssertionError("Expected network_error")
        assert inst.post.call_count == 1
