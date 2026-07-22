---
name: run
description: Launch and drive Breeze Modern (Next.js + FastAPI trading dashboard) via a headless-Chromium Playwright REPL. Use when asked to run/start the app, take a screenshot of it, or confirm a UI change works by actually using it (e.g. Portfolio, Dashboard, Order Book).
---

Breeze Modern is a browser-driven web app (`./dev.sh`: uvicorn :8000 + chain-builder
worker + `next dev` :3000). There's no chromium-cli in this environment, so it's
driven via a small Playwright REPL at `frontend/scripts/browser-driver.mjs` (lives
there, not under this skill folder, so it resolves `frontend/node_modules/playwright`
— Node's ESM resolver looks up from the importing file's own path, not `cwd`).

All paths below are relative to the repo root.

## Prerequisites

```bash
# One-time, if missing:
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cd ..
cd frontend && npm install && npx playwright install chromium && cd ..
```
`playwright` is already a `frontend/package.json` devDependency; `npx playwright
install chromium` downloads the browser into `~/Library/Caches/ms-playwright` (or
platform equivalent) once and is shared across projects.

## Run the app

Use **mock broker mode** for agent/automated runs — it fakes ICICI entirely
(synthetic positions, quotes, and a synthetic broker token) and, critically, makes
`/auth/direct-login` set auth cookies immediately instead of redirecting to a real
ICICI OAuth login you can't script:

```bash
MOCK_MARKET_MODE=OFF_MARKET ./dev.sh > /tmp/dev.log 2>&1 &
echo $! > /tmp/dev.pid
timeout 120 bash -c 'until curl -sf http://127.0.0.1:8000/health >/dev/null; do sleep 1; done'
```
(`MOCK_MARKET_MODE=LIVE` also works — LIVE simulates WS tick flow, OFF_MARKET is
lighter. Either sets `ICICI_BROKER_MODE=mock` automatically.) `dev.sh` prints the
frontend URL once `next dev` is up; it can take a few extra seconds after `/health`
responds. **Before starting**, check `lsof -tiTCP:3000 -sTCP:LISTEN` /
`:8000` — `dev.sh` auto-kills a stale instance of *this* repo's own previous run,
but leaves other processes alone. Stop with `kill $(cat /tmp/dev.pid)` (dev.sh's own
trap stops the backend/worker/frontend it started).

## Drive it

```bash
SCREENSHOT_DIR=/tmp/shots node frontend/scripts/browser-driver.mjs <<'EOF'
launch
login-mock testuser1 TestPass123
nav /portfolio
wait-for text=OPEN POSITIONS
screenshot portfolio
console --errors
quit
EOF
```
Screenshots land in `/tmp/shots/` (override: `SCREENSHOT_DIR`). For iterative
debugging, run the same file under tmux and `send-keys` one line at a time instead
of a heredoc — same commands, same process, no relaunch cost.

### Auth

`login-mock <user_id> <password>` registers the account via `POST
/api/register/direct` (ignores 409 if it already exists — `api_key`/`secret_fragment`
are never validated against real ICICI, any non-empty value works) then `POST
/auth/direct-login`, which in mock mode sets auth cookies directly in one call — no
ICICI OAuth redirect to drive. Password must be 8+ characters.

### Commands

| command | what it does |
|---|---|
| `launch` | start headless Chromium (1800×1100 viewport — see Gotchas), open a page |
| `login-mock <user_id> <password>` | register (idempotent) + mock direct-login |
| `nav <path-or-url>` | navigate; bare paths resolve against `http://127.0.0.1:3000` |
| `wait-for <css-sel>` / `wait-for text=<text>` | wait up to 15s |
| `click <css-sel>` | click via Playwright locator |
| `click-text <text>` | click first element containing text — see Gotchas re: ambiguity |
| `fill <css-sel> <value>` | fill an input |
| `type <text>` / `press <key>` | keyboard input |
| `screenshot [name]` | full-page PNG → `$SCREENSHOT_DIR/<name>.png` |
| `eval <js>` | evaluate in page, print JSON |
| `text [css-sel]` | print `innerText` (body if no selector) |
| `cookies` | dump current cookie jar (debugging auth) |
| `mock-route <path> <json-body>` | intercept a GET endpoint for the rest of this session, e.g. `mock-route /deployment/license-status {"deployment_license_status":"active"}` to test past the read-only license gate without a real license |
| `viewport <w> <h>` | resize (e.g. drop below 1280 to test the mobile card layout) |
| `count <css-sel>` | print how many elements match (e.g. assert a button is truly absent, not just off-screen) |
| `attr <css-sel> <prop>` | print a DOM property of the first match (e.g. `attr input[type=checkbox] checked`, `attr button disabled`) |
| `console [--errors]` | print captured console/pageerror messages |
| `quit` | close browser, exit |

## Gotchas

- **1280px viewport clips the desktop table.** The Portfolio/Order Book tables
  assume real desktop width above Tailwind's `xl` breakpoint; at exactly 1280 the
  rightmost column (row action buttons) needs horizontal scroll that a screenshot
  won't capture. The driver defaults to 1800×1100 — don't shrink it below ~1440 if
  you need to see row actions.
- **`click-text` matches the first DOM occurrence, not the visible one.** E.g.
  `click-text "Square Off"` can match a same-text button hidden behind a modal
  backdrop and time out waiting for it to become clickable. Scope with a CSS
  selector instead: `click div[role="dialog"] button:has-text("Square Off")`.
