# Key design decisions

This document records **why** the modern stack is shaped the way it is. It is not a changelog; it explains durable architectural choices.

---

## 1. Single browser origin (port 3000) for the “happy path”

**Decision**: Users should open the app on the **Next.js origin** (e.g. `http://localhost:3000`), not the raw API on 8000, when using Google OAuth and ICICI return posts together.

**Rationale**:

- **OAuth state** and **session cookies** are bound to the host the browser used to start the flow. If Google redirects to 3000 but API calls assume 8000 (or the reverse), you get `mismatching_state` and broken sessions.
- ICICI’s flow posts back to **`/icici-return`**; that path must hit the backend **while** the browser’s address bar stays on the same origin you configured in `PUBLIC_FRONTEND_ORIGIN` and broker redirect URLs.

**Implication**: `GOOGLE_OAUTH_REDIRECT_BASE_URL` and `PUBLIC_FRONTEND_ORIGIN` must match the URL users type in the address bar (see [Configuration reference](./configuration-reference.md)).

---

## 2. Path-based reverse proxy instead of a single `/api` prefix

**Decision**: nginx (compose + all-in-one) and Next rewrites enumerate concrete prefixes (`/auth/`, `/api/`, `/home/data`, `/portfolio/data`, …) rather than mounting the entire API under one `/api` namespace.

**Rationale**:

- The backend evolved from **legacy path shapes** (`/home/data`, `/portfolio/data`, etc.) that the modern UI still calls.
- Collapsing everything under `/api/v1/...` would be a large breaking change for both frontend and any bookmarks/scripts.
- Narrow proxy rules reduce the risk that a random Next route (e.g. `/portfolio`) is swallowed by the API upstream; `next.config.js` explicitly avoids rewriting `GET /portfolio` because the backend can redirect in ways that break when proxied.

---

## 3. One secret for JWT and symmetric encryption

**Decision**: `JWT_SECRET` (or `ENCRYPTION_KEY` / legacy `JWT_SECRET_KEY`) is used both for **JWT signing** and for **encrypting** sensitive stored values (e.g. broker credential material, Google OAuth cookie payload).

**Rationale**:

- Operators manage **one** high-entropy secret in `.env`.
- Rotating the secret invalidates tokens and requires re-saving credentials—acceptable trade-off for a self-hosted deployment.

**Risk**: Loss of the secret means **encrypted data cannot be recovered**. Back up `.env` securely and treat it like a root key.

---

## 4. SQLite for application state

**Decision**: `users.sqlite3` and `scrips.sqlite3` on the filesystem, with **template seeding** when files are missing.

**Rationale**:

- No external database service is required for local or small single-node deploys.
- Docker bind mounts can **hide** image-baked files; `db-templates/` holds copies of empty DBs and limit files so first boot still works.

**Limitation**: SQLite is not ideal for **horizontal scale** of concurrent writes. The app is designed around **one backend instance** per deployment unit (single container or single backend service).

---

## 5. `processor` singleton pattern

**Decision**: Many routes obtain a shared `processor()` instance (module-level `breeze = processor()` in several route modules).

**Rationale**:

- Matches legacy behaviour: one orchestrator holds Breeze connection patterns, caches, and master file logic.
- Reduces repeated initialisation cost.

**Trade-off**: Tighter coupling and global state; tests sometimes patch `processor` or `icici_client` at module boundaries.

---

## 6. Breeze session reuse and cache

**Decision**: The processor creates **one Breeze session per request context** and reuses it where ICICI rejects repeated `generate_session` patterns; optional TTL / midnight IST cache behaviour via `BREEZE_SESSION_CACHE_TTL_SECONDS`.

**Rationale**:

- ICICI returns errors (e.g. checksum) if sessions are mishandled; the code path documents broker-specific constraints.

---

## 7. Monolithic Docker image for production (nginx + FastAPI + Next)

**Decision**: Root `Dockerfile` produces a **single** image run by supervisord, with nginx on 3000.

**Rationale**:

