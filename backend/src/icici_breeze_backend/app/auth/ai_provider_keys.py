"""Per-user encrypted storage for GenAI provider API keys."""
from __future__ import annotations

import hashlib
import sqlite3
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from typing import Optional

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


@dataclass
class AiProviderConfig:
    user_id: str
    provider: str
    api_key: str
    model: Optional[str]
    enabled: bool


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
        enabled: bool = True,
    ) -> None:
        p = (provider or "").strip().lower()
        if p not in _PROVIDERS:
            raise ValueError("provider must be gemini or openai")
        key = (api_key or "").strip()
        if not key:
            raise ValueError("api_key is required")
        enc = self._cipher.encrypt(key.encode("utf-8"))
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO user_ai_provider(user_id, provider, api_key_encrypted, model, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    provider=excluded.provider,
                    api_key_encrypted=excluded.api_key_encrypted,
                    model=excluded.model,
                    enabled=excluded.enabled,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (user_id, p, enc, (model or "").strip() or None, 1 if enabled else 0),
            )
            conn.commit()

    def get(self, user_id: str) -> Optional[AiProviderConfig]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT user_id, provider, api_key_encrypted, model, enabled
                FROM user_ai_provider
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None
        dec = self._cipher.decrypt(row[2]).decode("utf-8")
        return AiProviderConfig(
            user_id=row[0],
            provider=row[1],
            api_key=dec,
            model=row[3],
            enabled=bool(row[4]),
        )

    def get_masked(self, user_id: str) -> Optional[dict]:
        cfg = self.get(user_id)
        if not cfg:
            return None
        return {
            "user_id": cfg.user_id,
            "provider": cfg.provider,
            "model": cfg.model,
            "enabled": cfg.enabled,
            "masked_api_key": _mask_key(cfg.api_key),
        }

    def revoke(self, user_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                """
                UPDATE user_ai_provider
                SET enabled = 0, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (user_id,),
            )
            conn.commit()
        return cur.rowcount > 0
