"""Base repository and DB utilities."""
import sqlite3
from typing import Optional

import icici_breeze_backend.app.core.config as cfg


def get_sync_db_path() -> str:
    """Path to main SQLite database."""
    return cfg.DATA_PATH + "db.sqlite3"


def get_scrip_db_path() -> str:
    """Path to scrip master database."""
    return cfg.DATA_PATH + cfg.SCRIP_DB


def get_sync_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Sync connection for repositories. Use in with-block."""
    path = db_path or get_sync_db_path()
    return sqlite3.connect(path)
