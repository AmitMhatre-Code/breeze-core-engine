"""Breeze-connect REST API catalog for Settings → Breeze API Playground."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

RiskLevel = Literal["read", "trade", "funds", "gtt"]
ParamType = Literal["string", "json"]


@dataclass(frozen=True)
class BreezeApiParamDef:
    name: str
    label: str
    type: ParamType = "string"
    required: bool = False
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class BreezeApiCatalogEntry:
    method: str
    title: str
    risk_level: RiskLevel
    params: tuple[BreezeApiParamDef, ...] = ()
    description: str = ""
    notes: str = ""


def _s(
    name: str,
    label: str | None = None,
    *,
    required: bool = False,
    placeholder: str = "",
    help: str = "",
) -> BreezeApiParamDef:
    return BreezeApiParamDef(
        name=name,
        label=label or name.replace("_", " ").title(),
        type="string",
        required=required,
        placeholder=placeholder,
        help=help,
    )


def _j(
    name: str,
    label: str,
    *,
    required: bool = True,
    placeholder: str = "[]",
    help: str = "",
) -> BreezeApiParamDef:
    return BreezeApiParamDef(
        name=name,
        label=label,
        type="json",
        required=required,
        placeholder=placeholder,
        help=help,
    )


_HIST_PARAMS = (
    _s("interval", required=True, placeholder="1minute"),
    _s("from_date", required=True, placeholder="2025-02-03T09:21:00.000Z"),
    _s("to_date", required=True, placeholder="2025-02-03T09:22:00.000Z"),
    _s("stock_code", required=True, placeholder="NIFTY"),
    _s("exchange_code", required=True, placeholder="NFO"),
    _s("product_type", placeholder="futures"),
    _s("expiry_date", placeholder="2025-02-27T07:00:00.000Z"),
    _s("right", placeholder="others"),
    _s("strike_price", placeholder="0"),
)

_PLACE_ORDER_PARAMS = (
    _s("stock_code", required=True),
    _s("exchange_code", required=True, placeholder="NFO"),
    _s("product", required=True, placeholder="futures"),
    _s("action", required=True, placeholder="buy"),
    _s("order_type", required=True, placeholder="limit"),
    _s("stoploss", placeholder="0"),
    _s("quantity", required=True, placeholder="75"),
    _s("price", required=True, placeholder="23700"),
    _s("validity", placeholder="day"),
    _s("validity_date", placeholder="2025-02-05T06:00:00.000Z"),
    _s("disclosed_quantity", placeholder="0"),
    _s("expiry_date", placeholder="2025-02-27T06:00:00.000Z"),
    _s("right", placeholder="call"),
    _s("strike_price", placeholder="24800"),
    _s("user_remark", placeholder=""),
)

_SQUARE_OFF_PARAMS = (
    _s("exchange_code", required=True, placeholder="NFO"),
    _s("product", required=True, placeholder="options"),
    _s("stock_code", required=True, placeholder="NIFTY"),
    _s("expiry_date", placeholder="2025-02-27T06:00:00.000Z"),
    _s("right", placeholder="Call"),
    _s("strike_price", placeholder="24000"),
    _s("action", required=True, placeholder="sell"),
    _s("order_type", placeholder="limit"),
    _s("validity", placeholder="day"),
    _s("stoploss", placeholder="0"),
    _s("quantity", required=True, placeholder="75"),
    _s("price", placeholder="0"),
    _s("validity_date", placeholder="2025-02-05T06:00:00.000Z"),
    _s("trade_password", placeholder=""),
    _s("disclosed_quantity", placeholder="0"),
)

_CATALOG: tuple[BreezeApiCatalogEntry, ...] = (
    BreezeApiCatalogEntry(
        method="get_customer_details",
        title="Get Customer Details",
        risk_level="read",
        description="Customer profile and exchange status using your logged-in broker session.",
    ),
    BreezeApiCatalogEntry(
        method="get_demat_holdings",
        title="Get Demat Holdings",
        risk_level="read",
        description="Demat holding details.",
    ),
    BreezeApiCatalogEntry(
        method="get_funds",
        title="Get Funds",
        risk_level="read",
        description="Fund allocation and balances.",
    ),
    BreezeApiCatalogEntry(
        method="set_funds",
        title="Set Funds",
        risk_level="funds",
        params=(
            _s("transaction_type", required=True, placeholder="debit", help='Use "credit" to add funds.'),
            _s("amount", required=True, placeholder="200"),
            _s("segment", required=True, placeholder="Equity", help="Equity, FNO, or Commodity"),
        ),
        notes="Moves funds between segments. Use with extreme care.",
    ),
    BreezeApiCatalogEntry(
        method="get_historical_data",
        title="Get Historical Data",
        risk_level="read",
        params=_HIST_PARAMS,
        notes="Interval: 1minute, 5minute, 30minute, or 1day.",
    ),
    BreezeApiCatalogEntry(
        method="get_historical_data_v2",
        title="Get Historical Data V2",
        risk_level="read",
        params=_HIST_PARAMS,
        notes="Interval: 1minute, 5minute, 30minute, or 1day.",
    ),
    BreezeApiCatalogEntry(
        method="get_margin",
        title="Get Margin",
        risk_level="read",
        params=(_s("exchange_code", required=True, placeholder="NSE"),),
    ),
    BreezeApiCatalogEntry(
        method="place_order",
        title="Place Order",
        risk_level="trade",
        params=_PLACE_ORDER_PARAMS,
        notes="Market orders are converted to aggressive limit orders per ICICI policy.",
    ),
    BreezeApiCatalogEntry(
        method="get_order_detail",
        title="Get Order Detail",
        risk_level="read",
        params=(
            _s("exchange_code", required=True, placeholder="NSE"),
            _s("order_id", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="get_order_list",
        title="Get Order List",
        risk_level="read",
        params=(
            _s("exchange_code", required=True, placeholder="NSE"),
            _s("from_date", required=True, placeholder="2025-02-05T10:00:00.000Z"),
            _s("to_date", required=True, placeholder="2025-02-05T10:00:00.000Z"),
        ),
    ),
    BreezeApiCatalogEntry(
        method="cancel_order",
        title="Cancel Order",
        risk_level="trade",
        params=(
            _s("exchange_code", required=True, placeholder="NSE"),
            _s("order_id", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="modify_order",
        title="Modify Order",
        risk_level="trade",
        params=(
            _s("order_id", required=True),
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("order_type", placeholder="limit"),
            _s("stoploss", placeholder="0"),
            _s("quantity", required=True, placeholder="75"),
            _s("price", required=True, placeholder="0.30"),
            _s("validity", placeholder="day"),
            _s("disclosed_quantity", placeholder="0"),
            _s("validity_date", placeholder="2025-02-05T06:00:00.000Z"),
        ),
    ),
    BreezeApiCatalogEntry(
        method="get_portfolio_holdings",
        title="Get Portfolio Holdings",
        risk_level="read",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("from_date", placeholder="2024-08-01T06:00:00.000Z"),
            _s("to_date", placeholder="2024-09-19T06:00:00.000Z"),
            _s("stock_code", placeholder=""),
            _s("portfolio_type", placeholder=""),
        ),
    ),
    BreezeApiCatalogEntry(
        method="get_portfolio_positions",
        title="Get Portfolio Positions",
        risk_level="read",
    ),
    BreezeApiCatalogEntry(
        method="get_quotes",
        title="Get Quotes",
        risk_level="read",
        params=(
            _s("stock_code", required=True, placeholder="NIFTY"),
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("expiry_date", placeholder="2025-02-27T06:00:00.000Z"),
            _s("product_type", placeholder="futures"),
            _s("right", placeholder="others"),
            _s("strike_price", placeholder="0"),
        ),
    ),
    BreezeApiCatalogEntry(
        method="get_option_chain_quotes",
        title="Get Option Chain Quotes",
        risk_level="read",
        params=(
            _s("stock_code", required=True, placeholder="NIFTY"),
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("product_type", placeholder="options"),
            _s("right", placeholder="call"),
            _s("expiry_date", placeholder="2025-08-28T06:00:00.000Z"),
            _s("strike_price", help="Optional; omit for full chain when combined with expiry/right."),
        ),
    ),
    BreezeApiCatalogEntry(
        method="square_off",
        title="Square Off",
        risk_level="trade",
        params=_SQUARE_OFF_PARAMS,
        notes="Squares off an open position. Can place real orders.",
    ),
    BreezeApiCatalogEntry(
        method="get_trade_list",
        title="Get Trade List",
        risk_level="read",
        params=(
            _s("from_date", required=True, placeholder="2025-02-05T06:00:00.000Z"),
            _s("to_date", required=True, placeholder="2025-02-05T06:00:00.000Z"),
            _s("exchange_code", required=True, placeholder="NSE"),
            _s("product_type", placeholder=""),
            _s("action", placeholder=""),
            _s("stock_code", placeholder=""),
        ),
    ),
    BreezeApiCatalogEntry(
        method="get_trade_detail",
        title="Get Trade Detail",
        risk_level="read",
        params=(
            _s("exchange_code", required=True, placeholder="NSE"),
            _s("order_id", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="get_names",
        title="Get Names",
        risk_level="read",
        params=(
            _s("exchange_code", required=True, placeholder="NSE"),
            _s("stock_code", required=True, placeholder="TATASTEEL"),
        ),
        description="ICICI stock codes and tokens for a symbol.",
    ),
    BreezeApiCatalogEntry(
        method="preview_order",
        title="Preview Order",
        risk_level="read",
        params=(
            _s("stock_code", required=True, placeholder="ITC"),
            _s("exchange_code", required=True, placeholder="NSE"),
            _s("product", required=True, placeholder="margin"),
            _s("order_type", required=True, placeholder="limit"),
            _s("price", required=True, placeholder="440"),
            _s("action", required=True, placeholder="buy"),
            _s("quantity", required=True, placeholder="1"),
            _s("specialflag", placeholder="N"),
        ),
    ),
    BreezeApiCatalogEntry(
        method="limit_calculator",
        title="Limit Calculator",
        risk_level="read",
        params=(
            _s("strike_price", required=True, placeholder="24000"),
            _s("product_type", required=True, placeholder="options"),
            _s("expiry_date", required=True, placeholder="06-Feb-2025"),
            _s("underlying", required=True, placeholder="NIFTY"),
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("order_flow", required=True, placeholder="Buy"),
            _s("stop_loss_trigger", placeholder="8"),
            _s("option_type", placeholder="Call"),
            _s("source_flag", placeholder="P"),
            _s("limit_rate", placeholder="7.5"),
            _s("order_reference", placeholder=""),
            _s("available_quantity", placeholder=""),
            _s("market_type", placeholder="limit"),
            _s("fresh_order_limit", placeholder="10.95"),
        ),
    ),
    BreezeApiCatalogEntry(
        method="margin_calculator",
        title="Margin Calculator",
        risk_level="read",
        params=(
            _j(
                "margin_list",
                "Margin list (JSON array)",
                placeholder='[{"stock_code":"NIFTY","quantity":"75","product":"futures","action":"buy","price":"23400","expiry_date":"27-Feb-2025","right":"others","strike_price":"0"}]',
                help="Array of leg objects per breeze-connect docs.",
            ),
            _s("exchange_code", placeholder="NFO"),
        ),
    ),
    BreezeApiCatalogEntry(
        method="gtt_three_leg_place_order",
        title="GTT Three Leg Place Order",
        risk_level="gtt",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("stock_code", required=True, placeholder="NIFTY"),
            _s("product", required=True, placeholder="options"),
            _s("quantity", required=True, placeholder="75"),
            _s("expiry_date", required=True),
            _s("right", required=True, placeholder="call"),
            _s("strike_price", required=True, placeholder="24000"),
            _s("gtt_type", required=True, placeholder="cover_oco"),
            _s("fresh_order_action", placeholder="buy"),
            _s("fresh_order_price", placeholder="8"),
            _s("fresh_order_type", placeholder="limit"),
            _s("index_or_stock", placeholder="index"),
            _s("trade_date", placeholder="2025-02-05T06:00:00.00Z"),
            _j("order_details", "Order details (JSON array)", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="gtt_three_leg_modify_order",
        title="GTT Three Leg Modify Order",
        risk_level="gtt",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("gtt_order_id", required=True),
            _s("gtt_type", required=True, placeholder="oco"),
            _j("order_details", "Order details (JSON array)", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="gtt_three_leg_cancel_order",
        title="GTT Three Leg Cancel Order",
        risk_level="gtt",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("gtt_order_id", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="gtt_single_leg_place_order",
        title="GTT Single Leg Place Order",
        risk_level="gtt",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("stock_code", required=True, placeholder="NIFTY"),
            _s("product", required=True, placeholder="options"),
            _s("quantity", required=True, placeholder="75"),
            _s("expiry_date", required=True),
            _s("right", required=True, placeholder="call"),
            _s("strike_price", required=True, placeholder="24000"),
            _s("gtt_type", required=True, placeholder="single"),
            _s("index_or_stock", placeholder="index"),
            _s("trade_date", placeholder="2025-02-05T06:00:00.00Z"),
            _j("order_details", "Order details (JSON array)", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="gtt_single_leg_modify_order",
        title="GTT Single Leg Modify Order",
        risk_level="gtt",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("gtt_order_id", required=True),
            _s("gtt_type", required=True, placeholder="single"),
            _j("order_details", "Order details (JSON array)", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="gtt_single_leg_cancel_order",
        title="GTT Single Leg Cancel Order",
        risk_level="gtt",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("gtt_order_id", required=True),
        ),
    ),
    BreezeApiCatalogEntry(
        method="gtt_order_book",
        title="GTT Order Book",
        risk_level="read",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("from_date", required=True, placeholder="2025-02-05T06:00:00.00Z"),
            _s("to_date", required=True, placeholder="2025-02-05T06:00:00.00Z"),
        ),
    ),
    BreezeApiCatalogEntry(
        method="ws_connect",
        title="WebSocket Connect",
        risk_level="read",
        description="Open Breeze tick-by-tick WebSocket (market hours).",
        notes="Use Settings playground WebSocket tab for live SSE stream after connect.",
    ),
    BreezeApiCatalogEntry(
        method="ws_disconnect",
        title="WebSocket Disconnect",
        risk_level="read",
    ),
    BreezeApiCatalogEntry(
        method="subscribe_feeds",
        title="Subscribe Feeds (NFO/BFO Options)",
        risk_level="read",
        params=(
            _s("exchange_code", required=True, placeholder="NFO"),
            _s("stock_code", required=True, placeholder="NIFTY"),
            _s("expiry_date", required=True, placeholder="27-Feb-2025"),
            _s("strike_price", required=True, placeholder="24000"),
            _s("right", required=True, placeholder="call"),
        ),
        notes="Sets get_exchange_quotes=True for buy/sell qty. Use /breeze-api-tester/ws/subscribe in playground.",
    ),
)

_METHOD_INDEX: dict[str, BreezeApiCatalogEntry] = {e.method: e for e in _CATALOG}
ALLOWED_METHODS: frozenset[str] = frozenset(_METHOD_INDEX.keys())


def get_catalog_entries() -> list[BreezeApiCatalogEntry]:
    return list(_CATALOG)


def get_catalog_entry(method: str) -> BreezeApiCatalogEntry | None:
    return _METHOD_INDEX.get((method or "").strip())


def catalog_entry_to_dict(entry: BreezeApiCatalogEntry) -> dict[str, Any]:
    return {
        "method": entry.method,
        "title": entry.title,
        "risk_level": entry.risk_level,
        "description": entry.description,
        "notes": entry.notes,
        "params": [asdict(p) for p in entry.params],
    }


def get_catalog_response() -> list[dict[str, Any]]:
    return [catalog_entry_to_dict(e) for e in _CATALOG]


def _parse_json_field(name: str, raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for {name}: {exc}") from exc


def build_invoke_args(method: str, params: dict[str, Any]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Return (positional_args, kwargs) for getattr(breeze, method)."""
    entry = get_catalog_entry(method)
    if entry is None:
        raise ValueError(f"Unknown method: {method}")

    allowed_names = {p.name for p in entry.params}
    kwargs: dict[str, Any] = {}
    positional: list[Any] = []

    for pname, pdef in ((p.name, p) for p in entry.params):
        if pname not in params:
            if pdef.required:
                raise ValueError(f"Missing required parameter: {pname}")
            continue
        raw = params[pname]
        if raw is None:
            continue
        if isinstance(raw, str) and not raw.strip():
            if pdef.required:
                raise ValueError(f"Missing required parameter: {pname}")
            continue

        if pdef.type == "json":
            val = raw if not isinstance(raw, str) else _parse_json_field(pname, raw.strip())
        else:
            val = str(raw).strip() if raw is not None else ""

        if method == "margin_calculator" and pname == "margin_list":
            positional.append(val)
        else:
            kwargs[pname] = val

    extra = set(params.keys()) - allowed_names
    if extra:
        raise ValueError(f"Unknown parameters: {', '.join(sorted(extra))}")

    if method == "margin_calculator" and not positional:
        raise ValueError("margin_list is required for margin_calculator")

    return tuple(positional), kwargs


def _coerce_param_value_permissive(pdef: Any, raw: Any) -> Any:
    if pdef.type == "json":
        if raw is None:
            return ""
        if not isinstance(raw, str):
            return raw
        stripped = raw.strip()
        if not stripped:
            return ""
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return raw
    if raw is None:
        return ""
    return str(raw).strip() if isinstance(raw, str) else str(raw)


def build_invoke_args_permissive(
    method: str, params: dict[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Playground-only: pass catalog params to SDK without required/extra validation."""
    entry = get_catalog_entry(method)
    if entry is None:
        raise ValueError(f"Unknown method: {method}")

    kwargs: dict[str, Any] = {}
    positional: list[Any] = []
    params = dict(params or {})

    for pdef in entry.params:
        pname = pdef.name
        val = _coerce_param_value_permissive(pdef, params.get(pname))
        if method == "margin_calculator" and pname == "margin_list":
            positional.append(val)
        else:
            kwargs[pname] = val

    return tuple(positional), kwargs


def is_breeze_invoke_response_ok(result: Any) -> bool:
    if isinstance(result, str):
        return "exception" not in result.lower()
    if isinstance(result, dict):
        st = result.get("Status") or result.get("status")
        if st not in (200, None):
            return False
    return True
