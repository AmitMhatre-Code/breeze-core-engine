## ICICI-Breeze-Modern

**Local-only modern UI for the ICICI Breeze backend.**

### Stack

- **Backend**: modernised FastAPI app in `backend/` (ICICI integration unchanged)
- **Frontend**: Next.js 16 + React 19 + TypeScript + Tailwind 4 in `frontend/`
- **Runtime**: Docker + `docker-compose` for local-only deployment

### Prerequisites

- Docker and Docker Compose installed
- `.env` file for the backend (repo root; used by the backend container)

At minimum, backend `.env` must define:

- `JWT_SECRET`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- ICICI broker keys as required by the legacy app

### Recommended setup: browser only on port **3000** (Next.js)

Use one consistent host in the browser (`localhost` **or** `127.0.0.1`, not both). Example below uses `http://localhost:3000`.

**Root `.env` (backend reads it — Docker and local API both):**

| Variable | Example | Purpose |
|----------|---------|---------|
| `PUBLIC_FRONTEND_ORIGIN` | `http://localhost:3000` | ICICI challenge posts to `/icici-return`; after broker login, redirect to `/dashboard` on this origin. |
| `GOOGLE_OAUTH_REDIRECT_BASE_URL` | `http://localhost:3000` | Google OAuth `redirect_uri` on **3000** so the session cookie matches (avoids `mismatching_state`). |
| `CORS_ORIGINS` | `http://localhost:3000` | Optional; use if credentialed API calls from the browser need CORS. |

**Google Cloud → OAuth client → Authorized redirect URIs:**  
`http://localhost:3000/auth/google/callback` (add the `127.0.0.1:3000` variant too if you ever use that host.)

**ICICI Breeze / API app redirect URL:**  
`http://localhost:3000/icici-return` (same host/port as above.)

**How you run it:** Next on **3000** with the API on **8000** behind rewrites/proxy (see “Development without Docker” / Docker). **Always** open the app at **3000** and start sign-in there — do not bookmark or open `http://localhost:8000/...` for login.

---

**Edge case — API on 8000 only (no Next in the browser):** leave `GOOGLE_OAUTH_REDIRECT_BASE_URL` unset and register `http://localhost:8000/auth/google/callback` in Google (host must match what you type in the browser).

### Running everything locally

From the repository root:

```bash
docker compose up --build
```

Then:

- App (modern UI + backend JSON endpoints via reverse proxy): `http://localhost:3000`

Inside the compose network, the frontend talks to the backend via the `proxy` container so everything is reachable from a single external port.

### Development without Docker

**One command** (from the repository root; requires `backend/.venv` and `chmod +x dev.sh` once):

```bash
./dev.sh
```

This sources the root `.env`, runs uvicorn on port **8000** and Next on **3000**, and tears down both on Ctrl+C. Override ports or bind host if needed: `BACKEND_PORT=8001 FRONTEND_PORT=3001 APP_HOST=localhost ./dev.sh`.

If **`ICICI_BREEZE_INSECURE_SSL`** is unset, `dev.sh` sets it to **`1`** so `breeze_connect`’s import-time HTTPS download can succeed when verification fails (common behind corporate TLS inspection or on some macOS Python installs). To keep strict verification, set `ICICI_BREEZE_INSECURE_SSL=false` in `.env` or run `ICICI_BREEZE_INSECURE_SSL=0 ./dev.sh`.

ICICI master downloads, `scrip_master.db`, and quantity-limit text files use **`backend/data/`**, resolved from the installed package layout (not the process cwd), same as Docker’s `/app/backend/data`.

Manual steps (equivalent to what `dev.sh` does):

Backend (from `backend/`):

```bash
cd backend
ICICI_BREEZE_INSECURE_SSL=1 PYTHONPATH=./src uvicorn icici_breeze_backend.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend (from `frontend/`):

```bash
npm install
NEXT_PUBLIC_BACKEND_URL=http://localhost:3000 npm run dev
```

Note: this mode uses separate ports (`3000` for Next, `8000` for the backend). If you need a strict single-port experience while developing, use Docker Compose (recommended).

### Main routes in the new UI

- `/` – entry screen that links to `/login`
- `/login` – Google sign-in → ICICI flow
- `/register` – entry into existing Google + ICICI registration flow
- `/dashboard` – overview using `/home/data` and `/dashboard/vix/options`
- `/portfolio` – portfolio table from `/portfolio/data`
- `/orders` – orders table from `/order/data`
- `/strategies` – summary tiles using `/hedge/data`, `/vertical-spread/data`, `/uncovered-shorts/data`
- `/settings` – links into legacy settings pages (`/settings/credentials`, `/settings/quantity-limits`)

