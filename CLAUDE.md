# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Breeze Modern** — a browser-based dashboard for **ICICI Direct Breeze**: portfolio, orders, option strategies, margin/scrip tools, and optional AI-assisted market outlook. Users authenticate with a direct app account and an ICICI broker session. Stack: **Next.js 16 (App Router) + FastAPI**, calling ICICI via `breeze_connect`, with SQLite + Redis and local files under `backend/data/`.

This repo is one half of a two-repo system. Production instances are **licensed and deployed by the sibling `breeze-saas-portal` repo** onto a customer's own AWS account via CloudFormation — this app phones home to the portal for license status and remote-approved upgrades, but the portal never touches trading traffic or ICICI credentials. See "Portal integration" below.

Deeper docs live in `docs/` — read these before making non-trivial changes, they are kept current:
- `docs/architecture.md` — runtime topologies, middleware chain, routing, persistence, portal integration, reference-data pipeline, active-chains/WS layer
- `docs/design-decisions.md` — **why** things are shaped this way (read before "fixing" something that looks odd) — 20 numbered decisions
- `docs/functionality.md` — feature/route map
- `docs/flows.md` — sequence diagrams for auth, broker return, heartbeat/upgrade, deploy
- `docs/configuration-reference.md` — full env var reference
- `docs/aws-deployment.md` — current (CloudFormation, portal-owned) vs legacy (dormant, manual GitHub Actions) deploy paths — read this before assuming anything about "how this gets deployed"

The `legacy/` directory is a **read-only historical snapshot** — never edit, create, or delete files there (see `.cursor/rules/legacy-read-only.mdc`). Compare behavior against it if useful, but ship fixes only in `backend/` and `frontend/`. Note: `legacy/` (the source snapshot) is unrelated to the "legacy" GHCR package `icici-breeze-modern` mentioned in `docs/aws-deployment.md` — same word, two different things.

## Commands

### Local dev (both services)

```bash
./dev.sh
```
Loads root `.env`, starts uvicorn (8000), the `chain_builder` worker, and `next dev` (3000), waits for `/health`, and stops everything on Ctrl+C. Open only `http://<APP_HOST>:3000` in the browser — not 8000 — so OAuth state and session cookies stay on one origin. Override with `BACKEND_PORT`, `FRONTEND_PORT`, `APP_HOST` env vars.

