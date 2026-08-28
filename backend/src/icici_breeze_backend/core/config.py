import os

# Resolve backend/data from package location so paths work when cwd is repo root (./dev.sh) or backend/.
_core_dir = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.abspath(os.path.join(_core_dir, "..", "..", ".."))
DATA_PATH = os.path.join(_BACKEND_ROOT, "data") + os.sep
# Empty DB templates live here (Dockerfile copies from data/). Not under DATA_PATH so a bind mount
# on .../data does not hide templates (common in production with -v host:/app/backend/data).
DB_TEMPLATE_PATH = os.path.join(_BACKEND_ROOT, "db-templates") + os.sep
USERS_DB = "users.sqlite3"
SCRIP_DB = "scrips.sqlite3"
SCRIPS_EMPTY_DB = "scrips.empty.sqlite3"
USERS_EMPTY_DB = "users.empty.sqlite3"
ICICI_MASTERFILE_URL = "https://directlink.icicidirect.com/NewSecurityMaster/SecurityMaster.zip"
SCRIP_MASTER = "FONSEScripMaster.txt"  # legacy alias for NSE scrip master
LIMITS_MASTER = "NSEFreezeLimits.txt"  # legacy alias for NSE quantity limits

# Constants
ELM = 0.02

# Tiered ELM rates (index vs. single-stock, standard vs. deep-OTM). The stock "standard" tier is a
# flat-rate approximation of ICICI's true 5%-or-1.5x-6mo-volatility rule — this codebase has no
# historical daily-close pipeline to compute the volatility-based alternative.
ELM_INDEX_STD = 0.02
ELM_INDEX_DEEP_OTM = 0.03
ELM_INDEX_DEEP_OTM_THRESHOLD = 0.10
ELM_STOCK_STD = 0.05
ELM_STOCK_DEEP_OTM = 0.0525
ELM_STOCK_DEEP_OTM_THRESHOLD = 0.30

# Index underlyings with listed F&O contracts. Used to classify a stock_code as index vs. single-stock
# (e.g. for ELM tiering and GTT order placement's index_or_stock param) since no scrip-master-backed
# classification exists.
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}


def is_index_symbol(stock_code: str) -> bool:
    return str(stock_code).strip().upper() in INDEX_SYMBOLS

# Product Types
OPTIONS = "Options"
LIMIT = "limit"
MARKET = "market"
STOCK = "Stock"
INDEX = "Index"

# Actions
BUY = "Buy"
SELL = "Sell"
CLEAR = "Clear"
QUOTE = "Quote"
CANCEL = "Cancel"
OPTIMIZE = "Optimize"
SQUAREOFF = "SquareOff"
LOGIN = "Login"
LOGOUT = "Logout"
SUBMIT = "Submit"
CHECKED = "on"

# Session Actions
GET = "Get"
SET = "Set"
DESTROY = "Destroy"

# Rights
CALL = "Call"
PUT = "Put"

# Exchanges
NFO = "NFO"
NSE = "NSE"
BFO = "BFO"

# Hedging: allowlist of underlying short codes by options segment.
# Keys are segment exchange codes used across the app (NFO for NSE options, BFO for BSE options).
HEDGEABLE_UNDERLYINGS: dict[str, set[str]] = {
    NFO: {"NIFTY", "CNXBAN"},
    BFO: {"BSESEN", "BANKEX"},
}

# Master files
# NSE options scrip master and qty limits file
SCRIP_MASTER_NSE = "FONSEScripMaster.txt"
LIMITS_MASTER_NSE = "NSEFreezeLimits.txt"

# BSE options scrip master and qty limits file
SCRIP_MASTER_BSE = "FOBSEScripMaster.txt"
LIMITS_MASTER_BSE = "BSEFreezeLimits.txt"

# Messages
SUCCESS = "alert-success"
INFO = "alert-info"
WARNING = "alert-warning"
DANGER = "alert-danger"

# Order Statuses
REQUESTED = "Requested"
ORDERED = "Ordered"
# One secret for login (JWT) and credential encryption. Set JWT_SECRET or ENCRYPTION_KEY (or legacy JWT_SECRET_KEY) in the environment.
JWT_SECRET = (os.environ.get("JWT_SECRET") or os.environ.get("ENCRYPTION_KEY") or os.environ.get("JWT_SECRET_KEY") or "").strip()

