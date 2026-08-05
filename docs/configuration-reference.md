# Configuration reference

All sensitive values belong in a **repo-root `.env` file** (or an equivalent env file you pass to Docker). Do not commit real secrets. The backend loads `.env` via `python-dotenv` from `backend/src/icici_breeze_backend/main.py` (searching package-adjacent and cwd paths).

---

## Required for a working login experience

| Variable | Purpose |
|----------|---------|
| `JWT_SECRET` | Signing JWTs **and** symmetric encryption for stored credentials and OAuth cookie payloads. Alternatives accepted: `ENCRYPTION_KEY`, `JWT_SECRET_KEY` (first non-empty wins in code). Use a long random string. |
| `GOOGLE_CLIENT_ID` | Optional unless Google OAuth routes are actively enabled in your deployment. |
| `GOOGLE_CLIENT_SECRET` | Optional unless Google OAuth routes are actively enabled in your deployment. |

**ICICI**: API key and related broker credentials are normally **entered through the UI** (`/settings/credentials`) and stored encrypted in SQLite—not always a single static env var. You still need whatever ICICI’s registration flow expects for your account (documented by ICICI). Ensure redirect URL matches below.

---

## Strongly recommended when using Next on port 3000

| Variable | Example | Purpose |
|----------|---------|---------|
| `PUBLIC_FRONTEND_ORIGIN` | `http://localhost:3000` | Where to send the user after ICICI steps (`/dashboard`, etc.). Must match browser URL (scheme + host + port). |
| `GOOGLE_OAUTH_REDIRECT_BASE_URL` | `http://localhost:3000` | Base URL for Google’s `redirect_uri` so it matches the session cookie host. |
| `CORS_ORIGINS` or `ALLOWED_ORIGINS` | `http://localhost:3000` | CORS allowlist for credentialed browser calls to the API. `CORS_ORIGINS` overrides `ALLOWED_ORIGINS` if both set. |

---

## Google Cloud Console (optional/legacy-compatible)

**Authorized redirect URIs** (typical dev):

- `http://localhost:3000/auth/google/callback`
- Optionally `http://127.0.0.1:3000/auth/google/callback` if you use that host consistently.

**Edge case (API on 8000 only)**: `http://localhost:8000/auth/google/callback` and leave `GOOGLE_OAUTH_REDIRECT_BASE_URL` unset.

---

## ICICI Breeze / API app

Register the **redirect / callback URL** with ICICI as:

- `{PUBLIC_FRONTEND_ORIGIN}/icici-return`  
  e.g. `http://localhost:3000/icici-return`

Use the **same** host you type in the browser (`localhost` vs `127.0.0.1` are different origins).

---

## Session and cookies

| Variable | Default | Purpose |
|----------|---------|---------|
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access token lifetime. |
| `COOKIE_SECURE` | `false` | If `true` / `1` / `yes`, cookies use `Secure` flag—**requires HTTPS**. Use on production behind TLS. Plain HTTP (default AWS workflow on port 80) keeps this `false` unless you terminate TLS. |

---

## ICICI client behaviour