- **AWS workflow** pulls one image and runs `docker run -p 80:3000`, avoiding compose on the host.
- Same behaviour as three-container compose: path-based split between UI and API.

**Trade-off**: Larger image and coupled release of frontend/backend versions (acceptable for this project’s scale).

---

## 8. `requests` monkey-patch

**Decision**: Apply a patch so GET requests can carry a body, matching ICICI client expectations.

**Rationale**:

- Third-party `breeze_connect` behaviour is not forked; patching `requests` is localised in `app/core/requests_patch.py` and applied at import time in `main.py`.

**Trade-off**: Global effect on `requests` inside the process; mitigated by this app being the only major consumer in the container.

---

## 9. Optional insecure SSL for `breeze_connect` import

**Decision**: Environment flag `ICICI_BREEZE_INSECURE_SSL` disables certificate verification for the import-time HTTPS download path.

**Rationale**:

- Corporate TLS inspection and some Python/OpenSSL stacks cause **startup failure** before FastAPI binds.
- Opt-in only; documented as unsafe for production unless you understand the risk.

---

## 10. `legacy/` is read-only in this repo

**Decision**: Historical snapshot under `legacy/` must not be modified in normal development.

**Rationale**:

- Preserves a reference for behaviour comparison without merging old and new trees.
- All fixes ship in `backend/` and `frontend/`.

---

## 11. Rate limiting and correlation IDs

**Decision**: Lightweight middleware for rate limits and correlation IDs on every request.

**Rationale**:

- Improves supportability (error JSON includes `correlation_id`) and basic abuse resistance on a self-hosted instance exposed to the internet.

---

## 12. Outlook naming vs Microsoft Outlook

**Decision**: The feature is called “outlook” in routes and settings but implements **market outlook** (RSS + optional AI), not Microsoft Graph email.

**Rationale**:

- Historical naming; documented here to avoid confusion for new contributors.

---

## 13. Explicit live vs mock broker mode

**Decision**: Support both `ICICI_BROKER_MODE=live` and `ICICI_BROKER_MODE=mock`.

**Rationale**:

- Local development and admin/e2e checks need deterministic behavior without outbound ICICI dependencies.
- Mock mode preserves the same auth and route shape so frontend integration remains realistic.

**Trade-off**: Mock mode must never be treated as production-safe; docs and deployment guidance keep this boundary explicit.

---

## 14. Parked orders persist in users SQLite database

**Decision**: Persist parked-order drafts in `users.sqlite3` using migration-backed schema (`parked_orders`).

**Rationale**:

- Draft and staged-order UX needs persistence across page refresh and app restarts.
- Keeping this in SQLite avoids introducing another state store for a single-node deployment model.

---

## 15. Outlook routes keep a frontend proxy boundary

**Decision**: Frontend server route proxy (`frontend/src/app/api/outlook/[...path]/route.ts`) remains in front of backend outlook APIs.

**Rationale**:

- Maintains same-origin behavior for browser clients.
- Centralizes request shaping for streaming and future UI-specific headers without exposing backend topology details to clients.

---

## 16. Fail-closed license enforcement via cached, signed policy tokens

**Decision**: The app trusts a short-TTL (~600s) ES256-signed `policy_token` from breeze-saas-portal, caches the resulting status in memory, and treats a **stale** cache (no verified token for longer than 2× the heartbeat interval) as `unlicensed` rather than continuing to trust the last known-good value indefinitely.

**Rationale**:

- The portal and this instance communicate over an unreliable link (customer network, portal downtime); the app must have a defined behavior for "I haven't heard from the portal in a while" rather than assuming the last good answer still holds.
- Fail-closed (degrade to read-only) is the safer default for a licensing control — a network blip should not silently leave trading permanently enabled for a revoked or expired license.

**Trade-off**: A sufficiently long portal outage puts a legitimately-licensed instance into read-only mode. Acceptable given the self-hosted, single-tenant deployment model and the 300–3600s heartbeat cadence (worst case: read-only after roughly 10–120 minutes of silence). See [breeze-saas-portal/docs/license-management.md](../../breeze-saas-portal/docs/license-management.md) for the signing side of this contract.

