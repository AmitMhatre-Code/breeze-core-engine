#!/usr/bin/env bash
# Start backend (uvicorn) and frontend (Next dev) together from the repo root.
# Usage: ./dev.sh   (chmod +x dev.sh once)
# Uses backend/.venv; loads repo-root .env into the shell so child processes inherit vars.
# Ensure PUBLIC_FRONTEND_ORIGIN / GOOGLE_OAUTH_REDIRECT_BASE_URL use the same host as you
# open in the browser (e.g. http://127.0.0.1:3000 vs http://localhost:3000).
#
# Local test mode (no real ICICI broker, no static IP): set MOCK_MARKET_MODE=LIVE or
# MOCK_MARKET_MODE=OFF_MARKET in .env to switch the whole environment between simulated
# market-hours and after-market-hours behavior -- see docs/configuration-reference.md's
# MARKET_HOURS_OVERRIDE entry for what each mode actually drives.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
REDIS_PID=""

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

# breeze_connect downloads SecurityMaster over HTTPS at import time. Without this, imports can fail
# with SSL: CERTIFICATE_VERIFY_FAILED (proxy MITM, or Python SSL bundle on some macOS installs).
# main.py disables cert verification only when ICICI_BREEZE_INSECURE_SSL is 1/true/yes.
if [[ -z "${ICICI_BREEZE_INSECURE_SSL:-}" ]]; then
  export ICICI_BREEZE_INSECURE_SSL=1
fi

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
# Default hostname for printed URLs and browser API base (match your root .env).
APP_HOST="${APP_HOST:-127.0.0.1}"

# --- Kill this repo's own previous instance before starting a fresh one ---
# Only ever run one deployment of this app locally, so a leftover backend/
# frontend/worker from a prior ./dev.sh (left running after a closed
# terminal, a crashed shell, etc.) is stopped automatically rather than
# blocking startup with a port-in-use error. A PID is only ever touched here
# if its own command line (or, for Next's `next-server` process, its actual
# cwd -- Next's argv carries no path) traces back to *this* checkout; a port
# occupied by something else (including the sibling breeze-saas-portal repo,
# if it happens to share a default port) is left alone and reported instead
# of being silently killed. Set SKIP_DEV_STALE_KILL=1 to disable this.
#
# This must run *before* the MOCK_MARKET_MODE block below checks whether
# Redis is already reachable: killing a previous instance's backend here
# trips that old ./dev.sh's own wait-loop, which runs its cleanup trap and
# tears down everything it started -- including its own Redis, if it was the
# one that started it. Checking Redis reachability first and killing our
# predecessor second would leave that check stale by the time it matters.
if [[ -z "${SKIP_DEV_STALE_KILL:-}" ]]; then
  _pid_belongs_to_repo() {
    local pid="$1"
    if ps -p "$pid" -o command= 2>/dev/null | grep -qF "$ROOT"; then
      return 0
    fi
    local cwd
    cwd="$(lsof -p "$pid" -a -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    [[ -n "$cwd" && "$cwd" == "$ROOT"* ]]
  }

  _kill_stale_pid() {
    local pid="$1" label="$2"
    echo "Stopping previous ${label} (pid ${pid})..." >&2
    kill "$pid" 2>/dev/null || true
    for ((_i = 0; _i < 20; _i++)); do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
  }

  _kill_stale_port() {
    local port="$1" label="$2" pid
    for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null); do
      if _pid_belongs_to_repo "$pid"; then
        _kill_stale_pid "$pid" "$label (port ${port})"
      else
        echo "Port ${port} is already in use by an unrelated process (pid ${pid}) -- stop it manually or set BACKEND_PORT/FRONTEND_PORT to something else." >&2
        exit 1
      fi
    done
  }

  _kill_stale_pattern() {
    local pattern="$1" label="$2" pid
    for pid in $(pgrep -f "$pattern" 2>/dev/null); do
      _pid_belongs_to_repo "$pid" && _kill_stale_pid "$pid" "$label"
    done
  }

  _kill_stale_port "$BACKEND_PORT" "backend"
  _kill_stale_port "$FRONTEND_PORT" "frontend"
  _kill_stale_pattern "icici_breeze_backend.workers.chain_builder" "chain-builder worker"
  # Give a just-killed predecessor's own cleanup trap (which may tear down a
  # Redis it started) a moment to finish before the Redis check below runs.
  sleep 0.5