| Variable | Default | Purpose |
|----------|---------|---------|
| `ICICI_BREEZE_INSECURE_SSL` | unset (`dev.sh` sets `1`) | When truthy, disables TLS verification for `breeze_connect`’s import-time download path. **Security risk** if enabled in untrusted networks. |
| `BREEZE_SESSION_CACHE_TTL_SECONDS` | `0` | `0` means cache until midnight IST; otherwise TTL in seconds for session reuse logic. |
| `ICICI_BROKER_MODE` | `live` | `live` uses actual ICICI APIs; `mock` stubs ICICI calls for local/testing workflows. |
| `ICICI_MOCK_SYNTHETIC_BROKER_TOKEN` | `mock-token` | Synthetic broker token used in mock mode when issuing app auth cookies. |
| `ICICI_MOCK_BROKER_COOKIE_VALUE` | `mock` | Broker cookie value injected by mock mode auth paths. |
| `AGGRESSIVE_LIMIT_ORDER_ENABLED` | `true` | Master gate for the aggressive-order (⚡) feature, enabled by default now that **Limit + tolerance** (the default mode) is a fully working, ICICI-independent path. When `true`, order forms let users pick per order between **Market** (native ICICI `order_type=market, price=0`; may be rejected until ICICI enables native market orders) and **Limit + tolerance** (an ordinary limit priced off LTP by a tolerance %, resolved server-side via `POST /order/aggressive-price` — works today, no ICICI dependency). Set to `false` to hide the UI toggle and make `place_order`/`break_order`/`break_order_place_chunk` reject `aggressive_limit=true` requests. |
| `AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT` | `5` | Tolerance % that seeds a new user's "Limit + tolerance" mode. Buy fills at `LTP × (1 + tol)`, Sell at `LTP × (1 − tol)`, tick-rounded. Per-user overrides persist on `user_account` and are editable per order. |
| `AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT` | `25` | Hard server-side clamp on the tolerance % — a client can never push the derived limit further than this from LTP, regardless of what it sends. |
| `AGGRESSIVE_LIMIT_TICK_SIZE` | `0.05` | Exchange tick the derived aggressive limit price is rounded to. |
| `MARKET_HOURS_OVERRIDE` | unset | Dev-only. `open`/`live`/`1` forces `market_hours.is_india_market_open()` to `True`; `closed`/`off_market`/`0` forces `False`; unset uses the real IST wall clock. Only affects real-wall-clock callers (`now=None`) — code that passes an explicit `now` is never affected. This is what actually drives `quote_source_router.resolve_quote_source()` to pick `"websocket"` vs `"bhavcopy"` for chain quotes. Normally set indirectly via `MOCK_MARKET_MODE` below rather than directly. |
| `MOCK_MARKET_MODE` | unset | Dev-only, read by `dev.sh` itself (not application code). `LIVE` or `OFF_MARKET` switches the whole local test environment between simulated market-hours and after-market-hours behavior in one step: derives `MARKET_HOURS_OVERRIDE` (`open`/`closed`) and `ICICI_BROKER_MODE=mock` (unless either is already set in `.env`, which wins), and auto-starts a local Redis if one isn't already reachable — required for `LIVE` mode, since the backend and `chain_builder` worker are separate OS processes that only share subscription/tick state through Redis. |

`ICICI_MAX_RETRIES` and `ICICI_TIMEOUT_SECONDS` are currently defined in code (`core/config.py`) with fixed defaults unless extended.

`/dev/mock-broker-cookie` is exposed for dev/test setup and should not be publicly enabled in production deployments.

