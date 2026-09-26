"""Per-user "use the SPAN file for margins" choices, one per scope (docs/design-decisions.md #48).

- ``strategy_builder`` -- the Strategy Builder page only (its builds, proposals, basket margin
  and covered-shorts scan).
- ``backtest``         -- bot backtest lot sizing.
- ``app``              -- everywhere else a margin is quoted: Place Order, the option-chain
  place panel, the order confirmation dialog, Basket Order, the Uncovered Shorts page and the
  Portfolio page's SPAN figures.

Live bots never follow any of these: they size real orders, and an under-estimate there is a
rejected order, so they always ask ICICI.

A choice is only honoured while the portal's ICICI margin add-on is current
(margin_addon.get_active_rates). Without it a SPAN-file figure understates ICICI's by ~2-5% of
short notional, so every scope falls back to ICICI's margin_calculator, the stored choice is
left alone, and the scope returns to it by itself once the add-on arrives again.

Each scope is its own ``user_account`` column, all defaulting to the SPAN file. Adding a
column with a default fills existing rows too, so every existing user starts on the SPAN file
without a separate data migration. The retired ``strategy_builder_margin_source`` column
(default ``breeze_api``) is left in place and unread, so rolling back to an older image finds
the schema it expects.
"""
from __future__ import annotations

import logging
import sqlite3

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.margin_addon import get_active_rates
from icici_breeze_backend.app.services.nsccl_baseline import (
    MARGIN_SOURCE_BREEZE,
    MARGIN_SOURCE_EXCHANGE,
)

_logger = logging.getLogger(__name__)

SCOPE_STRATEGY_BUILDER = "strategy_builder"
SCOPE_BACKTEST = "backtest"
SCOPE_APP = "app"
SCOPES = (SCOPE_STRATEGY_BUILDER, SCOPE_BACKTEST, SCOPE_APP)

_COLUMNS = {
    SCOPE_STRATEGY_BUILDER: "margin_source_strategy_builder",
    SCOPE_BACKTEST: "margin_source_backtest",
    SCOPE_APP: "margin_source_app",
}
_VALID = (MARGIN_SOURCE_BREEZE, MARGIN_SOURCE_EXCHANGE)


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_margin_source_columns(db_path: str | None = None) -> None:
    with sqlite3.connect(db_path or _db_path()) as conn:
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_account'"
        ).fetchone():
            return
        have = {str(r[1]) for r in conn.execute("PRAGMA table_info(user_account)")}
        for column in _COLUMNS.values():
            if column not in have:
                conn.execute(
                    f"ALTER TABLE user_account ADD COLUMN {column} TEXT NOT NULL "
                    f"DEFAULT '{MARGIN_SOURCE_EXCHANGE}'"
                )
        conn.commit()


def _column(scope: str) -> str:
    try:
        return _COLUMNS[scope]
    except KeyError:
        raise ValueError(f"unknown margin scope {scope!r}") from None


def get_user_choice(user_id: str, scope: str) -> str:
    """What the user picked for `scope`, regardless of whether it can be honoured right now."""
    column = _column(scope)
    try:
        ensure_margin_source_columns()
        with sqlite3.connect(_db_path()) as conn:
            row = conn.execute(
                f"SELECT {column} FROM user_account WHERE user_id = ?", (user_id,)
            ).fetchone()
    except sqlite3.Error:
        _logger.debug("margin source choice unreadable for %s", user_id, exc_info=True)
        return MARGIN_SOURCE_EXCHANGE
    value = str(row[0]).strip().lower() if row and row[0] else MARGIN_SOURCE_EXCHANGE
    return value if value in _VALID else MARGIN_SOURCE_EXCHANGE


def set_user_choice(user_id: str, scope: str, source: str) -> None:
    source = (source or "").strip().lower()
    if source not in _VALID:
        raise ValueError(f"margin_source must be one of {_VALID}")
    column = _column(scope)
    ensure_margin_source_columns()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(f"UPDATE user_account SET {column} = ? WHERE user_id = ?", (source, user_id))
        conn.commit()


def honour(source: str | None) -> str:
    """`source` if it can be used right now, else ICICI's margin_calculator."""
    if source == MARGIN_SOURCE_EXCHANGE and get_active_rates() is not None:
        return MARGIN_SOURCE_EXCHANGE
    return MARGIN_SOURCE_BREEZE


def effective_margin_source(user_id: str, scope: str) -> str:
    """The source a margin for `scope` is actually priced from."""
    return honour(get_user_choice(user_id, scope))
