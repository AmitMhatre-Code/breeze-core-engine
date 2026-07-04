"""Test-only setup for pointing the real `breeze_connect` SDK at the local mock server.

Import this module *before* anything else imports `breeze_connect` (directly, or
transitively via `icici_breeze_backend.app.services.processor` /
`icici_breeze_backend.core.icici_client`). `backend/tests/conftest.py` does this
at the very top of the file for that reason.

Why this exists: `breeze_connect` downloads ICICI's real `SecurityMaster.zip`
with a bare, unguarded `urllib.request.urlopen(...)` call at *module import
time* (`breeze_connect/breeze_connect.py:32`), and re-downloads it again on
every `generate_session()` call. Neither call is configurable, and the first
one isn't even wrapped in a try/except -- on a machine without direct internet
access to icicidirect.com (or behind a TLS-intercepting proxy), merely
`import breeze_connect` crashes before any test/fixture code gets to run. This
module patches both download paths, scoped narrowly to icicidirect.com URLs,
to return an empty (but valid) zip instead -- `get_stock_script_list()`
already tolerates an empty archive (it's the same code path it falls back to
on any real download failure), it just does so instantly and offline.
"""
from __future__ import annotations

import base64
import contextlib
import io
import socket
import threading
import time
import urllib.request
import zipfile

import httpx
import requests

from tests.fixtures.mock_instruments import ALL_INSTRUMENTS

_ICICI_HOST_MARKER = "icicidirect.com"


def _empty_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    return buf.getvalue()


_EMPTY_ZIP = _empty_zip_bytes()


class _FakeUrlopenResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def __enter__(self) -> "_FakeUrlopenResponse":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _install_offline_patches() -> None:
    """Idempotent: safe to import this module more than once."""
    real_urlopen = urllib.request.urlopen
    if getattr(real_urlopen, "_breeze_mock_patched", False):
        return

    def _offline_urlopen(url, *args, **kwargs):
        target = url if isinstance(url, str) else getattr(url, "full_url", "")
        if _ICICI_HOST_MARKER in str(target):
            return _FakeUrlopenResponse(_EMPTY_ZIP)
        return real_urlopen(url, *args, **kwargs)

    _offline_urlopen._breeze_mock_patched = True  # type: ignore[attr-defined]
    urllib.request.urlopen = _offline_urlopen

    real_requests_get = requests.get

    def _offline_requests_get(url, *args, **kwargs):
        if _ICICI_HOST_MARKER in str(url):
            raise requests.exceptions.ConnectionError(
                "breeze_mock_env: real ICICI network calls are disabled under the local mock harness"
            )
        return real_requests_get(url, *args, **kwargs)

    _offline_requests_get._breeze_mock_patched = True  # type: ignore[attr-defined]
    requests.get = _offline_requests_get


_install_offline_patches()

import breeze_connect  # noqa: E402  (import deliberately after the offline patches above)
from breeze_connect import breeze_connect as _breeze_connect_module  # noqa: E402

_config = _breeze_connect_module.config


@contextlib.contextmanager
def patch_breeze_urls(base_url: str, *, ws_market_mode: str | None = None):
    """Point every `breeze_connect.config` base URL at the local mock server.

    `breeze_connect` reads `config.API_URL` / `config.LIVE_STREAM_URL` / etc. live
    at call time (not at import time), so patching the shared `config` module
    object before `generate_session()`/`ws_connect()` run is sufficient -- no
    per-instance override exists on `BreezeConnect` itself.

    `ws_market_mode`, if given, is appended as `?market_mode=...` to only the
    three Socket.IO base URLs (never `API_URL`/`BREEZE_NEW_URL`, which have a
    REST path appended after them and would break if given a query string) --
    this drives the mock server's per-connection LIVE/OFF_MARKET override.
    """
    fields = ("API_URL", "BREEZE_NEW_URL", "LIVE_FEEDS_URL", "LIVE_STREAM_URL", "LIVE_OHLC_STREAM_URL")
    originals = {name: getattr(_config, name) for name in fields}
    ws_base = base_url if not ws_market_mode else f"{base_url}?market_mode={ws_market_mode}"
    try:
        _config.API_URL = base_url.rstrip("/") + "/breezeapi/api/v1/"
        _config.BREEZE_NEW_URL = base_url.rstrip("/") + "/api/v2/"
        _config.LIVE_FEEDS_URL = ws_base
        _config.LIVE_STREAM_URL = ws_base
        _config.LIVE_OHLC_STREAM_URL = ws_base
        yield
    finally:
        for name, value in originals.items():
            setattr(_config, name, value)


def seed_security_master(breeze) -> None:
    """Populate a `BreezeConnect` instance's in-memory scrip-master lookup tables.

    Normally filled in by `get_stock_script_list()` from the real
    SecurityMaster.zip (which the offline patch above turns into an empty
    archive). Without this, `subscribe_feeds(exchange_code=..., stock_code=...)`
    can't resolve a WS token and tick enrichment
    (`get_data_from_stock_token_value`) can't attach `stock_name`/`expiry_date`/
    `strike_price`/`right` -- both silently degrade to "not found" rather than
    raising, so the gap is easy to miss without this seeding.
    """
    stock_script = breeze.stock_script_dict_list = [{}, {}, {}, {}, {}, {}]
    token_script = breeze.token_script_dict_list = [{}, {}, {}, {}, {}, {}]

    # index map: 0=BSE, 1=NSE, 2=NDX, 3=MCX, 4=NFO, 5=BFO
    for inst in ALL_INSTRUMENTS:
        if inst.kind == "equity":
            idx = 1 if inst.exchange_code == "NSE" else 0
            stock_script[idx][inst.stock_code] = inst.token
            token_script[idx][inst.token] = [inst.stock_code, inst.company_name]
        else:
            idx = 5 if inst.exchange_code == "BFO" else 4
            stock_script[idx][inst.contract_name] = inst.token
            token_script[idx][inst.token] = [inst.contract_name, inst.company_name]


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockBreezeServerHandle:
    def __init__(self, base_url: str, state, server) -> None:
        self.base_url = base_url
        self.state = state
        self._server = server

    def set_mode(self, mode: str) -> None:
        self.state.mode = mode

    def stop(self) -> None:
        self._server.should_exit = True


@contextlib.contextmanager
def run_mock_breeze_server(mode: str | None = None):
    """Start the mock server (tests/mock_breeze_server.py) in a background thread.

    `breeze_connect`'s Socket.IO *client* is synchronous/blocking, so the mock
    server can't share the test's own thread/event loop -- it needs its own,
    exactly like any other "app under test" a synchronous test talks to over
    a real socket.
    """
    import uvicorn

    from tests.mock_breeze_server import create_app

    port = _free_port()
    _, asgi_app, state = create_app(mode=mode)
    config = uvicorn.Config(asgi_app, host="127.0.0.1", port=port, log_level="warning", ws="wsproto")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    started = False
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/healthz", timeout=0.5)
            if resp.status_code == 200:
                started = True
                break
        except (httpx.ConnectError, httpx.ReadTimeout):
            time.sleep(0.05)
    if not started:
        raise RuntimeError("mock breeze server did not become healthy in time")

    handle = MockBreezeServerHandle(base_url, state, server)
    try:
        yield handle
    finally:
        handle.stop()
        thread.join(timeout=5)


def decode_session_token(base64_session_token: str) -> tuple[str, str]:
    """Inverse of the mock server's `customerdetails` response -- (user_id, session_key)."""
    raw = base64.b64decode(base64_session_token.encode("ascii")).decode("ascii")
    user_id, session_key = raw.split(":", 1)
    return user_id, session_key
