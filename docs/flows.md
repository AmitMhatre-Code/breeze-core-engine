# User and system flows

This document describes major flows with diagrams. It complements [Functionality](./functionality.md) (what exists) and [Architecture](./architecture.md) (how components fit).

Legend for diagrams:

- **Solid lines**: typical happy path.
- **Dashed or notes**: alternatives (API-only mode, failures).

---

## 1. Local startup: Docker Compose

```mermaid
flowchart TB
  subgraph Host
    U[Developer]
    B[Browser :3000]
  end
  subgraph Compose
    PX[nginx proxy :3000]
    FE[frontend container\nNext :3000 internal]
    BE[backend container\nFastAPI :8000]
    VOL[(./backend/data\n./backend/logs)]
  end
  U --> B
  B --> PX
  PX -->|"/auth /api /home/data ..."| BE
  PX -->|other paths| FE
  FE -->|NEXT_PUBLIC_BACKEND_URL\nhttp://proxy:3000| PX
  BE --> VOL
```

**Reading the loop**: The frontend server may call “itself” via the proxy URL so server-side rendering and internal fetches see the same path routing as the browser.

---

## 2. Local startup: `dev.sh` (no Docker)

```mermaid
sequenceDiagram
  participant Dev as Developer
  participant DS as dev.sh
  participant UV as uvicorn :8000
  participant NX as next dev :3000
  participant BR as Browser

  Dev->>DS: ./dev.sh
  DS->>DS: source .env, set ICICI_BREEZE_INSECURE_SSL default
  DS->>UV: start FastAPI
  DS->>DS: curl /health until ready
  DS->>NX: npm run dev
  DS-->>Dev: print URLs
  BR->>NX: open http://APP_HOST:3000
  NX->>UV: rewrites /auth, /api, /home/data, ...
```

Environment wiring:

- `NEXT_PUBLIC_BACKEND_URL=http://${APP_HOST}:${FRONTEND_PORT}` — browser uses same host for relative API base where applicable.
- `BACKEND_UPSTREAM_URL=http://${APP_HOST}:${BACKEND_PORT}` — Next rewrites target the API directly.

---

## 3. Single-container production image (supervisord)

```mermaid
flowchart LR
  subgraph Container
    NG[nginx :3000]
    UV[uvicorn :8000]
    NJ[Next server.js :3001]
    SUP[supervisord]
  end
  SUP --> NG
  SUP --> UV
  SUP --> NJ
  Client[Client] --> NG
  NG --> UV
  NG --> NJ
```

Same path rules as compose nginx (`deploy/nginx.all-in-one.conf`).

---

## 4. Direct app login (browser on Next origin)

**Preconditions**: User account already created via direct registration and app password.

```mermaid
sequenceDiagram
  participant B as Browser
  participant N as Next / nginx
  participant F as FastAPI
  participant C as Credentials store

  B->>N: GET /login (UI)
  B->>N: POST /auth/direct-login (user_id + password)
  N->>F: proxy
  F->>C: verify app password hash
  F-->>B: Set bootstrap cookie, redirect /auth/icici-redirect
  B->>N: GET /auth/icici-redirect
  N->>F: proxy
  F-->>B: Redirect to broker login
```

**Failure mode**: Browser origin mismatch (`localhost` vs `127.0.0.1`) can break cookie handoff between direct login and ICICI redirect.

---

## 5. ICICI broker login and `/icici-return`

**Preconditions**: User’s ICICI API app redirect URL registered as `{PUBLIC_FRONTEND_ORIGIN}/icici-return` (same scheme/host/port as the browser).

```mermaid
sequenceDiagram
  participant B as Browser
  participant E as nginx / Next
  participant F as FastAPI
  participant I as ICICI

  B->>E: User completes direct-login step
  B->>F: Redirect to ICICI login
  I->>B: Redirect / form POST to /icici-return
  B->>E: POST /icici-return (same origin)
  E->>F: proxy
  F->>F: Parse challenge, set broker cookies
  F-->>B: Redirect to PUBLIC_FRONTEND_ORIGIN/dashboard (typical)
```

**Why same origin matters**: The browser must send cookies set for the **visible** host; splitting 8000 vs 3000 without proxy alignment breaks the flow.

---

## 6. Authenticated JSON request (dashboard data)

```mermaid
sequenceDiagram
  participant B as Browser
  participant E as Edge proxy
  participant F as FastAPI
  participant P as processor / icici_client
  participant IC as ICICI APIs

  B->>E: GET /home/data (Cookie: access token, etc.)
  E->>F: proxy
  F->>F: Auth context / JWT validation
  F->>P: Build response
  P->>IC: Breeze API calls if needed
  IC-->>P: JSON
  P-->>F: Normalised payload
  F-->>B: JSON + correlation_id on errors
```

Parallel pattern for `/portfolio/data`, `/order/data`, `/dashboard/vix/options`, etc.

---

## 7. New user registration (`/register`)

```mermaid
flowchart TB
  A[User opens /register UI] --> B[Enter user_id app_password api_key secret_fragment]
  B --> C[POST /api/register/direct]
  C --> D{Valid?}
  D -->|no| E[4xx + message]
  D -->|yes| F[Persist user row + encrypted credentials]
  F --> H[Redirect to login / dashboard]
```

Correction and delete flows use `/api/register/correct-direct` and `/api/register/delete`; password recovery uses `/api/register/recover/start` and `/api/register/recover/complete`.

