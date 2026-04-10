"""Stored preference: which LLM provider generates market outlook when both are configured."""
from __future__ import annotations

import sqlite3

import icici_breeze_backend.app.core.config as cfg


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def get_outlook_ai_provider(user_id: str) -> str | None:
    try:
        with sqlite3.connect(_db_path()) as conn:
            row = conn.execute(
                "SELECT outlook_ai_provider FROM user_account WHERE user_id = ?",
                (user_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or not row[0]:
        return None
    v = str(row[0]).strip().lower()
    if v in ("gemini", "openai"):
        return v
    return None


def set_outlook_ai_provider(user_id: str, provider: str) -> None:
    p = (provider or "").strip().lower()
    if p not in ("gemini", "openai"):
        raise ValueError("provider must be gemini or openai")
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "UPDATE user_account SET outlook_ai_provider = ? WHERE user_id = ?",
            (p, user_id),
        )
        conn.commit()