fi

# --- Local test mode: MOCK_MARKET_MODE in .env drives everything else below ---
if [[ -n "${MOCK_MARKET_MODE:-}" ]]; then
  MOCK_MODE="$(echo "$MOCK_MARKET_MODE" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
  case "$MOCK_MODE" in
    LIVE) DEFAULT_MARKET_OVERRIDE=open ;;
    OFF_MARKET | AFTER_MARKET)
      MOCK_MODE="OFF_MARKET"
      DEFAULT_MARKET_OVERRIDE=closed
      ;;
    *)
      echo "Unknown MOCK_MARKET_MODE '$MOCK_MARKET_MODE' (expected LIVE or OFF_MARKET)" >&2
      exit 1
      ;;
  esac

  # .env values win, same convention as ICICI_BREEZE_INSECURE_SSL above: only fill in
  # a default when the var isn't already set.
  if [[ -z "${MARKET_HOURS_OVERRIDE:-}" ]]; then
    export MARKET_HOURS_OVERRIDE="$DEFAULT_MARKET_OVERRIDE"
  fi
  if [[ -z "${ICICI_BROKER_MODE:-}" ]]; then
    export ICICI_BROKER_MODE=mock
  fi
  export NEXT_PUBLIC_ICICI_BROKER_MODE="${NEXT_PUBLIC_ICICI_BROKER_MODE:-$ICICI_BROKER_MODE}"
  export ICICI_MOCK_SYNTHETIC_BROKER_TOKEN="${ICICI_MOCK_SYNTHETIC_BROKER_TOKEN:-1}"

  echo ""
  echo "=================================================================="
  echo " Local test mode: ${MOCK_MODE}"
  if [[ "$MOCK_MODE" == "LIVE" ]]; then
    echo "   Chain quotes: REST APIs + simulated WebSocket ticks (random walk)"
  else
    echo "   Chain quotes: REST APIs + real bhavcopy/reference-data pipeline"
  fi
  echo "   ICICI_BROKER_MODE=${ICICI_BROKER_MODE}  MARKET_HOURS_OVERRIDE=${MARKET_HOURS_OVERRIDE}"
  if [[ "$ICICI_BROKER_MODE" != "mock" ]]; then
    echo "   WARNING: ICICI_BROKER_MODE is not 'mock' -- a real broker session will still"
    echo "            try to reach real ICICI; MARKET_HOURS_OVERRIDE alone won't mock it."
  fi
  echo "=================================================================="
  echo ""

  # LIVE mode's chain_builder worker is a separate OS process from the backend; they
  # only share subscription/tick state through Redis. Without a real Redis, each gets
  # its own isolated in-memory fallback and the live chain-building pipeline never
  # completes (silently falls back to bhavcopy every time, no error). Not needed for
  # OFF_MARKET mode (bhavcopy chain-building happens in-process), but harmless there.
  if [[ -z "${REDIS_URL:-}" ]]; then
    REDIS_SERVER_BIN="$(command -v redis-server || true)"
    if [[ -z "$REDIS_SERVER_BIN" && -x /opt/homebrew/opt/redis/bin/redis-server ]]; then
      REDIS_SERVER_BIN=/opt/homebrew/opt/redis/bin/redis-server
    fi
    if [[ -n "$REDIS_SERVER_BIN" ]]; then
      "$REDIS_SERVER_BIN" --port 6379 >/dev/null 2>&1 &
      CANDIDATE_PID=$!
      sleep 0.5
      if kill -0 "$CANDIDATE_PID" 2>/dev/null; then
        REDIS_PID="$CANDIDATE_PID"
        echo "Started local Redis (pid ${REDIS_PID})."
      else
        echo "Redis already running on port 6379 -- using the existing instance."
      fi
    else
      echo "Warning: redis-server not found (brew install redis)." >&2
      echo "         Continuing with in-memory cache fallback -- LIVE mode's chain_builder" >&2
      echo "         worker won't be able to build live chains correctly without real Redis." >&2
    fi
  fi