`ICICI_BROKER_MODE=mock`'s `MockBreezeSdk` (`backend/src/icici_breeze_backend/dev/mock_broker.py`) also implements `ws_connect`/`subscribe_feeds`/`unsubscribe_feeds`/`on_ticks`: a background thread streams random-walk ticks (shared math in `dev/mock_market_data.py`) for whatever tokens get subscribed, so the real chain-quote pipeline (WS tick pipeline, `chain_builder` worker, canonical chain assembly) runs unmodified against fake price data when `MARKET_HOURS_OVERRIDE=open` forces the websocket route. Tick *values* are fake; contract *identity* (stock/expiry/strike/right) is resolved from the real local scrip-master DB (`backend/data/scrips.sqlite3`) -- which the reference-data orchestrator (`app/services/reference_data/orchestrator.py`) now refreshes from ICICI's real, unauthenticated public `SecurityMaster.zip` at every startup **regardless of `ICICI_BROKER_MODE`** (this download needs no broker session, API key, or static IP; only `update_ICICImaster()`'s *authenticated* siblings -- trading, portfolio, WS ticks -- are actually gated by broker mode). So even a fresh checkout gets real, tradeable-contract-filtered scrip data automatically in mock mode; no prior live-ICICI run or manually-seeded DB is required.

---

## Optional dashboard / news

| Variable | Purpose |
|----------|---------|
| `AITRADOS_SECRET_KEY` | Economic calendar integration when configured. |
| `NEWS_API_KEY` | News features when configured. |

---

## Telegram alerts (stop-loss / profit-booking notifications)

| Variable | Default | Purpose |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | unset | BotFather token. Leave unset to disable the feature entirely — no background loop starts, Settings > Telegram Alerts shows "not configured". Used only to *send* alerts; this app never reads Telegram updates. |
| `TELEGRAM_BOT_USERNAME` | unset | Bot's `@username` (without the `@`), used to build the `t.me/<username>?start=<token>` deep link. |

`TELEGRAM_POLL_TIMEOUT_SEC` was removed: this app no longer polls Telegram. Account **linking** is routed by the portal, which owns the single permitted `getUpdates`/webhook consumer for the shared bot token — so the claim loop also needs `PORTAL_API_BASE_URL` and a correct `PUBLIC_FRONTEND_ORIGIN` (it identifies itself to the portal by public IP). With the bot configured but no portal reachable, alerts still send to already-linked users; only new linking is unavailable.

For **licensed deployments**, `TELEGRAM_BOT_TOKEN`/`TELEGRAM_BOT_USERNAME` are centrally managed via the portal Console (Admin → Core Engine fleet settings) and pushed to every deployment automatically over the existing heartbeat channel — the portal has no other way to reach an instance running in a customer's own AWS account. Manually setting these in `.env` is a fallback for unlicensed/local dev instances, not the primary path; see `docs/architecture.md#telegram-alerts-stop-loss--profit-booking-notifications` for how the push mechanism works.

---

## Rate limiting and testing

| Variable | Default | Purpose |
|----------|---------|---------|
| `RATE_LIMIT_PER_MIN` | `100` | Requests per minute per client key (see middleware). |
| `E2E_RATE_LIMIT_BYPASS_SECRET` | unset | If set, matching requests can bypass rate limit (admin / E2E). |
| `INTEGRATION_SKIP_MARKET_HOURS` | unset | When `1`, admin integration tests may skip market-hours checks. |
| `E2E_BASE_URL` | `http://localhost:8000` | Base URL for subprocess tests spawned from admin routes. |
| `E2E_HOST`, `E2E_PORT`, `E2E_ADMIN_EMAIL`, `E2E_ADMIN_PASSWORD` | unset | Optional overrides for admin-triggered test harness execution. |

---

## Logging

Read **only from the `.env` file** (not from the process environment) in `main.py`:

| Variable | Purpose |
|----------|---------|
| `LOG_LEVEL` | e.g. `INFO`, `DEBUG`. |
| `LOG_FILE` | Optional path for file logging under `backend/logs/` or absolute path. |

---

## Runtime endpoints worth monitoring

| Endpoint | Purpose |
|----------|---------|
| `/health` | Liveness check; includes Redis connectivity/fallback status. |
| `/metrics` | ICICI client retry/circuit-breaker metrics. |
| `/metrics/runtime` | WS tick pipeline, active-chains registry, and Redis stats. |
| `/deployment/license-status` | Cached portal license status for the UI (see Portal integration below). |

---

## Docker Compose

`docker-compose.yml` uses:

```yaml
env_file:
  - ./.env
```

for the **backend** service. Mounts:

- `./backend/data:/app/backend/data`
- `./backend/logs:/app/backend/logs`

---

## Frontend / Next.js

| Variable | Where | Purpose |
|----------|-------|---------|
| `BACKEND_UPSTREAM_URL` | `next.config.js`, server routes | Upstream FastAPI for rewrites (compose: `http://backend:8000`; dev: `http://127.0.0.1:8000`). |
| `NEXT_PUBLIC_BACKEND_UPSTREAM_URL` | Same | Public alias for build-time if needed. |
| `NEXT_PUBLIC_BACKEND_URL` | `frontend/src/lib/config.ts`, `dev.sh` | Browser-visible API base; dev sets to `http://APP_HOST:FRONTEND_PORT` so calls go through Next’s origin. Production image often omits this so the client uses `window.location.origin`. |

Compose sets for **frontend** container:

- `NEXT_PUBLIC_BACKEND_URL: http://proxy:3000`
- `BACKEND_UPSTREAM_URL: http://backend:8000`

---

## AWS runtime

Customer deployments (the current, active path) get their `.env` written by breeze-saas-portal's CloudFormation stack, seeded with `DEPLOYMENT_LICENSE_KEY`, `PORTAL_API_BASE_URL`, and a freshly generated `JWT_SECRET`. The dormant legacy GitHub Actions workflows instead decode a **base64-encoded full `.env`** (secret `APP_ENV_FILE_B64`) into the same path. See [AWS deployment](./aws-deployment.md) for which path applies to you.

---

## Reference data and Redis

| Variable | Default | Purpose |
|----------|---------|---------|
| `REDIS_URL` | *(empty)* | Redis connection URL for scrip index, bhavcopy cache, and WebSocket quote cache. Falls back to in-process memory when unset or unreachable. **CloudFormation bootstraps and fleet upgrades** auto-set `redis://breeze-redis:6379/0` and start a co-located `breeze-redis` sidecar (`redis:7-alpine`) on Docker network `breeze-core-net`. The sidecar runs with **`--maxmemory 384mb --maxmemory-policy allkeys-lru`** (existing fleets are recreated on upgrade/bootstrap when maxmemory was unset). Redis is ephemeral (no volume); reference data reloads on schedule, from SQLite at boot, or manual load. |
| `REDIS_REQUIRE_CONNECTED` | `true` when `REDIS_URL` is set, else `false` | When true, the API **refuses to start** if Redis is unreachable (no in-memory fallback). Set `false` only for local dev without Redis. |
| `REDIS_MAXMEMORY_MB` | `384` | Documented cap for the Redis sidecar (used by upgrade/bootstrap scripts and `/health` reporting). Override on the host when starting `breeze-redis`. |
| `REDIS_HOST` | `127.0.0.1` | Used when `REDIS_URL` is empty. |
| `REDIS_PORT` | `6379` | Used when `REDIS_URL` is empty. |
| `REFERENCE_DATA_REFRESH_HOUR_IST` | `18` | Daily scheduled reference data load hour (IST). |
| `REFERENCE_DATA_REFRESH_MINUTE_IST` | `0` | Daily scheduled reference data load minute (IST). |
| `REFERENCE_DATA_LOOKBACK_DAYS` | `10` | Trading-day lookback when downloading NSE/BSE FO bhavcopy. |
| `NSE_FO_BHAVCOPY_URL_TEMPLATE` | NSE archives FO zip URL | `{yyyymmdd}` placeholder. |
| `BSE_FO_BHAVCOPY_URL_TEMPLATE` | BSE derivative CSV URL | `{yyyymmdd}` placeholder. |
| `WEBSOCKET_QUOTE_TTL_SECONDS` | `120` | Redis TTL for normalized WebSocket quote cells. |
| `WS_RAW_QUOTE_TTL_SECONDS` | `120` | Redis TTL for raw WebSocket tick payloads. |
| `WS_QUOTE_SNAPSHOT_ENABLED` | `true` | Capture the last live tick per contract to a durable snapshot, used as the first post-close quote source. Disable and BFO chains fall back to bhavcopy/REST, which carry no market depth after close. |
| `WS_QUOTE_SNAPSHOT_FLUSH_SECONDS` | `300` | How often the in-Redis snapshot is flushed to `scrips.sqlite3`. Lower = less captured depth lost to an unclean shutdown. |
| `WS_QUOTE_SNAPSHOT_RETENTION_DAYS` | `5` | Sessions of snapshot history kept in SQLite before pruning. Only the latest concluded session is ever served. |
| `WS_TICK_INGEST_QUEUE_SIZE` | `2000` | Max in-process WS tick ingest queue depth before coalescing drops oldest. |
| `WS_TICK_COALESCE_MS` | `100` | Coalesce window (ms) before writing latest tick per token to Redis. |
| `CHAIN_BUILDER_POLL_MS` | `250` | chain-builder worker poll interval when rebuilding active chains. |
| `CANONICAL_CHAIN_TTL_SECONDS` | `5` | Redis TTL for assembled canonical option chains. |
| `CHAIN_WS_WAIT_TIMEOUT_MS` | `8000` | How long a chain request blocks waiting for the live chain to become ready before falling back to offline sources. |
| `CHAIN_WS_WAIT_POLL_MS` | `100` | Poll interval within that wait. |
| `CHAIN_READY_ATM_STRIKE_WINDOW` | `5` | Strikes each side of ATM that must carry a real quote for a chain to count as ready. Raise it and far-dated or thin chains (BSESEN monthlies, single-stock options) become permanently un-ready — their deep wings may not trade at all in a session, so no wait length helps. |

**Monitoring:** `GET /health` reports Redis connectivity (`status`: `ok` or `degraded`). `GET /metrics/runtime` reports Redis memory, WS tick pipeline queues, and active chain registry stats.

**EC2 (t4g.small):** CloudFormation bootstrap and legacy deploy user-data configure a **2 GiB swap file** on the root volume as an OOM safety margin. Persistent app data remains on the attached data EBS volume.

---

## Checklist before going live

1. `JWT_SECRET` is unique and backed up offline.
2. `PUBLIC_FRONTEND_ORIGIN` and `GOOGLE_OAUTH_REDIRECT_BASE_URL` match the **public URL** users open.
3. Google redirect URIs include `{that origin}/auth/google/callback`.
4. ICICI redirect is `{that origin}/icici-return`.
5. `COOKIE_SECURE` matches your TLS posture.
6. Persistent volume mounted at `/app/backend/data` if you need survival across container replace.

---

## Exchange calendar (deployment-wide settings + Console sync)

Holidays and regular session hours (defaults 9:15–15:30 IST) are a single, deployment-wide value — not per-user — stored in `users.sqlite3` (`exchange_calendar` singleton table, one row). Any authenticated user can edit it under **Settings → Exchange calendar** or sync it from Breeze Console when linked; the edit takes effect for the whole deployment immediately, including background jobs (bhavcopy scheduler, chain health) that have no per-request user context. This is what a deployment operator uses to hand-enter special/holiday sessions (e.g. Muhurat trading) that no exchange API publishes — see [design-decisions.md #21](./design-decisions.md).

| Variable | Purpose |
|----------|---------|
| `PORTAL_API_BASE_URL` | Base URL of breeze-saas-portal (e.g. `https://breeze-ui.com`). Required for **Sync from Breeze Console** on the exchange calendar settings page, and for the portal integration below. |
| `DEPLOYMENT_LICENSE_KEY` | Used for other portal features (terms, heartbeat); **not** required for exchange calendar sync (public read endpoint). |

Bundled `backend/data/exchange_holidays.json` seeds the calendar's initial defaults on first startup (and is used as the migration's fallback when no legacy customization is found).

---

## Portal integration (license, heartbeat, upgrade)

These drive the license/heartbeat/self-upgrade client described in [Architecture — Portal integration](./architecture.md#portal-integration-license-heartbeat-and-upgrades). When `PORTAL_API_BASE_URL` is unset, this whole subsystem is inactive and trading is unrestricted (local dev default).

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORTAL_API_BASE_URL` | *(empty)* | Base URL of breeze-saas-portal. Must resolve to a hostname on the image's baked allowlist (see below) or heartbeats are silently skipped. |
| `DEPLOYMENT_LICENSE_KEY` | *(empty)* | License key sent on heartbeat/activation. Empty means the instance reports as unlicensed once the portal is configured. |
| `PORTAL_HEARTBEAT_INTERVAL_SEC` | `300` | Starting heartbeat interval; the portal's response can adjust it, clamped to **300–3600s** either way. |
| `PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PATH` | `/etc/breeze/portal_heartbeat_public.pem` | Where to read the portal's public key for verifying `policy_token`. Baked into the production image; override only for local testing. |
| `PORTAL_ALLOWED_HOSTS_PATH` | `/etc/breeze/portal_allowed_hosts.txt` | Allowlist file of hostnames `PORTAL_API_BASE_URL` may point to (SSRF guard). Baked into the production image. |
| `DEPLOYMENT_GHCR_IMAGE` | *(empty)* | Image reference (e.g. `ghcr.io/<org>/breeze-core-engine`) the in-place self-upgrade pulls from; the portal-supplied `target_tag` is appended. Upgrade is skipped if unset. |
| `DEPLOYMENT_CONTAINER_NAME` | `breeze-core-engine` | Name of the running app container the upgrade helper stops and recreates. |
| `DEPLOYMENT_ENV_FILE` | `/opt/breeze-core-engine/.env` | Host path to the `.env` file the recreated container is started with. |
| `LICENSE_STATUS_OVERRIDE` | unset | Dev-only. Forces `deployment_license_status.get_license_status()`/`trading_mutations_allowed()`/`get_license_status_for_api()` to a fixed value, bypassing `PORTAL_API_BASE_URL`/`DEPLOYMENT_LICENSE_KEY`/heartbeat state entirely — lets you test read-only-mode enforcement without a real portal. One of `active`/`expired`/`revoked`/`unlicensed`/`pending_activation`/`trial_denied`; unrecognized or unset values fall through to normal portal-driven behavior. Never set in production. |

### Market outlook (portal-fetched, no per-instance config)

The dashboard's AI market outlook is generated centrally on breeze-saas-portal (one admin-configured API key + prompt produces a single global result on a schedule) and fetched by every deployment's `GET /api/outlook/market`. There is no per-user API key or prompt configuration on this instance anymore — the old `/settings/ai-provider` and `/api/settings/outlook-config` surface has been removed.

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORTAL_API_BASE_URL` | *(empty)* | Reused from the portal integration above. When unset, the outlook fetch loop is disabled and the dashboard card stays empty until it's configured. |
| `MARKET_OUTLOOK_REFRESH_INTERVAL_SEC` | `300` | How often this deployment re-polls the portal's (cheap, Redis-backed) cached outlook. Independent of the portal admin's own generation interval. |

If the portal is briefly unreachable, this deployment keeps serving its last successfully fetched outlook (with a staleness warning) rather than showing a blank card — see `app/services/portal_market_outlook.py`.
| `DEPLOYMENT_DATA_HOST_PATH` | `/opt/breeze-core-engine/data` | Host path bind-mounted into the recreated container. |
| `DEPLOYMENT_PUBLISH_PORT` | `80` | Host port the recreated container publishes. |
| `APP_VERSION` / `IMAGE_TAG` / `DEPLOYMENT_VERSION` | *(empty)* | Checked in this order as the version string reported on heartbeat; falls back to a baked `/etc/breeze_app_version` file, then `"unknown"`. |
| `APP_VERSION_FILE` | `/etc/breeze_app_version` | Override path for the baked version file. |

**Runtime endpoint:** `GET /deployment/license-status` returns the cached status for the UI (also embedded in `/home/data`).