Requires `backend/.venv` to already exist:
```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### Docker Compose (single port 3000 via nginx)

```bash
docker compose up --build
```

### Backend tests (pytest, run from `backend/`)

```bash
cd backend && PYTHONPATH=./src .venv/bin/python -m pytest tests/                  # full suite
cd backend && PYTHONPATH=./src .venv/bin/python -m pytest tests/test_market_hours.py       # one file
cd backend && PYTHONPATH=./src .venv/bin/python -m pytest tests/test_market_hours.py -k test_weekend_not_trading_day  # one test
```
There is no `pytest.ini`/`pyproject.toml` — `PYTHONPATH=./src` is required so `icici_breeze_backend` resolves, and pytest must be invoked from `backend/` so `tests.fixtures.*` imports resolve. `tests/conftest.py` has an autouse fixture that bakes a test DRM public key/allowed-hosts file for portal heartbeat tests — no manual setup needed.

### Frontend (run from `frontend/`)

```bash
npm run dev            # next dev (port 3000)
npm run build           # next build (standalone output)
npm run lint             # eslint
npm test                  # vitest run (tests under src/**/*.test.ts)
```

## Architecture

### Three ways this runs, one path-based proxy model

| Topology | Where | Ports |
|---|---|---|
| `./dev.sh` | local dev | uvicorn :8000 (portal heartbeat loop + reference-data bootstrap run inside its lifespan), chain-builder worker (no port), `next dev` :3000 |
| `docker-compose.yml` | local, three containers | backend :8000, frontend :3000, nginx proxy on host :3000 |
| root `Dockerfile` | the image customers actually run, one image + supervisord | nginx :3000 (public), uvicorn :8000 (loopback), chain-builder worker, Next standalone `server.js` :3001 (loopback) |

In every topology the browser talks to **one origin** (nginx or Next's own rewrites), which splits requests by **path prefix** to backend vs frontend — there is no single `/api` mount point. `frontend/next.config.js` `rewrites()` enumerates the exact backend paths (`/auth/*`, `/api/register/*`, `/home/data`, `/portfolio/data`, `/book/*`, `/deployment/license-status`, `/strategy-builder/*`, etc.); `nginx.conf` / `deploy/nginx.all-in-one.conf` mirror the same split. This is deliberate (see design-decisions.md #2) — the backend kept legacy path shapes like `/home/data` and `/portfolio/data` instead of a `/api/v1/...` namespace, and `GET /portfolio` is intentionally **not** rewritten because the backend's redirect there breaks when proxied (App Router serves that page directly).

When adding a new backend route the frontend needs to call, you almost always need a matching `rewrites()` entry in `next.config.js` (and, if compose/production nginx also need it, the corresponding nginx conf).

**Production deployment is portal-owned, not CI-owned in this repo**: `ghcr-publish-main.yml` builds and publishes the image; there is no workflow here that runs it on EC2. breeze-saas-portal's CloudFormation stack does that (Amazon Linux 2023 arm64, not Ubuntu). This repo also still has *dormant* legacy GitHub Actions workflows (`legacy-aws-deploy-*.yml`) that deploy an unrelated older image (`icici-breeze-modern`) to Ubuntu EC2 instances — don't confuse the two when touching deploy-adjacent code. See `docs/aws-deployment.md`.

### Backend layering (`backend/src/icici_breeze_backend/`)

- Entry point: `main.py` → `start_application()` (app factory), loaded by uvicorn as `icici_breeze_backend.main:app`. It loads `.env` and configures logging **before** importing app modules, and applies a `requests` monkey-patch (`app/core/requests_patch.py`) at import time so GET-with-body works (`breeze_connect` needs this). The FastAPI `lifespan` does real work now: starts the portal heartbeat loop (if configured), resets the active-chains registry, and bootstraps the reference-data cache/scheduler.
- Middleware order matters: `CORSMiddleware` → `RateLimitMiddleware` → `CorrelationIdMiddleware` → `RequestLoggerMiddleware`.
- Routing: `app/api/router.py` includes `app/api/v1/router.py` with **no global prefix**; each feature router sets its own prefix (`/portfolio`, `/order`, `/book`, `/strategy-builder`, `/dashboard`, `/admin`, `/vertical-spread`, `/uncovered-shorts`, `/performance`, ...). Some routers use `prefix=""` because they own legacy-shaped paths directly (`home.py`, `route_settings.py`, `route_outlook.py`, `route_hedge.py`, `route_deployment.py`).
- `app/services/processor.py` is a **module-level singleton** (`breeze = processor()`) used by most routes — it centralizes `BreezeConnect` session handling, scrip master, and option chain logic. `processor().update_ICICImaster()` is still very much alive: called from legacy manual-refresh routes *and* from the new scheduled reference-data orchestrator. Tests patch `processor`/`icici_client` at module boundaries rather than injecting dependencies.
- `core/icici_client.py` wraps ICICI calls with retries, timeouts, metrics, and a circuit breaker.
- Two broker modes: `ICICI_BROKER_MODE=live` vs `mock` — mock mode exists for deterministic local/e2e testing and must never be treated as production-safe.
- `ICICI_BREEZE_INSECURE_SSL` disables TLS verification only for `breeze_connect`'s import-time SecurityMaster download (corporate MITM workaround) — opt-in, not for production.

### Portal integration (license, heartbeat, upgrade)

Every deployed instance holds a license issued by breeze-saas-portal and periodically POSTs a heartbeat to it (`app/services/portal_deployment_heartbeat.py`, 300–3600s interval). The portal's response is a signed `policy_token` JWT (`app/services/portal_policy_token.py` verifies it against a public key baked into the image at build time); the resulting status is cached (`app/services/deployment_license_status.py`) and treated as `unlicensed` if it goes stale for more than 2× the heartbeat interval — **fail-closed**, not fail-open. `app/api/deps_license.py`'s `require_trading_not_revoked` blocks order/book/hedge/strategy-builder mutation routes with a 403 whenever status is `revoked`/`unlicensed`/`pending_activation`/`trial_denied`; this is a real, expected user-facing state (read-only mode), not a bug, when a customer's license lapses. When the portal approves a version upgrade, this app pulls the new image and delegates the stop+recreate to a sibling `docker:cli` helper container (`app/services/deployment_container_upgrade.py`) — it can't stop itself from the inside. Full detail: `docs/architecture.md#portal-integration-license-heartbeat-and-upgrades`; the portal-side contract is authoritatively documented in `breeze-saas-portal/docs/license-management.md`, not here.

### Reference data pipeline, active chains, and the WS tick pipeline

Bhavcopy/scrip-master/SPAN loading is no longer purely on-demand: `app/services/reference_data/scheduler.py` bootstraps a cache warm-up at startup and runs a daily IST-scheduled refresh (`orchestrator.py` coordinates NSE/BSE bhavcopy + scrip master + SPAN baseline). Redis reference-data keys are versioned (`refdata:v{N}:...` with a `refdata:current_version` pointer flipped only once a version is fully built) so readers never see a half-loaded refresh — see `reference_data/keys.py`.

The `chain_builder` worker (separate OS process in every topology, not a thread of the API process) only refreshes chains that actually have a live WS subscriber, tracked via `reference_data/active_chains.py` — this is a deliberate CPU/memory bound for modest EC2 instance sizes, not an oversight if you notice a chain isn't "hot" until someone requests it. `chain_readiness.py`'s `wait_for_canonical_chain()` blocks a request until every tradeable strike in a chain has a real quote, surfaced in the frontend as a loading state (`ChainBuildStatus`/`SectionGate`). Tradeable strikes are filtered using `MarginPercentage > 0` from the ICICI scrip master.

Redis is optional: `app/db/redis_client.py` falls back to an in-process `_MemoryStore` when Redis is unreachable, unless `REDIS_REQUIRE_CONNECTED=true`. On the customer CloudFormation deployment, Redis runs as a sibling `breeze-redis` Docker container (not a managed cloud service), capped via `REDIS_MAXMEMORY_MB`.

### Persistence

- `backend/data/users.sqlite3` — accounts, encrypted broker credential metadata, parked orders, AI provider keys, outlook preferences, the global (deployment-wide, not per-user) exchange calendar, and the reference-data schedule/progress/history tables (migration-backed schema).
- `backend/data/scrips.sqlite3` — scrip master cache.
- `backend/db-templates/` — seed copies of empty DBs/limit files, used because Docker bind mounts can hide image-baked files on first boot.
- SQLite is intentionally single-instance — the app is designed around one backend process per deployment, not horizontal scale of writers. On the CFN deployment, `backend/data`'s equivalent is a small (2 GiB) EBS volume with no CloudFormation retention policy — it's deleted along with the stack on destroy.

### Auth/secrets

- One secret (`JWT_SECRET`, aliases `ENCRYPTION_KEY`/`JWT_SECRET_KEY`) is used for **both** JWT signing and encrypting stored broker credential material — losing it means encrypted data is unrecoverable, so treat it like a root key.
- `PUBLIC_FRONTEND_ORIGIN` and `GOOGLE_OAUTH_REDIRECT_BASE_URL` must match the exact origin the browser uses (host, not just domain — `localhost` and `127.0.0.1` are different origins for cookies/OAuth state). `PUBLIC_FRONTEND_ORIGIN` also determines the `public_ip` this instance reports to the portal — get it wrong and heartbeats/activation will fail their IP-binding check.
- Direct login (`/auth/direct-login` → `/auth/icici-redirect`) is current; legacy `/auth/login` (ICICI-token login) is deprecated and returns HTTP 410.

### Frontend (`frontend/src/`)

Next.js App Router, React 19, TypeScript, Tailwind 4, TanStack React Query for server state, Chart.js for charts. `output: "standalone"` for the Docker build. Production Docker build deliberately avoids hardcoding `NEXT_PUBLIC_BACKEND_URL` so the browser uses `window.location.origin` for same-origin calls through nginx. One frontend server route proxy still exists for outlook (`src/app/api/outlook/[...path]/route.ts`) to keep same-origin behavior and control streaming/headers without exposing backend topology. `src/components/license/` (banner + read-only guards) and `src/lib/use-deployment-license.ts` implement the license-status UI described above.

## Conventions worth knowing before editing

- "Outlook" in routes/settings means **market outlook** (RSS + optional AI narrative), not Microsoft Outlook/Graph — historical naming, not a bug.
- Correlation IDs are attached to error JSON (via `CorrelationIdMiddleware`) for support debugging — don't strip them from error responses.
- `/admin/tests/status` and `/admin/*` are restricted admin/test surfaces, distinct from the pytest suite.
- `route_audit` module exists for operator audit trails but is not currently mounted in the v1 router — don't assume it's live without checking.
- Read-only trading mode (see "Portal integration" above) is a real license-enforcement state, not a bug — if a mutation route 403s with a "Read-only mode" message, check `deployment_license_status` before assuming it's broken.