# JWT access token lifetime in minutes (set JWT_ACCESS_TOKEN_EXPIRE_MINUTES in env; default 15).
try:
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15") or "15")
except ValueError:
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 15

# Google OAuth (registration, login, correction, delete)
GOOGLE_CLIENT_ID = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()

# When set (e.g. http://localhost:3000), ICICI login uses /icici-return for callback + challenge POST
# so cookies land on the Next origin; after login the user is redirected here + /dashboard.
PUBLIC_FRONTEND_ORIGIN = (os.environ.get("PUBLIC_FRONTEND_ORIGIN") or "").strip().rstrip("/")

# Google OAuth redirect_uri must match where the browser session cookie was set. When you use Next on
# :3000 with rewrites to the API on :8000, set this to the browser origin (e.g. http://localhost:3000).
# Leave unset if you open /auth/google only on the API host (same host as request.base_url).
GOOGLE_OAUTH_REDIRECT_BASE_URL = (os.environ.get("GOOGLE_OAUTH_REDIRECT_BASE_URL") or "").strip().rstrip("/")

# Cookie settings (ICICI broker token - HttpOnly). Set COOKIE_SECURE=true in production (HTTPS).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

# ICICI Client Configuration (Phase 5 US3)
ICICI_MAX_RETRIES = 3  # retry attempts for failed API calls
ICICI_TIMEOUT_SECONDS = 30.0  # timeout per API call

# Breeze session cache: TTL in seconds. If 0 or unset, cache uses "until midnight IST".
try:
    BREEZE_SESSION_CACHE_TTL_SECONDS = int(os.environ.get("BREEZE_SESSION_CACHE_TTL_SECONDS", "0") or "0")
except ValueError:
    BREEZE_SESSION_CACHE_TTL_SECONDS = 0

# Broker: `live` (default) uses ICICI Breeze; `mock` uses local fixtures (no outbound ICICI).
_ICICI_BROKER_MODE_RAW = (os.environ.get("ICICI_BROKER_MODE") or "live").strip().lower()
ICICI_BROKER_MODE = "mock" if _ICICI_BROKER_MODE_RAW == "mock" else "live"
# When mock + JWT valid but broker cookie missing, treat broker token as this value (opt-in).
ICICI_MOCK_SYNTHETIC_BROKER_TOKEN = os.environ.get("ICICI_MOCK_SYNTHETIC_BROKER_TOKEN", "").lower() in (
    "1",
    "true",
    "yes",
)
ICICI_MOCK_BROKER_COOKIE_VALUE = (os.environ.get("ICICI_MOCK_BROKER_COOKIE_VALUE") or "mock").strip() or "mock"

# Console portal: report successful app login against registered deployment (EC2 stack .env).
DEPLOYMENT_LICENSE_KEY = (os.environ.get("DEPLOYMENT_LICENSE_KEY") or "").strip()
PORTAL_API_BASE_URL = (os.environ.get("PORTAL_API_BASE_URL") or "").strip().rstrip("/")
DEPLOYMENT_GHCR_IMAGE = (os.environ.get("DEPLOYMENT_GHCR_IMAGE") or "").strip()
DEPLOYMENT_CONTAINER_NAME = (os.environ.get("DEPLOYMENT_CONTAINER_NAME") or "breeze-core-engine").strip()
# Host paths for in-place upgrade (docker.sock API on EC2; matches CFN bootstrap).
DEPLOYMENT_ENV_FILE = (os.environ.get("DEPLOYMENT_ENV_FILE") or "/opt/breeze-core-engine/.env").strip()
DEPLOYMENT_DATA_HOST_PATH = (
    os.environ.get("DEPLOYMENT_DATA_HOST_PATH") or "/opt/breeze-core-engine/data"
).strip()
_DEPLOYMENT_PUBLISH_PORT_RAW = (os.environ.get("DEPLOYMENT_PUBLISH_PORT") or "80").strip()
try:
    DEPLOYMENT_PUBLISH_PORT = int(_DEPLOYMENT_PUBLISH_PORT_RAW)
except ValueError:
    DEPLOYMENT_PUBLISH_PORT = 80
_PORTAL_HEARTBEAT_INTERVAL_RAW = (os.environ.get("PORTAL_HEARTBEAT_INTERVAL_SEC") or "300").strip()
try:
    PORTAL_HEARTBEAT_INTERVAL_SEC = max(60, int(_PORTAL_HEARTBEAT_INTERVAL_RAW))
