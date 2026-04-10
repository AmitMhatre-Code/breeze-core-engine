"""Per-user encrypted storage for GenAI provider API keys."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from typing import Any, Optional

from cryptography.fernet import Fernet

from icici_breeze_backend.app.core.config import DATA_PATH, USERS_DB

_PROVIDERS = {"gemini", "openai"}


def _fernet_from_secret(secret: str) -> Fernet:
    key_material = hashlib.sha256(((secret or "") + "ai_provider_keys").encode()).digest()
    return Fernet(urlsafe_b64encode(key_material[:32]))


def _mask_key(key: str) -> str:
    key = (key or "").strip()
    if len(key) <= 8:
        return "*" * len(key) if key else ""
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _parse_health(raw: Optional[str]) -> dict[str, dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[str(k)] = v
    return out


@dataclass
class AiProviderConfig:
    user_id: str
    provider: str
    api_key: str
    model: Optional[str]
    fallback_models: list[str]
    enabled: bool
    model_health: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_model_health_at: Optional[str] = None
    """None = list every model in global Gemini catalog (legacy)."""
    tracked_models: Optional[list[str]] = None


_MISSING = object()


class AiProviderKeyManager:
    def __init__(self, encryption_key: str):
        self._cipher = _fernet_from_secret(encryption_key or "")
        self._db_path = DATA_PATH + USERS_DB

    def upsert(
        self,
        *,
        user_id: str,
        provider: str,
        api_key: str,
        model: Optional[str] = None,
        fallback_models: Optional[list[str]] = None,
        enabled: bool = True,
        clear_health: bool = True,
    ) -> None:
        p = (provider or "").strip().lower()
        if p not in _PROVIDERS:
            raise ValueError("provider must be gemini or openai")
        key = (api_key or "").strip()
        if not key:
            raise ValueError("api_key is required")
        enc = self._cipher.encrypt(key.encode("utf-8"))
        normalized_models = self._normalize_model_list(fallback_models)
        with sqlite3.connect(self._db_path) as conn:
            set_health = (
                "model_health_json=NULL, last_model_health_at=NULL,"
                if clear_health
                else ""
            )
            conn.execute(
                f"""
                INSERT INTO user_ai_provider(
                    user_id, provider, api_key_encrypted, model, fallback_models_json,
                    tracked_models_json,
                    enabled, model_health_json, last_model_health_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    api_key_encrypted=excluded.api_key_encrypted,
                    model=excluded.model,
                    fallback_models_json=excluded.fallback_models_json,
                    tracked_models_json=user_ai_provider.tracked_models_json,
                    enabled=excluded.enabled,
                    {set_health}
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    p,
                    enc,
                    (model or "").strip() or None,
                    json.dumps(normalized_models),
                    1 if enabled else 0,
                ),
            )
            conn.commit()

    def update_model_fields(
        self,
        *,
        user_id: str,
        provider: str,
        model: Optional[str] = None,
        fallback_models: Optional[list[str]] = None,
        tracked_models: Any = _MISSING,
    ) -> bool:
        p = (provider or "").strip().lower()
        if p not in _PROVIDERS:
            raise ValueError("provider must be gemini or openai")
        row = self._fetch_row(user_id, p)
        if not row:
            return False
        normalized = self._normalize_model_list(fallback_models if fallback_models is not None else self._load_model_list(row[4]))
        new_model = (model if model is not None else row[3])
        new_model = (new_model or "").strip() or None
        tracked_sql = ""
        tracked_val: tuple = ()
        if tracked_models is not _MISSING:
            tj: Optional[str]
            if not tracked_models:
                tj = None
            else:
                tj = json.dumps(self._dedupe_tracked_list([str(x) for x in tracked_models]))
            tracked_sql = ", tracked_models_json = ?"
            tracked_val = (tj,)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                f"""
                UPDATE user_ai_provider
                SET model = ?, fallback_models_json = ?, updated_at = CURRENT_TIMESTAMP{tracked_sql}
                WHERE user_id = ? AND provider = ?
                """,
                (new_model, json.dumps(normalized), *tracked_val, user_id, p),
            )
            conn.commit()
        return True

    def _fetch_row(self, user_id: str, provider: str) -> tuple | None:
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute(
                """
                SELECT user_id, provider, api_key_encrypted, model, fallback_models_json, enabled,
                       model_health_json, last_model_health_at, tracked_models_json
                FROM user_ai_provider
                WHERE user_id = ? AND provider = ?
                """,
                (user_id, provider),
            ).fetchone()

    def get(self, user_id: str, provider: str) -> Optional[AiProviderConfig]:
        p = (provider or "").strip().lower()
        if p not in _PROVIDERS:
            return None
        row = self._fetch_row(user_id, p)
        if not row:
            return None
        dec = self._cipher.decrypt(row[2]).decode("utf-8")
        return AiProviderConfig(
            user_id=row[0],
            provider=row[1],
            api_key=dec,
            model=row[3],
            fallback_models=self._load_model_list(row[4]),
            enabled=bool(row[5]),
            model_health=_parse_health(row[6] if len(row) > 6 else None),
            last_model_health_at=(row[7] if len(row) > 7 else None) or None,
            tracked_models=self._parse_tracked_list(row[8] if len(row) > 8 else None),
        )

    def get_masked(self, user_id: str, provider: str) -> Optional[dict[str, Any]]:
        cfg = self.get(user_id, provider)
        if not cfg:
            return None
        ok, fail = _health_counts(cfg.model_health)
        return {
            "user_id": cfg.user_id,
            "provider": cfg.provider,
            "model": cfg.model,
            "fallback_models": cfg.fallback_models,
            "tracked_models": cfg.tracked_models,
            "enabled": cfg.enabled,
            "masked_api_key": _mask_key(cfg.api_key),
            "model_health": cfg.model_health,
            "last_model_health_at": cfg.last_model_health_at,
            "models_working": ok,
            "models_failing": fail,
        }

    def delete_provider(self, user_id: str, provider: str) -> bool:
        p = (provider or "").strip().lower()
        if p not in _PROVIDERS:
            return False
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM user_ai_provider WHERE user_id = ? AND provider = ?",
                (user_id, p),
            )
            conn.commit()
        return cur.rowcount > 0

    def set_model_health(
        self,
        user_id: str,
        provider: str,
        health: dict[str, dict[str, Any]],
        checked_at_iso: str,
    ) -> None:
        p = (provider or "").strip().lower()
        if p not in _PROVIDERS:
            raise ValueError("invalid provider")
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE user_ai_provider
                SET model_health_json = ?, last_model_health_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND provider = ?
                """,
                (json.dumps(health), checked_at_iso, user_id, p),
            )
            conn.commit()

    def merge_model_health_entry(
        self,
        user_id: str,
        provider: str,
        model: str,
        ok: bool,
        message: Optional[str],
        checked_at_iso: str,
    ) -> dict[str, dict[str, Any]]:
        cfg = self.get(user_id, provider)
        if not cfg:
            raise ValueError("no config")
        health = dict(cfg.model_health)
        health[model] = {"ok": ok, "message": message, "checked_at": checked_at_iso}
        self.set_model_health(user_id, provider, health, checked_at_iso)
        return health

    @staticmethod
    def _normalize_model_list(models: Optional[list[str]]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for model in models or []:
            m = str(model or "").strip()
            if not m or m in seen:
                continue
            seen.add(m)
            out.append(m)
        return out[:20]

    @staticmethod
    def _load_model_list(raw: Optional[str]) -> list[str]:
        text = (raw or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return AiProviderKeyManager._normalize_model_list([str(item) for item in data])

    @staticmethod
    def _parse_tracked_list(raw: Optional[str]) -> Optional[list[str]]:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except Exception:
            return None
        if not isinstance(data, list):
            return None
        return AiProviderKeyManager._dedupe_tracked_list([str(item) for item in data])

    @staticmethod
    def _dedupe_tracked_list(models: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for model in models:
            m = str(model or "").strip()
            if not m or m in seen:
                continue
            seen.add(m)
            out.append(m)
        return out


def _health_counts(health: dict[str, dict[str, Any]]) -> tuple[int, int]:
    ok_n = 0
    fail_n = 0
    for meta in health.values():
        if bool(meta.get("ok")):
            ok_n += 1
        else:
            fail_n += 1
    return ok_n, fail_n
