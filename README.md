## Breeze Modern

Source for this product lives in the **`breeze-core-engine`** repository (engineering name; unchanged).

A **browser-based dashboard** for **ICICI Direct Breeze**: portfolio, orders, option strategies, margin and scrip tools, and optional AI-assisted market outlook. Users sign in with **Google** (application identity) and **ICICI** (broker session). The stack is a **Next.js** front end and a **FastAPI** backend that calls ICICI through **`breeze_connect`**, with **SQLite** and local files under `backend/data/`.

**Documentation**: deeper material lives in **[`docs/`](./docs/README.md)**—functionality, architecture, design decisions, flow diagrams, full configuration reference, and AWS deployment.

---

### Stack (summary)

- **Backend**: `backend/` — FastAPI (`icici_breeze_backend`)
- **Frontend**: `frontend/` — Next.js 16, React 19, TypeScript, Tailwind 4
- **Local runtime**: `docker compose` (recommended single port **3000**) or `./dev.sh` (Next **3000** + API **8000**)
- **Production image**: root `Dockerfile` — nginx + FastAPI + Next (used for GHCR / AWS)

---

### Prerequisites

- **Docker and Docker Compose** *or*, for `./dev.sh`, Python venv under `backend/.venv` and Node for the frontend
- A **repo-root `.env`** file (same file for Docker backend service and local uvicorn). **Do not commit** real secrets.

**Minimum variables** (see **[Configuration reference](docs/configuration-reference.md)** for the full list and optional keys):

| Variable | Role |
|----------|------|
| `JWT_SECRET` | JWT signing and encryption for stored broker material (aliases: `ENCRYPTION_KEY`, `JWT_SECRET_KEY`) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OAuth |
| ICICI API credentials | Usually entered in the app under **Settings → Credentials** after registration (plus whatever ICICI’s portal requires for your API app) |

**Redirects and origins** (must match the URL users type in the browser—`localhost` and `127.0.0.1` are different sites):

| Where | What to register |
|-------|------------------|
| **Google Cloud → OAuth client → Authorized redirect URIs** | `{your-origin}/auth/google/callback` — e.g. `http://localhost:3000/auth/google/callback` when using Next on 3000 |
| **ICICI Breeze / API app redirect URL** | `{your-origin}/icici-return` — e.g. `http://localhost:3000/icici-return` |

**Strongly recommended** when the UI runs on port **3000** (set in the same `.env`):

| Variable | Example | Purpose |
|----------|---------|---------|
| `PUBLIC_FRONTEND_ORIGIN` | `http://localhost:3000` | Post–ICICI redirects to your UI (e.g. `/dashboard`) |
| `GOOGLE_OAUTH_REDIRECT_BASE_URL` | `http://localhost:3000` | Google `redirect_uri` host matches session cookies |
| `CORS_ORIGINS` | `http://localhost:3000` | Browser CORS when calling the API with credentials |

**Edge case**: if you open **only** `http://localhost:8000` in the browser, leave `GOOGLE_OAUTH_REDIRECT_BASE_URL` unset and register `http://localhost:8000/auth/google/callback` and `http://localhost:8000/icici-return` instead. This is not the recommended setup for the modern UI.

Diagrams for login, broker return, and deploy paths: **[User and system flows](docs/flows.md)**.

---

### Running with Docker

From the repository root:

```bash
docker compose up --build
```

Open **`http://localhost:3000`** (nginx proxy on the host). Backend data persists under `./backend/data` and logs under `./backend/logs` via compose volumes.

---

### Development without Docker

One command from the repo root (requires `chmod +x dev.sh` once and `backend/.venv`):

```bash
./dev.sh
```

Loads **`.env`**, starts uvicorn on **8000** and Next on **3000**, waits for **`/health`**, stops both on Ctrl+C. Override ports or bind address if needed, e.g. `BACKEND_PORT=8001 FRONTEND_PORT=3001 APP_HOST=localhost ./dev.sh`.

If **`ICICI_BREEZE_INSECURE_SSL`** is unset, `dev.sh` sets it to **`1`** so `breeze_connect`’s import-time HTTPS download can succeed when certificate verification fails (common behind corporate TLS inspection). For strict TLS, set `ICICI_BREEZE_INSECURE_SSL=false` in `.env` or run `ICICI_BREEZE_INSECURE_SSL=0 ./dev.sh`.

Manual equivalent: run uvicorn from `backend/` with `PYTHONPATH=./src`, and `npm run dev` from `frontend/` with `NEXT_PUBLIC_BACKEND_URL` and `BACKEND_UPSTREAM_URL` pointing at your chosen hosts (see `dev.sh`).

---

### Where to read more

| Topic | Document |
|-------|----------|
| Screens, features, API surface | [docs/functionality.md](docs/functionality.md) |
| Components, topologies, persistence | [docs/architecture.md](docs/architecture.md) |
| Why the stack is shaped this way | [docs/design-decisions.md](docs/design-decisions.md) |
| Flow diagrams (auth, data, CI/CD) | [docs/flows.md](docs/flows.md) |
| Every environment variable | [docs/configuration-reference.md](docs/configuration-reference.md) |
| GitHub Actions + EC2 + GHCR | [docs/aws-deployment.md](docs/aws-deployment.md) |