---

## 8. Settings: credentials update

```mermaid
sequenceDiagram
  participant B as Browser
  participant F as FastAPI
  participant CM as CredentialManager
  participant DB as users.sqlite3

  B->>F: GET /api/settings/credentials/data (authenticated)
  F->>DB: Load encrypted blob metadata
  F-->>B: State for form
  B->>F: POST /api/settings/credentials
  F->>CM: Encrypt with JWT_SECRET
  CM->>DB: UPDATE
  F-->>B: ok / message (may require re-login to ICICI)
```

---

## 9. Settings: margin source and SPAN baseline upload

```mermaid
flowchart LR
  UI[Settings margin UI] -->|POST multipart| API[/api/settings/margin-source/upload-baseline/]
  API --> ING[ingest_exchange_baseline_upload]
  ING --> DB[(SQLite table\nexchange baseline)]
  UI -->|POST refresh| REF[/api/settings/margin-source/refresh-baseline/]
  REF --> ICICI[ICICI / external fetch\nper implementation]
```

Large uploads are why Next enables an increased **proxy body size** in `next.config.js`.

---

## 10. Strategy builder: chain → margin → execute

```mermaid
sequenceDiagram
  participant B as Browser
  participant F as FastAPI
  participant P as processor

  B->>F: GET /strategy-builder/underlyings
  F->>P: List / filter underlyings
  F-->>B: JSON

  B->>F: GET /strategy-builder/chain?...
  F->>P: Option chain
  F-->>B: JSON

  B->>F: POST /strategy-builder/margin
  F->>P: margin_calculator path
  F-->>B: margin estimate

  B->>F: POST /strategy-builder/execute
  F->>P: place_order / broker ops
  F-->>B: execution result
```

---

## 11. Uncovered / covered shorts scan

```mermaid
flowchart TB
  UI[Uncovered shorts page] --> S1[GET /uncovered-shorts/data]
  UI --> S2[GET /uncovered-shorts/scan]
  UI --> S3[GET /uncovered-shorts/covered-shorts-scan]
  S1 --> PR[processor + chain data]
  S2 --> PR
  S3 --> PR
  PR --> IC[ICICI option chain / quotes]
```

Strategy builder reuses covered-shorts scan logic where applicable.

---

## 12. Market outlook (RSS + optional AI)

```mermaid
flowchart TB
  CFG[User: /api/settings/ai-provider\noutlook-config] --> DB[(users DB)]
  REQ[GET outlook market] --> SVC[outlook_service]
  SVC --> RSS[feedparser: RSS URLs]
  SVC --> AI[httpx: Gemini / provider]
  SVC --> CACHE[In-memory cache\nhour bucket key]
  SVC --> RES[JSON narrative + disclaimer]
```

---

## 13. Logout

```mermaid
sequenceDiagram
  participant B as Browser
  participant F as FastAPI

  B->>F: POST /auth/logout
  F->>F: Invalidate / clear cookies
  F-->>B: 200 + clear Set-Cookie
  B->>B: UI navigates to /login
```

---

## 14. Admin test runner (operator)

```mermaid
flowchart LR
  ADM[/admin UI/] --> API[/admin/tests/run/]
  API --> SUB[Subprocess / env copy]
  SUB --> E2E[E2E_BASE_URL\nE2E_RATE_LIMIT_BYPASS_SECRET optional]
```

Used for integration checks; see `route_admin.py` for exact behaviour and guards.

---

## 15. CI: publish image to GHCR

```mermaid
flowchart TB
  PUSH[Push to main] --> GHA[ghcr-publish workflow]
  GHA --> BDX[docker buildx]
  BDX --> IM[Image linux/arm64]
  IM --> GHCR[ghcr.io/owner/repo:tag]
```

---

## 16. CD: manual AWS deploy (summary)

```mermaid
flowchart TB
  W[workflow_dispatch] --> OIDC[AWS OIDC credentials]
  OIDC --> TERM[Terminate prior tagged EC2]
  TERM --> RUN[Run new instance + user-data]
  RUN --> VOL{EBS volume?}
  VOL -->|yes| MNT[Mount /opt/breeze-core-engine/data]
  VOL -->|no| LOCAL[Ephemeral data dir]
  MNT --> PULL[docker pull GHCR]
  LOCAL --> PULL
  PULL --> CONT[docker run -p 80:3000\n--env-file /opt/breeze-core-engine/.env]
  CONT --> EIP[Associate Elastic IP]
```

Full checklist: [AWS deployment](./aws-deployment.md).

---

## 17. Error handling path

```mermaid
flowchart TB
  R[Request] --> MW[Middleware]
  MW --> RT[Route handler]
  RT -->|AppException| H1[JSON detail + correlation_id]
  RT -->|HTTPException| H2[JSON detail + correlation_id]
  RT -->|RedirectToLogin| H3[302 to frontend /login]
  RT -->|Unhandled| H4[500 generic message + correlation_id\nstack in logs]
```

---

## 18. Edge case: API-only browser host (port 8000)

If users open **`http://localhost:8000`** only:

- Leave **`GOOGLE_OAUTH_REDIRECT_BASE_URL` unset** so Google callback is registered on 8000.
- Register **`http://localhost:8000/auth/google/callback`** in Google Cloud.
- ICICI redirect becomes **`http://localhost:8000/icici-return`**.
- This mode is **not** the recommended daily driver when using the modern Next UI.
