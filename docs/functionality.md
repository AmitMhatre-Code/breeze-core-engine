# Application functionality

Breeze Core Engine is a **browser-based trading and portfolio dashboard** that sits on top of **ICICI Direct Breeze** APIs. Users authenticate with **app credentials** (direct account password) and **ICICI** (broker session). The app surfaces portfolio, orders, option strategies, margin and scrip tooling, and optional AI-assisted market outlook features.

---

## Identity and access

- **Direct app auth**: Login uses `/auth/direct-login` (user id + app password), then `/auth/icici-redirect` to complete broker login.
- **Deprecated path**: Legacy `/auth/login` (ICICI-token login) is disabled and returns HTTP 410.
- **Application session**: After direct auth and ICICI steps complete, the backend issues credentials (JWT in cookies / headers patterns as implemented) so subsequent JSON API calls are authorized.
- **ICICI Breeze session**: Broker API key and session token are stored per user (encrypted); the backend builds `BreezeConnect` sessions to call ICICI on the user’s behalf.

---

## Primary UI areas (Next.js App Router)

Paths below are relative to the site root (e.g. `http://localhost:3000` in development).

| Route | Purpose |
|-------|---------|
| `/` | Landing; navigation toward login. |
| `/login` | Direct sign-in and ICICI login flow entry. |
| `/logout` | Session termination UX. |
| `/register` | New user registration (direct user id/password + ICICI credentials). |
| `/register/correct` | Correct stored ICICI credentials for an existing direct account. |
| `/register/forgot-password` | Start app-password recovery flow. |
| `/register/recover-complete` | Complete app-password reset after broker verification. |
| `/register/delete` | Account deletion flow. |
| `/challenge` | ICICI challenge handling UX (works with backend challenge endpoints). |
| `/dashboard` | Home-style overview: aggregates `/home/data` and VIX/options endpoints. |
| `/portfolio` | Holdings and positions-style data from `/portfolio/data`. |
| `/orders` | Order list from `/book/data`. |
| `/strategies` | Summary entry using hedge, vertical-spread, and uncovered-shorts data endpoints. |
| `/hedge`, `/vertical-spread`, `/uncovered-shorts` | Dedicated strategy views and scans. |
| `/trade-options-chain` | Options chain trading UI. |
| `/strategy-builder` | Multi-step builder: underlyings, chain, margin, execution APIs. |
| `/performance` | Performance metrics from `/performance/data`. |
| `/admin` | Administrative/test surfaces (guarded by backend). |
| `/settings` | Hub linking to detailed settings pages. |
| `/settings/credentials` | ICICI API credentials management. |
| `/settings/quantity-limits` | Quantity limit configuration. |
| `/settings/margin-source` | Breeze vs exchange margin baseline source. |
| `/settings/scrip-master` | Scrip master refresh. |
| `/settings/api-usage` | API usage statistics. |
| `/settings/ai-provider` | AI provider keys for outlook features. |
| `/settings/delete-account` | Account deletion entry. |

The UI uses **React Query** for server state and **Chart.js** where charts are shown.

---

## Backend capability map (by feature)

### Home and session

- **`/home/data`**: Consolidated “home” payload for the dashboard (user-facing summary).
- **Legacy-compatible paths** under `home.py`: login/logout redirects, `icici-return` for broker callback, challenge context, ICICI session posts—supporting both HTML redirect flows and JSON used by the modern UI.

### Portfolio, orders, book

- **`/portfolio/data`**, **`/portfolio/hedge-candidates`**: Portfolio and hedge candidate retrieval via ICICI.
- **`/order/data`**, **`/order`**: Order listing and related operations.
- **`/book/data`**, **`/book`**: Book / positions-style data (implementation aligns with ICICI book APIs).
- **`/book/parked-orders`** (+ patch/delete endpoints): Parked order draft lifecycle.

### Dashboard and volatility

- **`/dashboard/vix`**, **`/dashboard/vix/options`**, ATM variants: VIX and options chain slices for dashboard widgets.

### Strategy analytics

- **`/hedge/data`**: Hedge strategy data.
- **`/vertical-spread/data`**: Vertical spread universe / codes.
- **`/uncovered-shorts/data`**, **`/uncovered-shorts/scan`**, **`/uncovered-shorts/covered-shorts-scan`**: Uncovered shorts analysis and scans.

### Strategy builder

- **`/strategy-builder/underlyings`**, **`/chain`**, **`/covered-shorts-scan`**, **`/margin`**, **`/execute`**: End-to-end builder pipeline including margin calculation and order execution (broker-backed).

### Registration API

- **`/api/register/*`**: Direct registration, correction, delete, and recovery endpoints (`/direct`, `/correct-direct`, `/delete`, `/recover/start`, `/recover/complete`) backed by `users.sqlite3`.

### Settings API

- **`/api/settings/*`**: JSON for credentials, quantity limits, margin source (including SPAN baseline upload/refresh), scrip master refresh, AI provider keys, outlook configuration, API usage aggregates.

### Outlook (market narrative)

- Not Microsoft Outlook: a **market outlook** feature that can combine **RSS headlines** with **configurable AI** (e.g. Google Gemini) using user-stored API keys. Exposed via outlook routes and settings.

### Performance and admin

- **`/performance/data`**: Performance reporting payload.
- **`/admin/*`**: Admin data and test runners (restricted usage), including `/admin/tests/status`.

### Audit

- Internal audit logging is active; an operator-facing `route_audit` module exists but is not currently mounted in the v1 router.

### Health

- **`/health`**: Liveness for orchestration and load checks.
- **`/metrics`**: ICICI client metrics payload for monitoring.

---

## Static and file assets

- **`/static`**: FastAPI `StaticFiles` mount from `backend/static/` when present (sample data, legacy assets).
- **Backend data directory** (`backend/data/`): SQLite databases, ICICI master files, NSE/BSE freeze limit text files, logs—see [Architecture](./architecture.md).

---

## What the application does *not* do

- It does **not** replace ICICI’s official apps for all broker features; it focuses on the flows and data exposed by the Breeze API surface wired in this codebase.
- It is **not** a hosted multi-tenant SaaS by default: deployments are **your** infrastructure (local Docker, EC2, etc.) with **your** secrets in `.env`.

For **how** requests move through the system, see [Flows](./flows.md). For **components and topology**, see [Architecture](./architecture.md).
