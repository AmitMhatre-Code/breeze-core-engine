"""Shared pytest fixtures for breeze-core-engine backend tests."""
from __future__ import annotations

# Must run before anything else imports `breeze_connect` (directly, or via
# icici_breeze_backend.app.services.processor/core.icici_client): breeze_connect
# downloads ICICI's SecurityMaster.zip with a bare urlopen() at import time,
# which crashes the whole test session without internet access to icicidirect.com.
# See tests/breeze_mock_env.py for details.
import tests.breeze_mock_env  # noqa: E402,F401

import pytest

from tests.fixtures.portal_heartbeat_drm_keys import TEST_PUBLIC_KEY_PEM


@pytest.fixture(autouse=True)
def _isolate_log_sink(tmp_path, monkeypatch):
    """Keep the on-disk log sink out of the real backend/data/ during tests.

    Any test that calls `configure_logging` attaches a RotatingFileHandler pointed at
    `cfg.DATA_PATH/logs`; without this the suite writes hundreds of KB into the
    developer's working tree.
    """
    from icici_breeze_backend.app.core import log_sink

    monkeypatch.setattr(log_sink, "logs_dir", lambda: str(tmp_path / "logs"))


@pytest.fixture(autouse=True)
def _roomy_data_volume(monkeypatch):
    """Report the data volume as 10% full, so no backtest test is halted or refused because the
    machine running the suite happens to have a full disk. Storage tests set their own reading."""
    import collections

    from icici_breeze_backend.app.services.storage import usage

    reading = collections.namedtuple("usage", "total used free")(100 * 1024**3, 10 * 1024**3, 90 * 1024**3)
    monkeypatch.setattr(usage.shutil, "disk_usage", lambda _path: reading)


@pytest.fixture(autouse=True)
def _clear_order_book_cache():
    """The SG order-book cache is process-global, so without this a test that reads the
    book would silently satisfy the next test's read and any assertion counting broker
    calls would pass for the wrong reason."""
    from icici_breeze_backend.app.services import order_book_cache

    order_book_cache.clear()
    yield
    order_book_cache.clear()


@pytest.fixture(autouse=True)
def _clear_symbol_registry_cache():
    """The symbol registry mirrors symbol_master in a process-global dict and a versioned Redis
    key, neither of which follows a test's tmp_path. Without clearing both, a test that seeds its
    own underlyings answers the next test's lookups, and a test that seeds none silently resolves
    against the developer's real backend/data/scrips.sqlite3."""
    from icici_breeze_backend.app.db.redis_client import cache_delete_pattern
    from icici_breeze_backend.app.services.reference_data import symbol_registry

    def _reset():
        symbol_registry.clear_cache()
        try:
            cache_delete_pattern("refdata:*:symbols")
        except Exception:
            pass

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _signal_state_isolated(tmp_path, monkeypatch):
    """The signal grid keeps one settings row, a table of backtest runs and in-process engines.
    Point both tables at temp DBs so no test reads or writes the developer's users.sqlite3, and
    start every test with no live engines or bars left over from another."""
    from icici_breeze_backend.app.services.index_signal import backtest as signal_backtest
    from icici_breeze_backend.app.services.index_signal import gate, publisher
    from icici_breeze_backend.app.services.index_signal import settings as signal_settings

    signal_settings.reset_cache_for_tests()
    publisher.reset_state_for_tests()
    gate.invalidate()
    monkeypatch.setattr(
        signal_settings, "_db_path", lambda: str(tmp_path / "signal_settings.sqlite3")
    )
    monkeypatch.setattr(signal_backtest, "_db_path", lambda: str(tmp_path / "signal_runs.sqlite3"))
    # The 30-day gate is open unless a test closes it: bot tests are about the bot, and the
    # gate's own tests (test_signal_backtest.py) read `mechanism_availability` directly.
    monkeypatch.setattr(gate, "refusal", lambda mechanism, db_path=None: None)
    yield
    signal_settings.reset_cache_for_tests()
    publisher.reset_state_for_tests()
    gate.invalidate()


@pytest.fixture(autouse=True)
def portal_heartbeat_verify_files(tmp_path, monkeypatch):
    """Bake test public key and allowed portal host for DRM verification tests."""
    pub = tmp_path / "portal_heartbeat_public.pem"
    hosts = tmp_path / "portal_allowed_hosts.txt"
    pub.write_text(TEST_PUBLIC_KEY_PEM, encoding="utf-8")
    hosts.write_text("portal.example\nbreeze-ui.com\n", encoding="utf-8")
    monkeypatch.setenv("PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("PORTAL_ALLOWED_HOSTS_PATH", str(hosts))


@pytest.fixture(autouse=True)
def exchange_calendar_db(tmp_path, monkeypatch):
    """Point the global exchange-calendar repo at a fresh temp DB seeded with
    bundled defaults, so unmocked calls to app.services.market_calendar never
    read or write the real backend/data/users.sqlite3 dev database. Tests that
    want a specific calendar can still monkeypatch
    `icici_breeze_backend.app.repositories.exchange_calendar._db_path`
    themselves (it will simply override this one for that test)."""
    from icici_breeze_backend.app.db.exchange_calendar_migrate import (
        ensure_exchange_calendar_table,
    )
    from icici_breeze_backend.app.repositories import exchange_calendar as ec_repo

    db_path = str(tmp_path / "exchange_calendar_default.sqlite3")
    ensure_exchange_calendar_table(db_path)
    monkeypatch.setattr(ec_repo, "_db_path", lambda: db_path)


@pytest.fixture(autouse=True)
def isolate_live_snapshot_cache_keys():
    """Purge processor.py's short-TTL get_positions()/get_margin_situation() cache
    around every test. It's a process-global Redis/in-memory cache keyed only by
    user_id, and its 15s TTL comfortably outlives a single test -- without this, a
    test asserting `assert_called_once()` on a mocked broker call could pass only
    because an earlier test for the same user_id already warmed the cache."""
    from icici_breeze_backend.app.db.redis_client import cache_delete_pattern

    cache_delete_pattern("portfolio_positions_snapshot:*")
    cache_delete_pattern("margin_situation_snapshot:*")
    yield
    cache_delete_pattern("portfolio_positions_snapshot:*")
    cache_delete_pattern("margin_situation_snapshot:*")


@pytest.fixture(autouse=True)
def isolate_quote_snapshot_keys():
    """Purge the durable quote-snapshot keys around every test.

    Unlike every other quote cache these are written with NO TTL (that is the
    whole point -- they must outlive the session), so on a dev machine with a
    real Redis they otherwise survive across test runs and leak into unrelated
    tests' view of `snapshot_is_fresh`.
    """
    from icici_breeze_backend.app.db.redis_client import cache_delete_pattern

    cache_delete_pattern("quotes:snapshot:*")
    yield
    cache_delete_pattern("quotes:snapshot:*")
