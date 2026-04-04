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

# Product Types
OPTIONS = "Options"
LIMIT = "limit"
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
HEDGE = "Hedge"
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

EXPIRED = "Expired"
CANCELLED = "Cancelled"
EXECUTED = "Executed"
PARTIAL_EXECUTED = "Partially Executed"
PARTIAL_EXECUTED_EXPIRED = "Partially Executed And Expired"
PARTIAL_EXECUTED_CANCELED = "Partially Executed And Cancelled"

# Dashboard: economic calendar (AiTrados), news (optional)
AITRADOS_SECRET_KEY = (os.environ.get("AITRADOS_SECRET_KEY") or "").strip()
NEWS_API_KEY = (os.environ.get("NEWS_API_KEY") or "").strip()