"""User messages repository - transient UI feedback (order cancel/break)."""
import logging
from typing import List, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.repositories.base import get_sync_connection

_logger = logging.getLogger(__name__)


def store_messages(user_id: str, messages: list) -> bool:
    """Store messages for user. Each message is a dict with 'type' and 'message' keys."""
    if not messages:
        return True
    try:
        with get_sync_connection() as conn:
            cur = conn.cursor()
            for m in messages:
                msg_type = m.get("type", "")
                msg_text = m.get("message", "")
                cur.execute(
                    "INSERT INTO user_messages (user_id, msg_type, message) VALUES (?, ?, ?)",
                    (user_id, msg_type, msg_text),
                )
            conn.commit()
        return True
    except Exception as e:
        _logger.warning("Store messages failed: user_id=%s: %s", user_id, e, exc_info=True)
        return False


def retrieve_and_flush_messages(user_id: str) -> Optional[List[dict]]:
    """Retrieve all messages for user, delete them, return as list of dicts."""
    try:
        with get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT msg_type, message FROM user_messages WHERE user_id = ? ORDER BY id",
                (user_id,),
            )
            rows = cur.fetchall()
            if not rows:
                return None
            messages = [{"type": r[0], "message": r[1]} for r in rows]
            cur.execute("DELETE FROM user_messages WHERE user_id = ?", (user_id,))
            conn.commit()
        return messages
    except Exception as e:
        _logger.warning("Retrieve messages failed: user_id=%s: %s", user_id, e, exc_info=True)
        return [{"type": cfg.DANGER, "message": "Unable to load order messages. Please try again."}]
