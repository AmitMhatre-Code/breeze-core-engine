"""Base repository and DB utilities."""
import sqlite3
from typing import Optional

import icici_breeze_backend.app.core.config as cfg


def get_sync_db_path() -> str:
    """Path to main SQLite database."""
    return cfg.DATA_PATH + cfg.USERS_DB


def get_scrip_db_path() -> str:
    """Path to scrip master database."""
    return cfg.DATA_PATH + cfg.SCRIP_DB


def get_sync_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Sync connection for repositories. Use in with-block.

    synchronous/cache_size are per-connection (unlike journal_mode, not persisted in the
    DB file), so they're set here rather than once at startup. NORMAL is safe under WAL
    (only FULL protects against an OS crash losing the last commit; WAL already protects
    against app-level crashes, which is the far more common case).
    """
    path = db_path or get_sync_db_path()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")
    return conn
