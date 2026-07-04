"""Standalone local mock of ICICI Breeze's REST + Socket.IO backend.

Runs as a normal FastAPI/uvicorn ASGI app. It does **not** implement a raw
`websockets`/FastAPI `@app.websocket` endpoint for ticks: the real
`breeze_connect` SDK's `ws_connect()`/`subscribe_feeds()` are built on
`python-socketio`'s `socketio.Client()` (Engine.IO/Socket.IO protocol, not
bare WS frames), so this mock runs a real `python-socketio` ASGI server
side-by-side with the FastAPI REST routes on one port.

Auth is intentionally permissive: any `api_key`/`api_secret`/`session_token`
is accepted and echoed back into a validly-shaped success response, so
tests can use random dummy credentials.

Dependencies: `fastapi`/`uvicorn` are already in backend/requirements.txt;
`python-socketio`/`python-engineio`/`wsproto` come in transitively via
`breeze-connect` itself and are already in `backend/.venv`. The one gap in a
freshly-created venv is uvicorn's WS transport: plain `uvicorn==0.24.0` (no
`[standard]` extra) has no `websockets`/`wsproto`-backed WS support unless one
of those packages is installed separately -- `wsproto` happens to already be
present transitively, but if `uvicorn.Config(..., ws="wsproto")` ever fails to
find a usable WS implementation, `pip install websockets` into `backend/.venv`.

Run standalone:
    MOCK_MARKET_MODE=LIVE python tests/mock_breeze_server.py [--port 8000]

Two streaming modes (see `MockServerState.mode`), selectable via the
`MOCK_MARKET_MODE` env var, the `?market_mode=` query string on the Socket.IO
connection URL, or `POST /__mock__/set_mode` for tests that hold a handle to
a running server:
  - LIVE: random-walk ticks every 500ms (real market hours simulation).
  - OFF_MARKET: frozen last-known-close snapshot, replayed on the same cadence
    (end-of-day / off-market reconciliation simulation), sourced from a small
    hardcoded OHLCV "bhavcopy" catalog (see tests/fixtures/mock_instruments.py).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import os
import urllib.parse
from datetime import date, datetime, time as dtime

import socketio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from icici_breeze_backend.dev.mock_market_data import seed_running_state, step_live_tick_fields
from tests.fixtures.mock_instruments import ALL_INSTRUMENTS, Instrument, wire_exchange_prefix

LIVE = "LIVE"
OFF_MARKET = "OFF_MARKET"
_VALID_MODES = (LIVE, OFF_MARKET)


class MockServerState:
    def __init__(self, mode: str | None = None) -> None:
        self.mode = _normalize_mode(mode or os.environ.get("MOCK_MARKET_MODE") or LIVE)
        self.room_members: dict[str, set[str]] = {}
        self.connections: dict[str, dict] = {}
        self.join_log: list[tuple[str, list[str]]] = []
        self.live_state: dict[str, dict] = {}
        self.off_market_timestamp = int(
            datetime.combine(date.today(), dtime(15, 30, 0)).timestamp()
        )

    def active_rooms(self) -> list[str]:
        """Wire-symbol room names (e.g. "4.1!71474") with at least one subscriber."""
        return [room for room, members in self.room_members.items() if members]


def _normalize_mode(mode: str) -> str:
    mode = (mode or LIVE).strip().upper()
    return mode if mode in _VALID_MODES else LIVE


def _pack(kind: str, f: dict) -> list:
    base = [
        f["symbol"], f["open"], f["last"], f["high"], f["low"], f["change"],
        f["bPrice"], f["bQty"], f["sPrice"], f["sQty"], f["ltq"], f["avgPrice"],
    ]
    if kind == "equity":
        return base + [
            f["ttq"], f["totalBuyQt"], f["totalSellQ"], f["ttv"], f["trend"],
            f["lowerCktLm"], f["upperCktLm"], f["ltt"], f["close"],
        ]
    return base + [
        f["OI"], f["CHNGOI"], f["ttq"], f["totalBuyQt"], f["totalSellQ"], f["ttv"],
        f["trend"], f["lowerCktLm"], f["upperCktLm"], f["ltt"], f["close"],
    ]


def _symbol_for(inst: Instrument) -> str:
    return f"{wire_exchange_prefix(inst)}.1!{inst.token}"


# The room a client actually joins is the full wire symbol (e.g. "4.1!71474"),
# not the bare instrument token -- that's what the background pusher must key on.
SYMBOL_TO_INSTRUMENT: dict[str, Instrument] = {_symbol_for(inst): inst for inst in ALL_INSTRUMENTS}


def _live_tick(inst: Instrument, state: MockServerState) -> list:
    st = state.live_state.setdefault(
        inst.token,
        seed_running_state(
            inst.token,
            base_price=inst.last,
        )
        | {"high": inst.day_high, "low": inst.day_low, "open": inst.day_open, "prev_close": inst.prev_close},
    )
    fields = step_live_tick_fields(_symbol_for(inst), st, lot_size=inst.lot_size)
    return _pack(inst.kind, fields)


def _off_market_tick(inst: Instrument, state: MockServerState) -> list:
    """Frozen last-known-close snapshot -- identical fields every broadcast."""
    fields = dict(
        symbol=_symbol_for(inst), open=inst.day_open, last=inst.last, high=inst.day_high, low=inst.day_low,
        change=round(inst.last - inst.prev_close, 2),
        bPrice=round(inst.last - 0.05, 2), bQty=0, sPrice=round(inst.last + 0.05, 2), sQty=0,
        ltq=0, avgPrice=round((inst.day_high + inst.day_low) / 2, 2),
        ttq=8_000_000, totalBuyQt=4_400_000, totalSellQ=3_600_000, ttv="", trend="-",
        lowerCktLm=round(inst.prev_close * 0.9, 2), upperCktLm=round(inst.prev_close * 1.1, 2),
        ltt=state.off_market_timestamp, close=inst.last,
        OI=12_000_000, CHNGOI=0,
    )
    return _pack(inst.kind, fields)


def build_tick(inst: Instrument, state: MockServerState) -> list:
    return _off_market_tick(inst, state) if state.mode == OFF_MARKET else _live_tick(inst, state)


def _mock_session_token_response(session_token: str, app_key: str) -> dict:
    user_id = "MOCKUSER1"
    combined = f"{user_id}:{session_token or 'mock-session-token'}"
    b64 = base64.b64encode(combined.encode("ascii")).decode("ascii")
    return {
        "Success": {
            "session_token": b64,
            "idirect_userid": user_id,
            "idirect_user_name": "Mock Test User",
            "idirect_usertype": "Trading",
            "broker_name": "MOCKBROKER",
            "app_key_echo": app_key,
        },
        "Status": 200,
        "Error": None,
    }


def create_app(mode: str | None = None, *, tick_interval: float = 0.5, verbose: bool = False) -> tuple[FastAPI, "socketio.ASGIApp", MockServerState]:
    """Build the combined FastAPI + Socket.IO mock app. Returns (fastapi_app, asgi_app, state)."""
    state = MockServerState(mode=mode)
    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins="*",
        logger=verbose,
        engineio_logger=verbose,
    )

    async def _tick_loop() -> None:
        while True:
            await asyncio.sleep(tick_interval)
            for room in state.active_rooms():
                inst = SYMBOL_TO_INSTRUMENT.get(room)
                if inst is None:
                    continue
                await sio.emit("stock", build_tick(inst, state), room=room)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(_tick_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    fastapi_app = FastAPI(title="mock-breeze-server", lifespan=lifespan)

    @fastapi_app.api_route("/breezeapi/api/v1/customerdetails", methods=["GET", "POST"])
    async def customerdetails(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        session_token = str((payload or {}).get("SessionToken") or "")
        app_key = str((payload or {}).get("AppKey") or "")
        return JSONResponse(_mock_session_token_response(session_token, app_key))

    @fastapi_app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "mode": state.mode}

    @fastapi_app.get("/__mock__/state")
    async def debug_state() -> dict:
        return {
            "mode": state.mode,
            "active_rooms": state.active_rooms(),
            "join_log": state.join_log[-50:],
            "connections": list(state.connections.keys()),
        }

    @fastapi_app.post("/__mock__/set_mode")
    async def set_mode(request: Request) -> dict:
        body = await request.json()
        state.mode = _normalize_mode(str(body.get("mode", LIVE)))
        return {"mode": state.mode}

    @sio.event
    async def connect(sid, environ, auth=None):  # noqa: ANN001 - python-socketio callback signature
        qs = environ.get("QUERY_STRING") or environ.get("asgi.scope", {}).get("query_string", b"")
        if isinstance(qs, bytes):
            qs = qs.decode()
        params = urllib.parse.parse_qs(qs or "")
        mode_override = (params.get("market_mode") or [None])[0]
        if mode_override:
            state.mode = _normalize_mode(mode_override)
        state.connections[sid] = {"auth": auth or {}}
        return True

    @sio.event
    async def join(sid, data):
        tokens = [str(t) for t in data] if isinstance(data, list) else [str(data)]
        for token in tokens:
            await sio.enter_room(sid, token)
            state.room_members.setdefault(token, set()).add(sid)
        state.join_log.append((sid, tokens))

    @sio.event
    async def leave(sid, data):
        tokens = [str(t) for t in data] if isinstance(data, list) else [str(data)]
        for token in tokens:
            await sio.leave_room(sid, token)
            state.room_members.get(token, set()).discard(sid)

    @sio.event
    async def disconnect(sid):
        state.connections.pop(sid, None)
        for members in state.room_members.values():
            members.discard(sid)

    asgi_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
    return fastapi_app, asgi_app, state


_fastapi_app, asgi_app, _state = create_app()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOCK_BREEZE_PORT", "8000")))
    parser.add_argument("--mode", default=None, choices=list(_VALID_MODES))
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    _fastapi_app, asgi_app, _state = create_app(mode=args.mode)
    print(f"Instruments available: {[i.stock_code + ':' + i.token for i in ALL_INSTRUMENTS]}")
    print(f"Starting mock Breeze server on http://{args.host}:{args.port} (mode={_state.mode})")
    uvicorn.run(asgi_app, host=args.host, port=args.port, log_level="info", ws="wsproto")
