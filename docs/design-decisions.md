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