fi

VENV="$ROOT/backend/.venv"
if [[ ! -x "$VENV/bin/uvicorn" ]]; then
  echo "Missing backend venv. From repo root run:" >&2
  echo "  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Keep venv aligned with requirements.txt (e.g. new deps like bcrypt after git pull).
if [[ -z "${SKIP_DEV_PIP_SYNC:-}" ]]; then
  "$VENV/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "Installing frontend dependencies (first run)..." >&2
  (cd "$ROOT/frontend" && npm install)
fi

export PYTHONPATH="$ROOT/backend/src"
export NEXT_PUBLIC_BACKEND_URL="http://${APP_HOST}:${FRONTEND_PORT}"
export BACKEND_UPSTREAM_URL="http://${APP_HOST}:${BACKEND_PORT}"

BACK_PID=""
CHAIN_PID=""
FRONT_PID=""

cleanup() {
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
  [[ -n "${CHAIN_PID:-}" ]] && kill "$CHAIN_PID" 2>/dev/null || true
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true
  wait "$BACK_PID" 2>/dev/null || true
  wait "$CHAIN_PID" 2>/dev/null || true
  wait "$FRONT_PID" 2>/dev/null || true
  # Only stop Redis if this script started it (REDIS_PID unset otherwise) -- never
  # kill a Redis instance we merely found already running.
  if [[ -n "${REDIS_PID:-}" ]]; then
    kill "$REDIS_PID" 2>/dev/null || true
    wait "$REDIS_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "Starting backend on ${BACKEND_UPSTREAM_URL} ..."
(
  cd "$ROOT"
  exec "$VENV/bin/uvicorn" icici_breeze_backend.main:app \
   --host 0.0.0.0 --port "${BACKEND_PORT}"
) &
BACK_PID=$!

# Backend runs heavy startup (ICICI master zip, scrip DB) before binding; Next proxies to :8000
# immediately and floods ECONNREFUSED until uvicorn is ready — wait for /health first.
HEALTH_URL="http://${APP_HOST}:${BACKEND_PORT}/health"
BACKEND_READY_TIMEOUT="${BACKEND_READY_TIMEOUT:-120}"
echo "Waiting for backend (up to ${BACKEND_READY_TIMEOUT}s): ${HEALTH_URL} ..."
ready=0
for ((i = 1; i <= BACKEND_READY_TIMEOUT; i++)); do
  if ! kill -0 "$BACK_PID" 2>/dev/null; then
    echo "Backend exited before becoming ready." >&2
    wait "$BACK_PID" || true
    exit 1
  fi
  if curl -sf --connect-timeout 1 --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "Timed out waiting for backend at ${HEALTH_URL}" >&2
  exit 1
fi
echo "Backend is ready."

echo "Starting chain-builder worker ..."
(
  cd "$ROOT"
  exec "$VENV/bin/python" -m icici_breeze_backend.workers.chain_builder
) &
CHAIN_PID=$!

echo "Starting frontend on http://${APP_HOST}:${FRONTEND_PORT} ..."
(
  cd "$ROOT/frontend"
  exec npm run dev -- --hostname "${APP_HOST}" --port "${FRONTEND_PORT}"
) &
FRONT_PID=$!

echo ""
echo "  Backend:  http://${APP_HOST}:${BACKEND_PORT}"
echo "  Chain worker: running (pid ${CHAIN_PID})"
echo "  Frontend: http://${APP_HOST}:${FRONTEND_PORT}"
echo "  Open the app on the frontend URL (Ctrl+C stops both)."
echo ""

# macOS ships Bash 3.2, which has no `wait -n`; wait until either child exits.
set +e
code=0
while true; do
  if ! kill -0 "$BACK_PID" 2>/dev/null; then
    wait "$BACK_PID"
    code=$?
    break
  fi
  if ! kill -0 "$CHAIN_PID" 2>/dev/null; then
    wait "$CHAIN_PID"
    code=$?
    break
  fi
  if ! kill -0 "$FRONT_PID" 2>/dev/null; then
    wait "$FRONT_PID"
    code=$?
    break
  fi
  sleep 0.2
done
exit "$code"
