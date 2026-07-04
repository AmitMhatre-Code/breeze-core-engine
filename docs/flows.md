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

## 12. Market outlook (portal-generated, fetched by this app)

Generation (RSS + AI) now happens centrally on breeze-saas-portal, on an admin-configured schedule, producing one global result for the whole fleet. This app just polls the portal's cached result:

```mermaid
flowchart TB
  ADMIN[Portal admin: API key + prompt\n+ refresh interval] --> PCFG[(portal: market_outlook_config)]
  WORKER[portal: market_outlook_worker] --> PCFG
  WORKER --> RSS[feedparser: RSS URLs]
  WORKER --> AI[httpx: Gemini / OpenAI]
  WORKER --> PRESULT[(portal: market_outlook_result)]
  WORKER --> PCACHE[(portal: Redis cache)]
  REQ[GET /api/outlook/market] --> FETCH[portal_market_outlook.py]
  FETCH -->|GET /api/public/market-outlook/current| PCACHE
  FETCH --> INMEM[In-memory cache\nthis process]
  INMEM --> RES[JSON narrative + disclaimer\n+ staleness warning if portal fetch is failing]
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
  PUSHM[Push to main] --> GHAM[ghcr-publish-main.yml]
  PUSHT[Push to testing] --> GHAT[ghcr-publish-testing.yml]
  GHAM --> BDX[docker buildx]
  GHAT --> BDX
  BDX --> IM[Image linux/arm64]
  IM --> GHCR[ghcr.io/owner/repo:latest + sha]
```

Both workflows are identical (same DRM key baking, same build) and push to the **same** `ghcr.io/owner/repo:latest` tag — a push to `testing` overwrites the same image tag a push to `main` would. There is no separate staging tag namespace.

---

## 16. CD: legacy manual AWS deploy (dormant path, `icici-breeze-modern` only)

This is the **legacy**, `workflow_dispatch`-only path for the older `icici-breeze-modern` image — it does not deploy the current `breeze-core-engine` app. Current-app deployment goes through breeze-saas-portal's CloudFormation stack (flow 19 below).

```mermaid
flowchart TB
  W[workflow_dispatch] --> OIDC[AWS OIDC credentials]
  OIDC --> TERM[Terminate prior tagged EC2]
  TERM --> RUN[Run new instance + user-data]
  RUN --> VOL{EBS volume?}
  VOL -->|yes| MNT[Mount /opt/breeze-core-engine/data]
  VOL -->|no| LOCAL[Ephemeral data dir]
  MNT --> PULL[docker pull GHCR icici-breeze-modern]
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

---

## 19. CD: current deploy path — breeze-saas-portal CloudFormation

This is what actually deploys `breeze-core-engine` in production; this repo's own CI (`ghcr-publish-main.yml`) only builds and publishes the image. Full detail is authoritative in breeze-saas-portal's docs (`docs/aws-deployment.md`, `docs/license-management.md`); this is a summary from this repo's side.

```mermaid
sequenceDiagram
  participant Console as breeze-saas-portal Console
  participant CFN as CloudFormation stack
  participant EC2 as New EC2 instance (Amazon Linux 2023)
  participant App as breeze-core-engine container

  Console->>CFN: Quick-create link (LicenseKey, UserEmail locked)
  CFN->>EC2: Provision instance, EIP, security group, EBS
  EC2->>EC2: Write /opt/breeze-core-engine/.env (JWT_SECRET, PORTAL_API_BASE_URL, DEPLOYMENT_LICENSE_KEY, PUBLIC_FRONTEND_ORIGIN)
  EC2->>EC2: Start breeze-redis sidecar
  EC2->>App: docker pull + run breeze-core-engine image
  CFN->>Console: POST /api/public/register-deployment (static IP)
  App->>Console: startup heartbeat
```

## 20. Portal heartbeat, license status, and self-upgrade

```mermaid
sequenceDiagram
  participant App as This instance
  participant Portal as breeze-saas-portal

  App->>Portal: POST /api/public/heartbeat {public_ip, version, license_key}
  Portal-->>App: policy_token (ES256 JWT, ~600s TTL)
  App->>App: Verify signature, issuer/audience, public_ip claim
  App->>App: Cache deployment_license_status; degrade to unlicensed if stale > 2x interval
  loop every heartbeat_interval_sec (300-3600s)
    App->>Portal: POST /api/public/heartbeat
    Portal-->>App: policy_token (optional trigger_upgrade + target_tag)
    alt trigger_upgrade and upgrade window open
      App->>App: docker pull target image
      App->>App: hand off stop+recreate to sibling docker:cli helper
    end
  end
```

Trading-mutation routes consult the cached status via `require_trading_not_revoked`; a `revoked`/`unlicensed`/`pending_activation`/`trial_denied` status returns HTTP 403 with a read-only message rather than executing the request.

## 21. License activation on ICICI login

```mermaid
sequenceDiagram
  participant B as Browser
  participant App as This instance
  participant Portal as breeze-saas-portal

  B->>App: Completes ICICI Direct login
  App->>Portal: POST /api/public/activate-license {license_key, public_ip, icici_user_id}
  alt verified 403 or trial_denied
    Portal-->>App: rejection
    App-->>B: Login blocked
  else success or network error
    Portal-->>App: policy_token (or timeout)
    App-->>B: Login proceeds (fail-open on network errors only)
  end
```

## 22. Reference data bootstrap and daily refresh

```mermaid
flowchart TB
  START[App startup] --> BOOT[bootstrap_reference_data_on_startup]
  BOOT --> SCHED[Start daily IST scheduler thread]
  BOOT --> WARM[Warm Redis/memory cache from SQLite]
  WARM --> COMPLETE{Cache already complete?}
  COMPLETE -->|yes| SKIP[Skip network load]
  COMPLETE -->|no| LOAD[run_reference_data_load trigger_mode=startup]
  SCHED -->|daily at configured IST hour:minute| LOAD2[run_reference_data_load trigger_mode=scheduled]
  LOAD --> ORCH[orchestrator: NSE/BSE bhavcopy + scrip master + SPAN baseline]
  LOAD2 --> ORCH
  ORCH --> VER[Build new refdata:v_N_ keys]
  VER --> FLIP[Flip refdata:current_version pointer]
```
