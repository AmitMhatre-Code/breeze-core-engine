"""Idempotency key handling for request deduplication (Phase 6 T088)."""
import logging
import sqlite3
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)


@dataclass
class IdempotencyResult:
    """Stored result of an idempotent operation."""
    idempotency_key: str
    user_id: str
    operation_type: str
    response_body: bytes
    status_code: int
    created_at: datetime
    expires_at: datetime


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


class IdempotencyKeyStore:
    """Store and retrieve idempotent operation results in SQLite."""

    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds

    def store_result(self, idempotency_key: str, user_id: str,
                     operation_type: str, response_body: bytes,
                     status_code: int) -> bool:
        """Store result of an idempotent operation."""
        try:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
            expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
            with sqlite3.connect(_db_path()) as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO idempotency_results
                       (idempotency_key, user_id, operation_type, response_body, status_code, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (idempotency_key, user_id, operation_type, response_body, status_code, expires_str),
                )
                conn.commit()
            return True
        except Exception as e:
            _logger.warning("Idempotency store failed: key=%s user_id=%s: %s", idempotency_key, user_id, e)
            return False

    def retrieve_result(self, idempotency_key: str, user_id: str) -> Optional[IdempotencyResult]:
        """Retrieve stored result if found and not expired."""
        try:
            with sqlite3.connect(_db_path()) as conn:
                cur = conn.cursor()
                cur.execute(
                    """SELECT idempotency_key, user_id, operation_type, response_body, status_code, created_at, expires_at
                       FROM idempotency_results WHERE idempotency_key = ? AND user_id = ?""",
                    (idempotency_key, user_id),
                )
                row = cur.fetchone()
            if not row:
                return None
            _, _, op_type, body, code, created, expires = row
            if isinstance(expires, str):
                expires_dt = datetime.strptime(expires[:19], "%Y-%m-%d %H:%M:%S") if " " in expires else datetime.fromisoformat(expires.replace("Z", ""))
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            else:
                expires_dt = expires
            now = datetime.now(timezone.utc)
            if now > expires_dt:
                return None
            created_dt = datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S") if isinstance(created, str) and " " in created else (datetime.fromisoformat(created.replace("Z", "")) if isinstance(created, str) else created)
            return IdempotencyResult(
                idempotency_key=idempotency_key,
                user_id=user_id,
                operation_type=op_type,
                response_body=body,
                status_code=code,
                created_at=created_dt,
                expires_at=expires_dt,
            )
        except Exception as e:
            _logger.warning("Idempotency retrieve failed: key=%s user_id=%s: %s", idempotency_key, user_id, e)
            return None

    def is_duplicate_request(self, idempotency_key: str, user_id: str) -> bool:
        return self.retrieve_result(idempotency_key, user_id) is not None


# Module-level store for route dependencies
idempotency_store = IdempotencyKeyStore(ttl_seconds=3600)
