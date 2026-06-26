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

`ICICI_MAX_RETRIES` and `ICICI_TIMEOUT_SECONDS` are currently defined in code (`core/config.py`) with fixed defaults unless extended.

`/dev/mock-broker-cookie` is exposed for dev/test setup and should not be publicly enabled in production deployments.

---

## Optional dashboard / news

| Variable | Purpose |
|----------|---------|
| `AITRADOS_SECRET_KEY` | Economic calendar integration when configured. |
| `NEWS_API_KEY` | News features when configured. |

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

`LOG_LEVEL` and `LOG_FILE` are resolved in `main.py` from, in order:

1. On-disk `.env` paths (`backend/.env`, cwd `.env`, and `/opt/breeze-core-engine/.env` when mounted)
2. Process environment (`docker run --env-file` / `--env-file` injection)

| Variable | Purpose |
|----------|---------|
| `LOG_LEVEL` | e.g. `INFO` (default), `DEBUG`. |
| `LOG_FILE` | Optional path for file logging under `backend/logs/` or absolute path. |

### ICICI Breeze forensic traces (`LOG_LEVEL=DEBUG`)

Set `LOG_LEVEL=DEBUG` in the host `.env`, recreate the container, then grep backend logs for `breeze_http_audit`. Each outbound Breeze REST call emits one DEBUG line with `breeze_call_id`, `correlation_id`, `origin` (`upstream` \| `synthetic` \| `daily_blocked`), HTTP vs Breeze body status, whitelisted response headers, and `icici_minute_limit` when ICICI returns per-minute limit text (including HTTP 401 + `Status:5`). At `INFO` or above, these lines are not emitted.

---

## Runtime endpoints worth monitoring

| Endpoint | Purpose |
|----------|---------|
| `/health` | Liveness check. |
| `/metrics` | ICICI client retry/circuit-breaker metrics. |

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

## AWS runtime (`APP_ENV_FILE_B64`)

The GitHub Actions deploy decodes a **base64-encoded full `.env`** into `/opt/breeze-core-engine/.env` on the instance. Include every variable the app needs in that secret. See [AWS deployment](./aws-deployment.md).

---

## Checklist before going live

1. `JWT_SECRET` is unique and backed up offline.
2. `PUBLIC_FRONTEND_ORIGIN` and `GOOGLE_OAUTH_REDIRECT_BASE_URL` match the **public URL** users open.
3. Google redirect URIs include `{that origin}/auth/google/callback`.
4. ICICI redirect is `{that origin}/icici-return`.
5. `COOKIE_SECURE` matches your TLS posture.
6. Persistent volume mounted at `/app/backend/data` if you need survival across container replace.
