"""Portable NDJSON debug lines for remote (EC2) reproduction — session d5296e."""
from __future__ import annotations

import json
import logging
import os
import time

_logger = logging.getLogger(__name__)
_SESSION_ID = "d5296e"


def debug_log_path() -> str:
    """Writable on EC2 under app data dir; falls back to /tmp."""
    try:
        import icici_breeze_backend.app.core.config as cfg

        return os.path.join(cfg.DATA_PATH, f"debug-{_SESSION_ID}.ndjson")
    except Exception:
        return f"/tmp/debug-{_SESSION_ID}.ndjson"


def agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": _SESSION_ID,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    # #region agent log
    line = json.dumps(payload, default=str)
    paths = [debug_log_path(), f"/tmp/debug-{_SESSION_ID}.ndjson"]
    for path in paths:
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    _logger.warning("[debug-%s] %s", _SESSION_ID, line)
    # #endregion