except ValueError:
    PORTAL_HEARTBEAT_INTERVAL_SEC = 300

# Dev-only: force a license status locally without a portal/heartbeat round trip, to test
# read-only enforcement. One of active/expired/revoked/unlicensed/pending_activation/trial_denied;
# unset or unrecognized -> normal portal-driven behavior. Never set this in production.
_LICENSE_STATUS_OVERRIDE_RAW = (os.environ.get("LICENSE_STATUS_OVERRIDE") or "").strip().lower()
LICENSE_STATUS_OVERRIDE = (
    _LICENSE_STATUS_OVERRIDE_RAW
    if _LICENSE_STATUS_OVERRIDE_RAW
    in ("active", "expired", "revoked", "unlicensed", "pending_activation", "trial_denied")
    else ""
)

# Telegram alerts: BotFather token/username for stop-loss / profit-booking notifications.
TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_BOT_USERNAME = (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
# No TELEGRAM_POLL_TIMEOUT_SEC: this app no longer polls Telegram at all. Inbound
# linking is routed by the portal (telegram_link_portal.py) because Telegram allows
# only one getUpdates consumer per bot token and the token is shared fleet-wide.

EXPIRED = "Expired"
CANCELLED = "Cancelled"
EXECUTED = "Executed"
PARTIAL_EXECUTED = "Partially Executed"
PARTIAL_EXECUTED_EXPIRED = "Partially Executed And Expired"
PARTIAL_EXECUTED_CANCELED = "Partially Executed And Cancelled"

# Dashboard: economic calendar (AiTrados), news (optional)
AITRADOS_SECRET_KEY = (os.environ.get("AITRADOS_SECRET_KEY") or "").strip()
NEWS_API_KEY = (os.environ.get("NEWS_API_KEY") or "").strip()

# Redis (optional; in-memory fallback when unreachable)
REDIS_URL = (os.environ.get("REDIS_URL") or "").strip()
REDIS_HOST = (os.environ.get("REDIS_HOST") or "127.0.0.1").strip()
try:
    REDIS_PORT = int(os.environ.get("REDIS_PORT") or "6379")
except ValueError:
    REDIS_PORT = 6379
try:
    REDIS_MAXMEMORY_MB = max(64, int(os.environ.get("REDIS_MAXMEMORY_MB", "384") or "384"))
except ValueError:
    REDIS_MAXMEMORY_MB = 384
_redis_require_raw = os.environ.get("REDIS_REQUIRE_CONNECTED")
if _redis_require_raw is not None:
    REDIS_REQUIRE_CONNECTED = _redis_require_raw.strip().lower() in ("1", "true", "yes")
else:
    REDIS_REQUIRE_CONNECTED = bool(REDIS_URL)

# Reference data loads (bhavcopy, scrip master, SPAN baseline)
NSE_FO_BHAVCOPY_URL_TEMPLATE = (
    os.environ.get("NSE_FO_BHAVCOPY_URL_TEMPLATE")
    or "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
).strip()
BSE_FO_BHAVCOPY_URL_TEMPLATE = (
    os.environ.get("BSE_FO_BHAVCOPY_URL_TEMPLATE")
    or "https://www.bseindia.com/download/BhavCopy/Derivative/BhavCopy_BSE_FO_0_0_0_{yyyymmdd}_F_0000.CSV"
).strip()
try:
    REFERENCE_DATA_LOOKBACK_DAYS = int(os.environ.get("REFERENCE_DATA_LOOKBACK_DAYS", "10") or "10")
except ValueError:
    REFERENCE_DATA_LOOKBACK_DAYS = 10
try:
    REFERENCE_DATA_REFRESH_HOUR_IST = int(os.environ.get("REFERENCE_DATA_REFRESH_HOUR_IST", "18") or "18")
except ValueError:
    REFERENCE_DATA_REFRESH_HOUR_IST = 18
try:
    REFERENCE_DATA_REFRESH_MINUTE_IST = int(os.environ.get("REFERENCE_DATA_REFRESH_MINUTE_IST", "0") or "0")
except ValueError:
    REFERENCE_DATA_REFRESH_MINUTE_IST = 0
try:
    REFERENCE_DATA_REQUEST_TIMEOUT_SECONDS = float(
        os.environ.get("REFERENCE_DATA_REQUEST_TIMEOUT_SECONDS", "120") or "120"
    )
except ValueError:
    REFERENCE_DATA_REQUEST_TIMEOUT_SECONDS = 120.0
try:
    REFERENCE_DATA_CONNECT_TIMEOUT_SECONDS = float(
        os.environ.get("REFERENCE_DATA_CONNECT_TIMEOUT_SECONDS", "15") or "15"
    )
except ValueError:
    REFERENCE_DATA_CONNECT_TIMEOUT_SECONDS = 15.0
try:
    WEBSOCKET_QUOTE_TTL_SECONDS = int(os.environ.get("WEBSOCKET_QUOTE_TTL_SECONDS", "120") or "120")
except ValueError:
    WEBSOCKET_QUOTE_TTL_SECONDS = 120
try:
    WS_TICK_INGEST_QUEUE_SIZE = int(os.environ.get("WS_TICK_INGEST_QUEUE_SIZE", "2000") or "2000")
except ValueError:
    WS_TICK_INGEST_QUEUE_SIZE = 2000
try:
    WS_TICK_COALESCE_MS = int(os.environ.get("WS_TICK_COALESCE_MS", "100") or "100")
except ValueError:
    WS_TICK_COALESCE_MS = 100
try:
    WS_RAW_QUOTE_TTL_SECONDS = int(os.environ.get("WS_RAW_QUOTE_TTL_SECONDS", "120") or "120")
except ValueError:
    WS_RAW_QUOTE_TTL_SECONDS = 120
WS_QUOTE_SNAPSHOT_ENABLED = (
    os.environ.get("WS_QUOTE_SNAPSHOT_ENABLED", "true") or "true"
).strip().lower() not in ("0", "false", "no")
try:
    WS_QUOTE_SNAPSHOT_FLUSH_SECONDS = int(
        os.environ.get("WS_QUOTE_SNAPSHOT_FLUSH_SECONDS", "300") or "300"
    )
except ValueError:
    WS_QUOTE_SNAPSHOT_FLUSH_SECONDS = 300
try:
    WS_QUOTE_SNAPSHOT_RETENTION_DAYS = int(
        os.environ.get("WS_QUOTE_SNAPSHOT_RETENTION_DAYS", "5") or "5"
    )
except ValueError:
    WS_QUOTE_SNAPSHOT_RETENTION_DAYS = 5
try:
    CANONICAL_CHAIN_TTL_SECONDS = int(os.environ.get("CANONICAL_CHAIN_TTL_SECONDS", "5") or "5")
except ValueError:
    CANONICAL_CHAIN_TTL_SECONDS = 5
try:
    CHAIN_BUILDER_POLL_MS = int(os.environ.get("CHAIN_BUILDER_POLL_MS", "500") or "500")
except ValueError:
    CHAIN_BUILDER_POLL_MS = 500
try:
    BWS_SUBSCRIBE_BATCH_SIZE = int(os.environ.get("BWS_SUBSCRIBE_BATCH_SIZE", "50") or "50")
except ValueError:
    BWS_SUBSCRIBE_BATCH_SIZE = 50

# Debug-only: when set, every raw tick from the ICICI socket (quote ticks,
# order notifications, OHLC ticks -- breeze_connect funnels all of them
# through the same on_ticks callback) is appended as a JSON line to this
# path. Unset by default; only enable for a short troubleshooting session.
BWS_TICK_DEBUG_LOG_PATH = (os.environ.get("BWS_TICK_DEBUG_LOG_PATH") or "").strip()
try:
    CHAIN_WS_WAIT_TIMEOUT_MS = int(os.environ.get("CHAIN_WS_WAIT_TIMEOUT_MS", "8000") or "8000")
except ValueError:
    CHAIN_WS_WAIT_TIMEOUT_MS = 8000
try:
    # Whole-chain wait, deliberately much shorter than CHAIN_WS_WAIT_TIMEOUT_MS above:
    # that one governs the single-contract lookup used while pricing an order, where
    # patience is worth paying for. A chain's deep-OTM strikes may never trade all
    # session, so waiting 8s for them only ever stalled the screen.
    CHAIN_WS_CHAIN_WAIT_TIMEOUT_MS = int(
        os.environ.get("CHAIN_WS_CHAIN_WAIT_TIMEOUT_MS", "2000") or "2000"
    )
except ValueError:
    CHAIN_WS_CHAIN_WAIT_TIMEOUT_MS = 2000
try:
    CHAIN_WS_WAIT_POLL_MS = int(os.environ.get("CHAIN_WS_WAIT_POLL_MS", "100") or "100")
except ValueError:
    CHAIN_WS_WAIT_POLL_MS = 100
try:
    CHAIN_SPOT_CACHE_TTL_SECONDS = int(os.environ.get("CHAIN_SPOT_CACHE_TTL_SECONDS", "60") or "60")
except ValueError:
    CHAIN_SPOT_CACHE_TTL_SECONDS = 60
try:
    # Post-close, pre-bhavcopy REST fallback: market is closed for the whole
    # window this cache is read in, so a long TTL is safe -- it's a safety net
    # against a stuck reference-data pipeline, not a freshness knob.
    ICICI_REST_CHAIN_CACHE_TTL_SECONDS = int(
        os.environ.get("ICICI_REST_CHAIN_CACHE_TTL_SECONDS", "1200") or "1200"
    )
except ValueError:
    ICICI_REST_CHAIN_CACHE_TTL_SECONDS = 1200
try:
    PNL_QUOTE_FLUSH_INTERVAL_SECONDS = float(
        os.environ.get("PNL_QUOTE_FLUSH_INTERVAL_SECONDS", "2.0") or "2.0"
    )
except ValueError:
    PNL_QUOTE_FLUSH_INTERVAL_SECONDS = 2.0
try:
    PNL_QUOTE_TTL_SECONDS = int(os.environ.get("PNL_QUOTE_TTL_SECONDS", "30") or "30")
except ValueError:
    PNL_QUOTE_TTL_SECONDS = 30
try:
    PNL_ENGINE_INTERVAL_SECONDS = float(os.environ.get("PNL_ENGINE_INTERVAL_SECONDS", "2.0") or "2.0")
except ValueError:
    PNL_ENGINE_INTERVAL_SECONDS = 2.0
try:
    PNL_STALE_QUOTE_SECONDS = float(os.environ.get("PNL_STALE_QUOTE_SECONDS", "10") or "10")
except ValueError:
    PNL_STALE_QUOTE_SECONDS = 10.0
PNL_ENGINE_ENABLED = str(os.environ.get("PNL_ENGINE_ENABLED", "true")).strip().lower() not in (
    "0", "false", "no",
)

# Master gate for the aggressive-order feature (the ⚡ toggle on order forms). When on, users can
# pick one of two execution styles per order:
#   - "market": a native ICICI market order (order_type=market, price=0). ICICI has not yet enabled
#     native market/aggressive-limit orders, so these may be rejected upstream until they do.
#   - "limit_tolerance": an ordinary limit order priced off LTP by a user tolerance % (BUY at
#     LTP*(1+tol), SELL at LTP*(1-tol), tick-rounded). This needs nothing from ICICI and works today.
# Enabled by default now that "limit_tolerance" is a fully working, ICICI-independent path (the
# default mode); set AGGRESSIVE_LIMIT_ORDER_ENABLED=false to hide the feature entirely.
AGGRESSIVE_LIMIT_ORDER_ENABLED = str(
    os.environ.get("AGGRESSIVE_LIMIT_ORDER_ENABLED", "true")
).strip().lower() in ("1", "true", "yes")

# Tolerance (percent) for the "limit_tolerance" mode: how far past LTP the auto-derived limit price
# is placed so it fills like a market order but stays bounded. DEFAULT seeds a new user's form; MAX
# is a hard server-side clamp regardless of what the client sends.
try:
    AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT = float(
        os.environ.get("AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT", "5") or "5"
    )
except ValueError:
    AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT = 5.0
try:
    AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT = float(
        os.environ.get("AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT", "25") or "25"
    )
except ValueError:
    AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT = 25.0
# Exchange tick size the derived limit price is rounded to.
try:
    AGGRESSIVE_LIMIT_TICK_SIZE = float(
        os.environ.get("AGGRESSIVE_LIMIT_TICK_SIZE", "0.05") or "0.05"
    )
except ValueError:
    AGGRESSIVE_LIMIT_TICK_SIZE = 0.05


def redis_connection_url() -> str:
    if REDIS_URL:
        return REDIS_URL
    return f"redis://{REDIS_HOST}:{REDIS_PORT}/0"