---

## 17. In-place self-upgrade via a sibling helper container

**Decision**: When the portal approves an upgrade, the running app container pulls the new image itself but delegates the actual stop-and-recreate to a **sibling `docker:cli` helper container**, rather than restarting itself or relying on an external always-on upgrade daemon.

**Rationale**:

- A container cannot reliably stop and replace itself from the inside — the process performing the swap would be killed mid-operation.
- Spinning up a short-lived helper container only when an upgrade is actually happening avoids running a second permanent daemon on a single-tenant customer instance just to handle the rare upgrade case.

**Trade-off**: Requires mounting the Docker socket into the app container so it can launch the helper — a real privilege escalation surface, accepted here because the instance is single-tenant and already trusts the app process with the host's `.env` file and Docker environment. The helper preserves the host `.env`, data bind mount, and published port; no CloudFormation stack update or EIP change is involved, keeping the upgrade fast and low-risk to the instance's networking.

---

## 18. Scheduled, cache-first reference-data refresh instead of per-request fetching

**Decision**: NSE/BSE bhavcopy, ICICI scrip master, and SPAN baselines are loaded on a startup bootstrap plus a daily IST-scheduled job, cached to SQLite and Redis, rather than fetched on demand per request. The startup bootstrap checks whether the cache is already complete before doing any network work.

**Rationale**:

- These sources are daily-batch by nature (bhavcopy files are published once per exchange session); there is nothing to gain from re-fetching them per request, only latency and load on NSE/BSE's servers.
- Checking cache completeness before re-loading on startup means a quick container restart (e.g. during an in-place upgrade, decision #17) doesn't force a redundant multi-minute download.
- `processor().update_ICICImaster()` (decision #5's `processor` singleton) remains callable from both the legacy manual-refresh admin action and the new scheduled orchestrator without conflicting — the orchestrator calls it with `publish_scrip_index=False` and handles scrip-index publishing itself as part of the broader coordinated load.

---

## 19. Active-chains registry bounds chain-builder work to what's actually subscribed

**Decision**: The `chain_builder` worker only refreshes `(exchange, stock, expiry)` chains that have a live WS subscriber, tracked in an active-chains registry, instead of refreshing every possible chain on every tick.

**Rationale**:

- Ties to decision #7 (monolithic image on modest EC2 instance sizes) — CPU/memory for chain assembly is a real constraint, and most of the possible chain universe has no active viewer at any given moment.
- The first subscriber to a chain pays a warm-up cost (the chain must be built before it's "ready"); this is made visible to the user via a loading state (`chain_readiness.py`'s `wait_for_canonical_chain`, surfaced in the frontend as `ChainBuildStatus`/`SectionGate`) rather than silently serving a stale or incomplete chain.

**Trade-off**: Slightly higher latency for the first request against a chain nobody has viewed recently, in exchange for materially lower steady-state CPU/memory use.

---

## 20. Redis is optional, not required

**Decision**: The app runs with an in-process, TTL-aware `_MemoryStore` fallback (`app/db/redis_client.py`) when Redis is unreachable, rather than treating Redis as a hard startup dependency — unless an operator explicitly opts into strict mode via `REDIS_REQUIRE_CONNECTED=true`.

**Rationale**:

- Consistent with decision #4 (SQLite, single-backend-instance deployment model): this app is designed to run as one process per deployment, so an in-process cache fallback is a coherent substitute for Redis rather than a correctness risk from multiple processes disagreeing.
- Local development and constrained environments shouldn't hard-fail just because Redis isn't running.

**Note**: On the customer CloudFormation deployment, Redis is present by default as a sibling `breeze-redis` Docker container (not a managed cloud service), so this fallback mainly matters for local dev, degraded states, and the brief window during an in-place upgrade (decision #17) where the sidecar might be recreated.
