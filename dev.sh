#!/usr/bin/env bash
# Start backend (uvicorn) and frontend (Next dev) together from the repo root.
# Usage: ./dev.sh   (chmod +x dev.sh once)
# Uses backend/.venv; loads repo-root .env into the shell so child processes inherit vars.
# Ensure PUBLIC_FRONTEND_ORIGIN / GOOGLE_OAUTH_REDIRECT_BASE_URL use the same host as you
# open in the browser (e.g. http://127.0.0.1:3000 vs http://localhost:3000).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

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

VENV="$ROOT/backend/.venv"
if [[ ! -x "$VENV/bin/uvicorn" ]]; then
  echo "Missing backend venv. From repo root run:" >&2
  echo "  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "Installing frontend dependencies (first run)..." >&2
  (cd "$ROOT/frontend" && npm install)
fi

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
# Default hostname for printed URLs and browser API base (match your root .env).
APP_HOST="${APP_HOST:-127.0.0.1}"

export PYTHONPATH="$ROOT/backend/src"
export NEXT_PUBLIC_BACKEND_URL="http://${APP_HOST}:${FRONTEND_PORT}"
export BACKEND_UPSTREAM_URL="http://${APP_HOST}:${BACKEND_PORT}"

BACK_PID=""
FRONT_PID=""

cleanup() {
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
  [[ -n "${FRONT_PID:-}" ]] && kill "$FRONT_PID" 2>/dev/null || true
  wait "$BACK_PID" 2>/dev/null || true
  wait "$FRONT_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Starting backend on ${BACKEND_UPSTREAM_URL} ..."
(
  cd "$ROOT"
  exec "$VENV/bin/uvicorn" icici_breeze_backend.main:app \
   --host 0.0.0.0 --port "${BACKEND_PORT}"
) &
BACK_PID=$!

echo "Starting frontend on http://${APP_HOST}:${FRONTEND_PORT} ..."
(
  cd "$ROOT/frontend"
  exec npm run dev -- --hostname "${APP_HOST}" --port "${FRONTEND_PORT}"
) &
FRONT_PID=$!

echo ""
echo "  Backend:  http://${APP_HOST}:${BACKEND_PORT}"
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
  if ! kill -0 "$FRONT_PID" 2>/dev/null; then
    wait "$FRONT_PID"
    code=$?
    break
  fi
  sleep 0.2
done
exit "$code"
