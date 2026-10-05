"""Fresh-volume seeding of users.sqlite3.

On a new deployment the data volume is empty and the backend seeds users.sqlite3 from
its template. The chain-builder worker starts alongside it and used to create the file
first (for its P&L-interval lookup), leaving a DB with no user_account table that the
backend then never seeded -- every start crash-looped on "no such table".
"""
from __future__ import annotations

import os
import shutil
import sqlite3

import pytest

import icici_breeze_backend.app.core.config as app_cfg
from icici_breeze_backend import main
from icici_breeze_backend.app.services import pnl_engine_settings
from icici_breeze_backend.core import config as cfg

_TEMPLATE = os.path.join(os.path.dirname(__file__), "..", "data", "users.empty.sqlite3")


def _tables(path: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    templates = tmp_path / "db-templates"
    data.mkdir()
    templates.mkdir()
    shutil.copy2(_TEMPLATE, templates / cfg.USERS_EMPTY_DB)
    # app.core.config copies DATA_PATH at import, and the no-argument migrations read that copy.
    monkeypatch.setattr(cfg, "DATA_PATH", str(data) + os.sep)
    monkeypatch.setattr(app_cfg, "DATA_PATH", str(data) + os.sep)
    monkeypatch.setattr(cfg, "DB_TEMPLATE_PATH", str(templates) + os.sep)
    return data


def test_settings_lookup_does_not_create_the_users_db(data_dir):
    loaded = pnl_engine_settings.load_pnl_engine_settings()

    assert loaded["pnl_recompute_interval_seconds"] > 0
    assert not (data_dir / cfg.USERS_DB).exists()


def test_db_created_by_another_process_is_reseeded(data_dir):
    db_path = str(data_dir / cfg.USERS_DB)
    pnl_engine_settings.ensure_pnl_engine_settings_table(db_path)  # what the worker used to do
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")

    main._ensure_app_database()

    assert {"user_account", "bots", "portfolio_squareoff_rules"} <= _tables(db_path)
    set_aside = [p.name for p in data_dir.iterdir() if ".unseeded-" in p.name]
    assert set_aside and all(n.startswith(cfg.USERS_DB) for n in set_aside)


def test_seeded_db_is_left_alone(data_dir):
    db_path = str(data_dir / cfg.USERS_DB)
    main._ensure_app_database()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO user_account (user_id, username, email) VALUES ('u1', 'u', 'u@x')"
        )

    main._ensure_app_database()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_account").fetchone()[0] == 1
    assert not [p for p in data_dir.iterdir() if ".unseeded-" in p.name]