- **License-gated actions show a "No deployment license" modal, not a silent
  failure.** A fresh local run has no portal license, so it's in read-only mode by
  design (see root `CLAUDE.md` "Portal integration") — submitting any order/hedge
  action opens that modal instead of the real confirm dialog. That's correct
  behavior to verify, not a bug to work around.
- **A `/api/login-disclosure/current` 404 appears on every page load.**
  Harmless and unrelated to app functionality in this local/mock setup — don't
  chase it as a regression unless you're specifically touching that feature.
- **The green "Live · WebSocket" dot next to an expanded position group** appears
  on a short delay (WS subscription handshake) — don't `wait-for` it as a readiness
  signal for anything else.
- **`readline`'s `'line'` event does not await async listeners.** If you extend
  `browser-driver.mjs`, keep new commands going through the existing `queue` chain
  (see the comment above it) — without it, a piped heredoc fires all commands
  concurrently and the process can exit mid-`launch`.

## Testing with a valid license (unblocking order placement)

By default, a fresh local run reports `unlicensed` (read-only banner, every
order/hedge/strategy-builder mutation 403s from `require_trading_not_revoked`) —
this is correct, fail-closed behavior per `docs/architecture.md`, not a bug.
`mock-route`-ing `/deployment/license-status` only fakes the **frontend** display
(unblocks `guardTradingAction` so a confirm dialog opens) — it does **not** satisfy
the backend's own independent check, so a real submission still 403s. To unblock
both for real, use the same test ES256 keypair the backend's own pytest suite uses
(`backend/tests/fixtures/portal_heartbeat_drm_keys.py`, `TEST_PRIVATE_KEY_PEM` /
`TEST_PUBLIC_KEY_PEM` — "not for production") against a locally-running
breeze-saas-portal (see its own `.claude/skills/run/SKILL.md`):

1. Start breeze-saas-portal with `PORTAL_HEARTBEAT_JWT_PRIVATE_KEY="$(cat
   /tmp/breeze-test-drm/portal_heartbeat_private.pem)"` set (extract
   `TEST_PRIVATE_KEY_PEM` from the fixtures file above into that path first).
2. Create a license via its admin console (`login-console admin` →
   `/console/admin/licenses` → fill Email/License Key → Create license) — pick any
   plaintext key, e.g. `TEST-LOCAL-DEV-LICENSE-KEY-0001`.
3. Register + activate it against this repo's reported IP (127.0.0.1) by calling
   the portal's own registration/activation endpoints directly — no CloudFormation
   or signed headers needed for these two:
   ```bash
   curl -s -X POST http://127.0.0.1:8100/api/public/deployments/register \
     -H "Content-Type: application/json" \
     -d '{"license_key":"TEST-LOCAL-DEV-LICENSE-KEY-0001","public_ip":"127.0.0.1"}'
   curl -s -X POST http://127.0.0.1:8100/api/public/activate-license \
     -H "Content-Type: application/json" \
     -d '{"license_key":"TEST-LOCAL-DEV-LICENSE-KEY-0001","public_ip":"127.0.0.1","icici_user_id":"QATESTER1"}'
   ```
   The second call's response should include `"deployment_license_status":"active"`.
4. Extract `TEST_PUBLIC_KEY_PEM` to a file and create an allowed-hosts file
   (containing `127.0.0.1`), then restart **this repo's** `./dev.sh` with:
   ```bash
   DEPLOYMENT_LICENSE_KEY="TEST-LOCAL-DEV-LICENSE-KEY-0001" \
   PUBLIC_FRONTEND_ORIGIN="http://127.0.0.1:3000" \
   PORTAL_API_BASE_URL="http://127.0.0.1:8100" \
   PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PATH="/tmp/breeze-test-drm/portal_heartbeat_public.pem" \
   PORTAL_ALLOWED_HOSTS_PATH="/tmp/breeze-test-drm/portal_allowed_hosts.txt" \
   ./dev.sh
   ```
5. `login-mock` (mock mode calls `notify_portal_deployment_login` on every
   direct-login) fires the real deployment-login handshake immediately — no need
   to wait for the 300s heartbeat interval. Confirm via `eval (async () => { const
   r = await fetch('/deployment/license-status'); return await r.json(); })()` →
   should show `{"deployment_license_status":"active","deployment_license_read_only":false}`.

`PUBLIC_FRONTEND_ORIGIN` must parse as a literal IPv4 host (`_public_ip_from_origin`
regex-checks it) — a hostname like `localhost` won't derive a public_ip and the
login-notify silently no-ops.

## Troubleshooting

- **Backend never becomes ready / `/health` times out:** check `/tmp/dev.log` —
  most often a stale `backend/.venv` (re-run `pip install -r requirements.txt`) or
  a port already held by an unrelated process (dev.sh will say so explicitly and
  exit rather than kill it).
- **`ERR_MODULE_NOT_FOUND: playwright`:** you're running the driver from somewhere
  other than a path that resolves `frontend/node_modules` — always invoke it as
  `node frontend/scripts/browser-driver.mjs`, not by `cd`-ing elsewhere first.
- **`login-mock` returns non-200/409 on register, or non-200 on login:** confirm
  the backend actually has `ICICI_BROKER_MODE=mock` (`MOCK_MARKET_MODE` set before
  `./dev.sh`) — without it, direct-login redirects to real ICICI OAuth instead of
  setting cookies, and this driver has no way to drive that.
