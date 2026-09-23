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
- **A context session is reused only while it still belongs to the caller's current token.** "Request context" is a `ContextVar`, and a request's context dies with the request — but the long-lived background threads (`bot-scheduler`, `bot-exit-arming`, `scalper-loop`, `cas-bingo-loop`) never get a fresh one. Before 2026-09-17 the first session such a thread built was kept for the life of the process: after the midnight IST token rollover every signed call on that thread ran on yesterday's session, while the same call from a request worked. Bot 2 skipped the whole SENSEX expiry morning as "could not be priced" that way, and `_has_session` reported a login that had expired. `get_session_breeze` now stamps each session with the `(user_id, broker_token)` it was built for and discards a context session whose stamp no longer matches (logged as a warning). The check also stops one thread serving several users from handing one user's session to another. A session placed in the context without a stamp (test fakes) is still reused as before.

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

---

## 21. Market operating hours are a single global, DB-backed value — not a hardcoded constant, not per-user

**Decision**: NSE/BSE market hours and the exchange holiday calendar are stored in one singleton `exchange_calendar` row (`app/repositories/exchange_calendar.py`), editable only from Settings → Exchange Calendar, and every backend code path — dashboards, chain health, quote routing, the bhavcopy background scheduler, admin routes, IV reference-time math — reads it through `app/services/market_calendar.py`. There is exactly one calendar per deployment, not one per user.

**Rationale**:

- No exchange publishes an API for one-off special sessions (e.g. Muhurat trading on Diwali) or ad-hoc holiday changes. The only way to represent these is an operator hand-editing a value — which means it has to live in a database, not a hardcoded constant, or every special session would require a code change and redeploy.
- Market hours are a physical fact about the exchange, not a per-user preference. An earlier version of this table was keyed per-`user_id`, but that was already inconsistent with the portal-console sync (`app/services/portal_exchange_calendar.py`), which has only ever fetched one shared payload for the whole deployment — there was never a real per-user dimension to preserve.
- Before this decision, `app/core/market_hours.py` hardcoded 9:15–15:30 IST and read a static bundled JSON file, while a *separate*, parallel per-user-aware module (`market_calendar.py`) existed but only two call sites actually used it. Nearly every system-wide caller silently ignored anything an operator configured in Settings — editing the Exchange Calendar page had no effect on the dashboard, chain health, or order-parking logic. This decision collapses both into the one module so that can't happen again.

**Migration**: `app/db/exchange_calendar_migrate.py` backfills the old per-user table on first startup after upgrade — it picks the customized row (if any; most-recently-updated wins ties) as the new global default, and renames the legacy table to `_legacy_user_exchange_calendar_backup` rather than dropping it.

**Trade-off**: In a deployment shared by more than one user (uncommon but possible — see `app/services/squareoff_watch.py`'s handling of multi-user deployments), any authenticated user editing the Exchange Calendar changes it for everyone. This is accepted because the alternative (per-user calendars) doesn't correspond to anything real — the exchange only has one set of hours.


---

## 22. Only a deliberate logout clears the broker session; an expired app session does not

**Decision**: `/auth/logout` branches on `LogoutRequest.reason` (`app/services/session_teardown.py`). A deliberate logout clears the persisted broker session token and the warm Breeze/snapshot/customer caches, as before. The frontend's *automatic* 401 sign-out (`auth-session-expired.ts`, which POSTs `reason: "session_expired"`) clears browser cookies only — the server-side broker session survives until its own midnight-IST expiry.

**Rationale**:

- PB/SL square-off dispatch runs off the `portfolio_pnl_engine` poll loop with no HTTP request in scope, so it reaches the broker via the persisted token (`app/repositories/broker_session.py`). Clearing that token disarms every live Strategy Group.
- Closing the browser has always left monitoring running; an app-JWT lapse in a background tab looked identical to the user but silently ended their protection, and the failure only surfaced *at the breach*, as a failed exit-order placement → Reset.
- The retained token is server-side only and is never handed back to a caller. A signed-out browser still has to log in from scratch; all the retention buys is the background engine's ability to finish the trading day the user already armed.
- A missing body (older client) is treated as deliberate, so the conservative behaviour is the default.

**User-facing consequences**:

- Logging out with live SGs shows a confirm dialog naming them (`components/layout/LogoutConfirmDialog.tsx`) and, on confirm, sends a Telegram alert that monitoring has stopped.
- A session expiry with live SGs sends a Telegram alert saying the opposite — monitoring is *still running* until midnight IST. That reassurance is deliberate: told "monitoring stopped", a user might re-arm on top of rules that are still live, or manually close positions automation is also about to close. The alert is deduped per user (15 min) because every open tab's 401 handler POSTs its own logout.

---

## 23. Strategy Builder margin is portfolio-aware (incremental), not standalone

**Decision**: Every Strategy Builder margin surface — Build-Your-Own, the propose-trades engine, and the covered/uncovered shorts scan — nets a proposed structure against the user's existing open option positions in the same scrip before showing or sizing against a margin figure. The displayed/sized number becomes `M(existing + proposed) − M(existing)` (incremental) rather than the proposed structure priced in isolation, whenever a same-scrip position exists. Full design, including the ten numbered sub-decisions (D1–D10) this entry summarizes, the API contract, and phase-by-phase implementation notes: `docs/strategy-builder-portfolio-margin-plan.md`.

**Rationale**:

- SPAN is a portfolio risk model, not a per-structure one (the same principle already established for the Portfolio page's own margin tile by `processor._compute_netted_margins`, which this feature reuses via `processor._netted_span_for_legs` rather than re-implementing). Pricing a proposed trade as if the account were empty overstates its true cost whenever it offsets, caps, or hedges something already open — and can understate the true cost when it doesn't (D8: a structure can even *release* margin, going incremental ≤ 0).
- Netting is scoped to **one scrip, one exchange, across all open expiries** (D1, D10) — SPAN nets across an underlying's own expiries but not across underlyings or across NSE/BSE, and this app doesn't currently net futures against options (D9; the app is options-only for this release).
- Sizing (lot count) uses the incremental figure, not the standalone one (D3) — the user's margin budget is capital they can actually deploy, and a hedged structure can genuinely afford more lots than the standalone number would ever allow. Because incremental margin saturates rather than scaling linearly with lot count (the offset benefit is used up after a few lots, after which the marginal lot costs the full standalone rate), sizing uses a two-point secant against a live anchor probe (D5) instead of the naive `budget // unit_margin` division used elsewhere in this engine — followed by one shrink-and-reverify pass against the real final-quantity figure, never a convergence loop.
- The Exchange Risk Baseline margin source stays fully offline when netting (D6): it has no live SPAN scanning-risk model in this codebase to net against positions in other expiries, so it nets same-expiry positions only (via the same local risk-array scan already used for its standalone figure) and surfaces a warning naming what it could not net, rather than either spending the live API budget that source exists to avoid or silently guessing.
- A failed positions fetch (or a failed live margin call needed to net) falls back to the pre-existing standalone figure and flags it (D7) — this system never sizes lots against an offset it could not verify, matching the fail-closed convention already established for license status (decision #22 is a different fail-closed instance of the same instinct: prefer a safe stale/standalone answer over a confident wrong one).

**Naming collision, worth knowing before touching this code**: this feature's "benefit from netting against open positions" is exposed as `positions_margin_benefit` (backend) / `positionsMarginBenefit` (frontend) — deliberately **not** `margin_benefit`, which was already a different, pre-existing quantity in the same API responses and UI components (`span_portfolio_scan.compute_portfolio_span_margin`'s *intra-structure* netting benefit — a candidate's own legs netted against each other, e.g. an iron condor vs its four legs priced naked; still rendered by `StrategyLegsPanel`/`BasketLegsPanel`). The two are computed differently, answer different questions, and are shown side by side, not merged.

**Trade-off**: More `margin_calculator` calls per build — roughly 2 standalone-only calls per unique structure becomes 3 (no meaningful overlap with the book, the common case) or 4 (real overlap, needing the secant), plus one shared `M(existing)` call per build that's usually served from the same-day Redis cache `_netted_span_for_legs` already maintains for the Portfolio page (scoped to the IST trading day — see #37). Builds against a scrip where the user holds a large existing book will be noticeably slower.

---

## 24. Broker calls are strictly serialized per user; order placement is never parallelized

**Decision**: Every outbound ICICI Breeze REST call passes through one gate (`app/services/icici_api_pacing.py`) that holds a **per-user lock** — at most one broker call per user is in flight at any moment, across the whole process. Order placement, modification, and cancellation are **never** fired concurrently, not even the legs of a single multi-leg basket. Throughput is managed by *preventing* throttles (a proactive trailing-60s window plus a critical/advisory budget split), not by bursting and recovering with exponential backoff.

**Rationale**:

- **ICICI's limit is a count per rolling window, not a concurrency limit.** Firing four order calls at once does not buy four calls' worth of extra budget — it spends the same four out of the same trailing-minute allowance, just sooner. The only thing parallelism buys is latency; the only thing it costs is headroom. `GlobalIciciApiPacer.wait_for_minute_slot` caps at `_MAX_CALLS_PER_MINUTE = 90` against ICICI's observed ~100 precisely to keep that headroom.
- **The throttle penalty outlasts the minute boundary.** Tripping the limit does not clear at the top of the next wall-clock minute; it puts the session in a cooldown measured in minutes. A burst that saves two seconds can cost ninety. This is the specific failure that motivated the whole proactive design in 2.5.0.
- **Backoff is a recovery mechanism, not a prevention one.** By the time ICICI returns 429, the call has already been made and already counted. Backoff only spaces *retries*; it cannot un-spend a burst. The reactive path is deliberately short — `_MAX_HTTP_ATTEMPTS = 4`, `_MAX_BACKOFF_SEC = 3.0`, default spacing 0.5s — i.e. under ~4s of total patience, which is structurally incapable of outlasting a minute-scale cooldown. That is not a bug to be "fixed" by lengthening it: it is the last-resort path for throttles the window failed to predict, and the real fix is upstream in the window.
- **Serialization is what makes throttle-retry safe at all.** With one call in flight, a throttle is unambiguous: ICICI *refused*, nothing executed, so re-sending cannot duplicate. `squareoff_dispatcher._is_retryable_throttle` leans on exactly this guarantee — it retries throttles for ~50s but never timeouts, because a timeout carries no such promise. Burst four legs concurrently and a mixed result destroys the guarantee: you no longer know which legs landed, so retrying risks a double fill and not retrying risks a half-unwound position. **Placement is not idempotent.** This is the decisive argument, and it does not weaken as ICICI's limits change.
- **Fairness under scarcity is handled by classification, not by racing.** `app/services/icici_call_class.py` splits calls into critical/advisory (unmarked defaults to critical), advisory traffic is shed first, and the last 500 daily calls are reserved for placing and cancelling orders. Parallelism would reintroduce exactly the random-loser behaviour that classification exists to end — whichever call happened to arrive first wins the slot.

**History — this has already been tried and walked back; do not re-litigate it without new evidence**:

- **2026-04-04** (`a53cd5e7`, "Rate Limit Management and Place Order") — first rate-limit handling, purely reactive: send, and back off if ICICI complains.
- **2026-06-13** (`dfb649d8`, "IC and SS Optimisation") — concurrent/async broker fetching was introduced for the strategy engine (`icici_async_fetch.py`, `margin_async_fetch.py`) **and the per-user lock was added in the same commit**. The concurrency was fenced in the same breath it was built: calls were dispatched concurrently but queued single-file at the gate.
- **2026-07-11** (`6699ea42`, "Performance Optimisation") — `icici_async_fetch.py` deleted outright. `margin_async_fetch.py` survives in name only and holds no concurrency primitives.
- **2026-08-03** (`cb6faf3a`, 2.5.0) — the philosophy flipped from reactive to proactive: trailing-60s window, critical/advisory split, reserved order budget.

**What to do instead when a flow is too slow**: reduce call *count*, not increase concurrency. The largest win on record came from exactly that — `attach_reset_details` was fetching the order book per rule on a 2s-polled endpoint, burning 4236 of 4730 daily calls (89.6%) to render an advisory banner; one shared cached read cut ~3600 calls/hour to ~120. No amount of parallelism could have delivered that, and it added no risk. Two forms of concurrency that *are* safe: overlapping broker calls with non-broker work (local computation, DB writes, UI updates), and — irrelevant here, given decision #4's single-instance model and the one-trader-per-deployment reality — concurrency across different users, since the lock is per-user.

**Trade-off**: A multi-leg basket takes leg-count × (spacing + round-trip) rather than one round-trip, and a user with many open Strategy Groups can feel the queue during a busy tick. That latency is accepted deliberately: the alternative trades a few seconds of wall-clock for an ambiguous fill state on order placement, which is the one failure mode this application cannot recover from automatically.

---

## 25. SPAN baselines refresh six times a session, and a standalone publish never bumps the refdata version

**Decision**: The NSE and BSE SPAN risk-parameter files are downloaded on their own fixed intraday schedule — 09:15, 11:15, 12:45, 14:15, 15:45 and 18:00 IST — rather than only with the daily reference-data load. BSE is fetched through the JSON API behind its risk-parameter page, not by scraping it. A publish of SPAN alone writes into the *live* Redis reference-data version instead of allocating a new one.

**Why the intraday cadence**: both clearing corporations republish during the session, and the margins a user sees are only as current as the last file ingested. NSE stamps `i1` the previous evening and `i2`–`i5` at roughly 11:00, 12:30, 14:00 and 15:30 IST; BSE stamps its modes at 04:30, 11:15, 12:45, 14:10, 15:45 and 17:30. Bhavcopy and the ICICI scrip master have no such intraday revisions, which is why they stay on the once-daily schedule rather than the whole load being run six times.

**Why the slot times are fixed in code**: they are chosen against those publish times, so they are not really a user preference. Three of them land within seconds of a BSE stamp, which is safe only because each slot resolves the newest published archive *before* downloading it and skips the ~9 MB transfer when it already holds that archive — a slot that fires early is a no-op that self-corrects at the next one. `SPAN_INTRADAY_REFRESH_ENABLED=false` turns the whole thing off; there is no per-slot tuning.

**Why BSE is an API call and not a scrape**: `Riskparameternew.aspx` looks like it wants a scraper — the user picks a date from dropdowns and a file mode from radio buttons — but the page is an Angular app and its HTML contains no form, no file table, and no `__VIEWSTATE` to post back. The selections are really two JSON calls (`getmaxdate/w`, then `LoadData/w?date=…&flag=0`), and the `File_Path` they return points at `notices.bseindia.com`, a host that does not resolve on the public internet; the page rewrites it to `www.bseindia.com/bsedata/` before downloading, and so does `span_sources.py`. `flag=1` is the binary PC-SPAN set, which this app's XML ingest cannot read — always `flag=0`.

**Why a standalone publish must not bump the version**: `bump_refdata_version()` flips `refdata:current_version` and then **purges the previous generation** (decision #18's copy-on-write scheme). That is correct for the full load, which allocates one version and writes every source into it. It is destructive for a single-source publish: a version containing only SPAN sheets would replace one containing the scrip index, strikes, exchange-code map and bhavcopy, and the purge would delete them — which the chain-builder worker, reading Redis rather than the API process's in-memory mirrors, would notice immediately. So `publish_span_baseline_from_db()` takes the caller's version when given one and otherwise writes into whatever generation is already live. This applies equally to the admin "refresh baseline" action.

**Trade-off**: writing into the live generation means a reader mid-refresh can see old margins for one underlying and new margins for another. That is acceptable for SPAN specifically — sheets are per-underlying-per-expiry blobs set individually, so no single sheet is ever torn, and the margins move by small amounts between revisions. It would not be acceptable for the scrip index, where a half-updated contract set would produce lookups against strikes that no longer exist.

---

## 26. Net option value comes from the SPAN file, and the margin harness measures methods rather than assuming one

**Decision**: The SPAN engine's net-option-value term is summed from the exchange's own settlement premium in the risk file, not priced with Black-Scholes. The short-option-minimum term is read from the file rather than assumed to be zero. And which exposure-margin model matches ICICI is settled by a user-run comparison harness on the production instance, not by argument — nothing about ELM was changed on the strength of a hypothesis.

**Why NOV from the file**: SPAN's final step is `risk charge − net option value`, and NOV is a sum of option premiums. The file states the premium the exchange computed against, per option, in `<p>`. This app previously priced that with Black-Scholes at a hardcoded 20% vol because the field was not being ingested — measured on a live NIFTY ATM short, the modelled premium came out ₹5,941.79 against the exchange's ₹3,880.50, a ₹2,061 error on one lot from an input that was sitting in a file already parsed. The Black-Scholes path survives only as a fallback for baseline rows written before the column existed, and it flags itself in `warnings` when it fires.

**Why SOM is implemented even though it is always zero**: `somTiers` has published a rate of 0 in every NSCCL and ICCL file inspected, so `max(scan, SOM)` currently never binds. It is still read rather than assumed, because the rate is per-underlying and per-day: assuming zero would turn the day an exchange starts charging it into a silent under-margin, and reading it costs one more branch in a parse the ingest was doing anyway.

**Why the harness exists at all**: "correct SPAN" and "what ICICI charges" are different targets, and this repo already contained evidence of the gap without anyone having measured it. Three positions on exposure margin coexisted here — the Portfolio page charges a flat 2% on index shorts only; Strategy Builder charges a tiered 2%/3% index and 5%/5.25% stock with a deep-OTM step-up, its comment asserting 5% is *ICICI's* stock rate against the exchange's 3.5%; and the margin-reconciliation work recorded that ICICI does not block ELM at all. Those cannot all be right. Worse, this app reads exactly one field of the seven `margin_calculator` returns, and the one it ignores — `non_span_margin_required` — may hold ICICI's own exposure figure, which would settle the question outright.

**How it is shaped, and why**:
- **Method matrix, not a single answer.** Every case is priced by 4 SPAN methods × 5 ELM methods, each carrying its own id, rates, thresholds and notes, and each scored against ICICI. The export embeds the full method catalog, so a JSON file read months later still says what produced each number.
- **Two ELM methods differ only in the expiry-day waiver.** They are indistinguishable on an ordinary day and separate only on an expiry day — which is precisely the run that tests this codebase's existing claim that ICICI folds ELM into SPAN on the day a contract expires.
- **Production-only, and it says so.** It refuses outside `ICICI_BROKER_MODE=live`: comparing against mock margins would produce a result-shaped artifact with no information in it. On a developer machine there is no static IP, so there is no run.
- **Advisory, serialized, budget-aware.** Calls go through the ordinary client, so they inherit the per-user lock and rolling-minute pacer (#24), and are marked advisory so a margin study can never be the reason an exit order is shed. A ~50-case run is ~1% of the 5000/day quota.
- **Runs are kept, not streamed.** The question is a comparison between runs — expiry day against ordinary day, before a SPAN revision against after — so a run you cannot return to is a run you cannot use. Twenty are retained.

**What it deliberately does not do**: it does not change any margin the app charges. It produces evidence. Acting on that evidence — unifying the two ELM models, or dropping one — is a separate decision to be taken once there is data, not before.

**What two production runs established** (2026-09-08 expiry day, 59 cases; 2026-09-09 ordinary day, 56 cases):
- `non_span_margin_required` came back **0 in 115/115 cases**. ICICI's quoted figure is a single number and never itemises exposure margin, so the harness cannot measure ELM. ELM rates therefore come from exchange circulars — index 2%, stock 3.5% — not from fitting to the broker.
- NOV from `<p>` returns **exactly 0** on every long-only case where ICICI also returns 0; Black-Scholes NOV is off by −78% to +70%. The `<p>` decision is confirmed, and the fallback should stay a fallback.
- `somTiers` was 0 in every file across both runs, so `max(scan, SOM)` still never binds. Kept as the tripwire it was built to be.

**A trap in the file's `<p>`, and why it is not a bug**: a strike's settlement premium can sit far off the smooth curve its neighbours describe — a BANKNIFTY 59500 CE at 215.00 between neighbours at 822.48 and 765.72. That is not corruption. Implied vol separates the two populations cleanly: strikes that **traded** carry the real settlement price and follow a genuine skew (12.7–20.1% IV, decaying with strike), while strikes that **did not** carry a theoretical price at the series' single flat `<v>` (~20.3% IV, clustered). The premium curve is legitimately non-monotone because flat-vol theoreticals sit above the true skew in the OTM wing. Risk-array entries equal to `<p>` on those strikes are correct too — SPAN caps a long's loss at the premium, which only bites on cheap strikes; the short side of the array is unaffected, which is why scanning risk was never wrong. **Do not "repair" the curve.** A monotonicity or neighbour-interpolation test flags ~7% of strikes and is measuring the traded/theoretical mix; substituting interpolated premiums moved one CNXBAN iron condor from +0.3% to +23.6% against ICICI. This also explains why the Black-Scholes NOV path over-credits on OTM strikes: a fixed σ = 0.20 reproduces the file's flat-vol theoreticals while the market is at 12–15% there.

---

## 27. A missing quote is reported as missing; rules judge price age, never the clock

The P&L engine reprices from the WS quote cache alone (#24 — never REST). When a leg had no cached quote it used to be revalued at **its own entry price**, which is not a neutral placeholder: it makes that leg's P&L exactly zero. Three consequences followed, none of them visible.

- An entirely unpriced book totalled exactly ₹0 and the Dashboard's Open P&L tile showed it, because a live `0` outranks the REST snapshot the page already holds. That snapshot was correct — the Portfolio page, reading the same positions, showed the true MTM at the same moment.
- No threshold can trip at zero, so **every armed stop-loss and target quietly stood down** whenever quotes were absent. The 30s quote TTL therefore acted as an undocumented interlock: after the close, and through any feed gap longer than half a minute, auto-square-off was disarmed and nothing said so.
- `dashboard_day_pnl_live` had the same shape: unpriced contracts contribute nothing, so the tile published a confident figure assembled from whatever else happened to value.

**What changed.** An unpriced leg now reports `pnl: None`, and a book that is not fully priced reports `total_pnl: None`; `/dashboard/live` withholds the figure and the client falls back to its REST snapshot, labelled as a snapshot the way the Portfolio table labels a non-live LTP. Quotes are retained until shortly before the next session opens rather than for 30 seconds, so the last traded price stays available after the close and across feed gaps. Rules are gated on the **age of the stored quote** (`PNL_RULE_MAX_QUOTE_AGE_SECONDS`, default 120s), not on freshness-for-display and not on the market calendar.

**Why age and not the clock.** The exchange decides when it is trading; the calendar in Settings is only our belief about it. Gate rules on `is_market_open()` and a session the exchange extends past the configured close disarms every stop-loss for exactly the minutes that matter most. Gate on ticks and both directions come out right: prices still arriving means the market is still trading, whatever the calendar thinks; prices stopped means there is nothing to judge, whatever the calendar thinks. No clock appears anywhere in the safety path.

**Why permissive, and what that costs.** A stop-loss judged against a two-minute-old *traded* price still protects the position; refusing to judge it leaves the position unprotected, which is what the old behaviour did. Both tiers therefore act within the age window. The cost is that a group square-off can fire on a price that has moved — so `squareoff_dispatcher` re-reads the LTP at dispatch (one Redis read, no ICICI call) rather than pricing the exit off the quote that tripped the rule. Deciding to exit and pricing the exit are separate questions.

**Retention needs the age gate to be safe.** Nothing re-subscribes the feed overnight: the active-chain registry is subscriber-driven and wiped daily, so a quote held "until a newer tick replaces it" could still be sitting there the next morning with no tick coming. Retention is bounded three ways — a TTL tied to the next session open, an explicit clear on the daily reset, and consumers that judge the stored timestamp rather than the key's presence. Only the third is load-bearing; the other two are for the cases where the calendar moved under a running instance.

**Drift is checked before the price gate.** A group whose composition changed under it must be Reset whether or not we hold quotes for its legs — composition is not a price question. Only the threshold arithmetic below it needs prices, and it needs *all* of them, since one unpriced leg makes the group total wrong and a group square-off exits every leg.

**Armed rules are now a feed target in their own right.** `register_positions` subscribes nothing — the engine consumes whatever the pipeline publishes — so a rule armed against a chain no browser holds had no subscriber at all. `chains_requiring_feed()` is unioned into the price-feed watchdog's targets, and the daily registry reset re-pins live square-off groups (previously only `hydrate_group_rules_on_startup` did, so an instance running across midnight kept its SGs armed and unfed). What cannot be judged is counted and surfaced in the WS health status, because "your protection is not currently armed" is a state the user has to be able to see rather than infer.

---

## 28. Underlying identity comes from ICICI's Security Master, never a hand-kept name list

One underlying is spelled at least three ways. ICICI's Breeze API and every contract identity in this app use the Security Master's `ShortName` (`BSESEN`, `CNXBAN`, `RELIND`); the same file's `ExchangeCode` carries the exchange's symbol (`SENSEX`, `NIFTY BANK`, `RELIANCE`); `CompanyName` is what `breeze_connect` stamps into a tick's `stock_name` (`BSE SENSEX`, `INFOSYS LTD`). NSE's derivative bhavcopy adds a fourth for indices only (`BANKNIFTY`, `MIDCPNIFTY`, `NIFTYFPI`).

Those spellings used to be reconciled by hand in four places that disagreed with each other — a `cfg.INDEX_SYMBOLS` set, `cfg.HEDGEABLE_UNDERLYINGS`, a `reference_data/aliases.py` dict, and a private set in `margin_harness/cases.py` (which listed a `NIFMID` that does not exist) — plus a first-word string reduction in `ws_tick_normalize`. Nothing anywhere knew about `NIF150` or `BSEFOC`.

**What it cost.** `INDEX_SYMBOLS` held exchange-style names, so `is_index_symbol("BSESEN")` was False and ELM tiering charged the **5% single-stock rate on index shorts** — 2.5x over, ₹276L against a true ₹109L on one SENSEX basket, which then sized the Strategy Builder's recommendation about a third too small. It was wrong for every listed index but NIFTY and BANKEX, and the same predicate told GTT placement to send `index_or_stock="stock"` for those underlyings.

**Where it comes from now.** `reference_data/symbol_registry.py` is the single source. The Security Master already states the classification per contract — `InstrumentName` is `OPTIDX`/`FUTIDX` on NSE and `OPTIND`/`FUTIND` on BSE, against `OPTSTK`/`FUTSTK` — and the loader was parsing that column into `raw_scrip_data` and then dropping the table. It is now captured into a `symbol_master` table alongside the three names, published into the versioned reference-data cache with the scrip index, and read through one API (`resolve`, `is_index`, `short_name_for`, `exchange_symbol_for`, `aliases_for`, `underlyings`).

**`is_index()` returns `None`, not `False`, when it does not know.** "Never heard of it" and "not an index" are different answers, and conflating them is precisely what charged the stock tier on SENSEX. Callers handle the third case explicitly: ELM falls back to the higher single-stock tier *and* flags `elm_approximate`, so an over-provision is never presented as a measured number, and GTT logs before defaulting.

**One declared table survives, scoped to what no ICICI file contains**: the NSE bhavcopy's own index tickers. Stocks need no entry (NSE and ICICI agree — `RELIANCE`), and neither do BSE's index tickers (`SENSEX`, `FOCIT` already match ICICI's `ExchangeCode`). SPAN pfCode bridges (`BSXOPT` → `BSESEN`) stay in `nsccl_baseline`, next to that file's parser. A publish-time check reports any underlying the bhavcopy names that the registry cannot place, so the next newly listed index shows up in the log instead of silently failing its joins.

**Product decisions still name symbols.** `HEDGEABLE_UNDERLYINGS`, the bots' NIFTY/SENSEX rosters, the navbar ticker and `system_chain_health` all name underlyings deliberately — which underlyings a feature supports is scope, not identity. Re-expressing them as "every index" would silently widen them.

## 29. Raw SPAN archives are retained, and a second SPAN implementation is carried purely as a cross-check

**Decision**: Each day's raw SPAN archive is written to `backend/data/span/<source_date>/` exactly as downloaded and kept for five source dates; the margin harness runs the third-party `marginism` library against that archive and reports its SPAN beside our own. Only the risk figure is taken from it. No production margin path calls it.

**Why keep the raw file at all**: the ingest streams a ~48MB XML into `exchange_margin_baseline` and drops it, which is right for serving margins but wrong for answering "why did this number differ". SPAN is republished six times a session and revisions drift a percent or two, so a figure can only be reproduced against the exact snapshot it came from. Retention is best-effort and never fails a baseline refresh: the refresh is the real work. The archives are the original ZIP rather than the extracted XML, are disposable and rebuildable from the exchange, and are git-ignored.

**Why a second engine, given it returns the same numbers**: it returns the same numbers, and that is the point. Ours and marginism's are independent implementations of the same published algorithm — run against our own stored risk arrays, marginism reproduced our SPAN **bit-identically in 56/56 cases** (max |Δ| 0.0000%). A permanent, automatic agreement check is worth more than a one-off audit, because the failure it catches is drift introduced later. Where the two *can* diverge is ground we do not cover: the intra-commodity (calendar) spread charge from `dSpread`, combined-commodity netting across expiries via `ccDef`/`pfLink`, and futures legs from `futPf`. None of those are exercised by the current single-expiry option cases, so today the comparison reads identical in every row — the informative outcome, not a null result.

**What is deliberately not used**: marginism's `exposure.py`. It classifies index-vs-stock from a hardcoded `index_symbols` tuple, which is exactly the hand-kept name set #28 records as a live money bug. Exposure margin stays ours, from `symbol_registry`.

**Two details that would otherwise mislead**:
- The lookup reports whether it found the **exact** revision or only a neighbouring one from the same day, and the summary excludes inexact comparisons from its agreement figures. Silently substituting a nearby revision would read intraday SPAN drift as engine disagreement.
- The same-day fallback matches on the archive's leading non-numeric name (`nsccl.` vs `BSERISK`), so an NFO case can never be handed the BSE file.

**What it does not establish**: a second SPAN implementation cannot close the gap to ICICI's quoted figure, because both engines compute the same quantity. That gap is a question about what ICICI charges on top, not about our arithmetic.

---

## 30. The index direction signal is one published value built from heavyweight L2 books, and "unavailable" is a state of its own

> **Superseded by #39 (2026-09-19).** Kept for the history of why; the code it describes has been retired.

**Decision**: NIFTY/SENSEX bullish / bearish / neutral comes from exactly one place, `app/services/index_signal/`. For each index's ten heaviest constituents it takes the top-5 bid and ask quantities from the L2 depth feed (NIFTY on NSE books, SENSEX on BSE books), computes each stock's order-book imbalance `(Σbid − Σask)/(Σbid + Σask)`, weights those by free-float index weight (W-OBI), smooths with a 3-second EWMA and applies hysteresis: take a side past ±0.30, fall back to neutral only inside ±0.20. `publisher` writes the result to Redis at the **P&L recompute interval** (Settings → Advanced), and every consumer — the navbar, any other screen, the bots — reads it through `index_signal.reader`.

**Why one published value**: a signal computed separately by each consumer would disagree with itself, because the smoother and the hysteresis carry state. The navbar showing "bullish" while a bot trades a bearish view is the failure this rules out. Redis makes the answer the same in every process, and the payload carries its own `valid_until` (three publish intervals, floor 10s) so a stalled publisher reads as `unavailable` instead of as the last verdict it wrote.

**Why the EWMA is time-based, not per sample**: the smoothing window is a time and the publish cadence is a user setting from 1 to 30 seconds. A fixed-α EWMA stepped at the publish interval smooths over a different horizon for every setting — at 2s α≈0.49, at 30s it is a single snapshot. The engine updates on every depth tick with `α = 1 − exp(−Δt/τ)`, so τ is a real time constant whatever the cadence, and a long gap (overnight, a feed outage) washes the old value out on its own.

**Why "unavailable" is not "neutral"**: neutral is a reading — the heavyweights' books are balanced. Unavailable means there is no reading: `market_closed`, `low_coverage` (less than 70% of tracked weight has a book ≤30s old), `warming_up` (the smoother has not run for 2τ at adequate coverage), `no_constituents`, or from the reader `stale` / `not_published`. Consumers must treat anything other than bullish/bearish as *no directional trade*. This is the same fail-closed stance as #16 and #27: missing data must never look like a benign value.

**Tuning is a setting, never an environment variable**: on/off, tracked stocks, τ, the enter/exit thresholds, minimum coverage, book staleness, depth levels and shadow-log retention are one global row in `users.sqlite3` (`index_signal/settings.py`), edited in Settings → Index Signal and read fresh on every publish loop — the `pnl_engine_settings` pattern. Customer instances are provisioned by the portal's CloudFormation stack, so an env-only knob is one nobody can actually turn. Every change applies live: switching off unsubscribes the depth rooms and publishes an explicit `unavailable / disabled` (the navbar hides its chip for that reason and no other), a new τ restarts the smoother because the old value was smoothed on a different time scale, and a new stock count re-baskets and re-subscribes. The loop always runs; "off" is a state it publishes.

**Why SENSEX reads BSE books**: it was a product decision, made knowing the cost. These stocks trade mostly on NSE, so their BSE top-5 books are thinner and noisier, and the coverage gate does more work for SENSEX than for NIFTY. The ICICI ShortName is identical on both exchanges for every heavyweight (HDFBAN is NSE 1333 and BSE 500180), so one `symbol_registry` lookup resolves both depth rooms.

**Weights are fetched, never hand-kept (#28)**: NIFTY weights come from NSE's `equity-stock-indices` API (per-stock `ffmc`, free-float market cap). SENSEX weights come from BSE's API: `HeatMapData` lists the constituents, `StockTrading` gives each one's `MktCapFF`, and the sum must match `MarketCap?code=16`'s index free-float total within 1%, so a partial fetch fails loudly instead of inflating the names that did arrive. The fallbacks are the niftyindices monthly factsheet PDF (NIFTY only) and then seeded weights, labelled `source: "seed"` in every payload. The refresh runs once per trading day, off-thread, and a failure keeps the last good set. Two findings from the 2026-09-10 evaluation are worth keeping:
- **No third-party library is used.** jugaad-data, nselib, nsepython, nsetools, bse, bseindia and bsedata were all tried. The ones that worked each wrap a single endpoint, and `bse` (the only maintained BSE client) and `nsepython` are GPLv3, which does not belong in an image we ship to customers.
- **NSE's old `equity-stockIndices` path returns 404.** It is easy to mistake for a bot wall. The live path is `equity-stock-indices`, and it needs no cookie warm-up.

**Only the tracked names' relative weights matter**: W-OBI divides by the sum of tracked weight, so a month-old factsheet or a seed moves the signal far less than its age suggests. Names the registry cannot resolve are skipped and reported, and the next-heaviest takes the slot.

**Depth ticks bypass the chain pipeline**: `ws_tick_pipeline.ingest_tick` returns after the raw listeners for `quotes: "Market Depth"` payloads. They have no LTP for the P&L buffer and no contract identity for the chain builder, and about 20 busy books would otherwise take slots in a queue that drops its oldest entry when full.

**The watchdog re-arms the depth feed but never escalates for it**: `ws_price_feed_watchdog` re-subscribes it at the open and on 45s of silence, like index spot, but its outcome is not counted towards the rebuild-the-socket escalation. A refused depth subscribe is that feed's problem and must not cost every live chain its socket.

**Shadow mode before any bot acts on it**: the ±0.30 threshold is an untested prior, and top-5 OBI measures *resting* liquidity, not aggression. A large bid wall is as often a seller being absorbed as a buyer arriving. `shadow_log` records a one-minute sample and every state transition, each with the index spot at that moment. A spot more than 15s old is logged as blank: out of hours the navbar caches a REST close with no expiry, and that close would otherwise pass for the level at the open. `score` then judges the readings at +1/+5/+15 minutes, and it is built against the four ways a hit rate flatters a signal:
- The 95% range comes only from readings a whole horizon apart, because a run's back-to-back minutes are one bet.
- Each directional cell shows its edge over how often the index moved that way after *any* reading.
- Moves under a chosen minimum are flat, neither hit nor miss.
- Flips are scored once each, from the flip's own level. Flips out of `unavailable` are skipped: that is the signal waking up, not an opinion.

Each horizon is paired on its own, so a reading near the close keeps its shorter outcomes. Bots do not consume the signal until that evidence has been reviewed.

**One fixed readiness test, decided in advance**: the bots will scalp on flips, so `shadow_log.readiness` asks a single question: do flips, at +5 minutes, beat the market's trend by more than the trader's breakeven move? The answer is `ready`, `too_early`, `no_edge` or `worse`, and the Settings screen's plain-language summary shows it above the folded full tables. Phase 3 bots must read this same verdict, not re-derive it. The rules:
- **The test is fixed.** The report has 24 cells per index, and letting the summary pick whichever looks best would find a winner in pure noise. +15 minutes is shown alongside as "does the move hold?", never as a second way to pass.
- **A hit must pay for the trade.** `index_signal/breakeven.py` prices the round trip with the Trading Costs model every bot and the backtest use (`bots/charges.py`), never a second rupee figure. It prices one lot of the nearest expiry's at-the-money option at the last session's bhavcopy premium, then converts to index bps with delta 0.5 and the latest logged level. It costs no broker calls. Three-quarters of the cost is flat brokerage plus GST, so a day-old premium moves the answer by rupees. The bid-ask spread is left out because the bhavcopy has no bid or ask. A 1 bp move the called way is not a scalp that made money.
- **Minimums guard against early luck; they are not targets.** Each side needs at least 30 separate calls. The verdict also needs 10 trading days with at least 3 up and 3 down days among them, so a one-way week cannot make one side look clever. Because the 95% range must also clear the trend, a strong signal passes soon after the minimums and a marginal one needs far more calls. These gates only decide when a bot may start trading on the signal. They add no delay to individual flips, which the evidence already scores from the level at the flip itself.
- **Calls dropped within 5 minutes are reported, not gated.** For a bot that trades every flip, each withdrawn call is a round trip paid for nothing.

**Not yet verified against a live capture**: breeze_connect's depth parser is the only source for the payload shape, because live broker calls work only from the production static IP. `depth_feed.depth_sums` therefore keys on the `BestBuyQty-k` / `BestSellQty-k` field names that both exchange layouts share, not on field position. Turn on the tick-debug capture (`/admin/ws-tick-debug`) for the first live session.

**Order-flow challengers run in shadow beside W-OBI (2026-09-13)**: the first shadow day showed no usable information in W-OBI (correlation with the next 1/5/15-minute move ≈ 0.04–0.07) and a persistent bearish lean, which fits the caveat above — resting size is not intent. Rather than retune W-OBI against its own report, one challenger per index was chosen *before* any evidence existed for it, and `index_signal/flow.py` computes it on the feeds already subscribed:
- **NIFTY — futures pressure**: order-flow imbalance (Cont–Kukanov–Stoikov) at the NIFTY futures contract's best bid/ask, averaged with the aggressor imbalance of its traded quantity, from the quote ticks the always-on scalper candle feed receives (`futures_feed.set_quote_observer`). The futures contract is where aggression shows first, and one liquid book beats ten thin ones.
- **SENSEX — constituent queue flow**: the same imbalance at the top of each tracked BSE constituent's book (`depth_feed.set_top_listener`), weighted like W-OBI. SENSEX futures are too thin to carry a signal.

Each flow is a ratio of time-decayed signed and absolute sums, on W-OBI's −1..+1 scale and thresholds, with τ = 30s because single flow events are much noisier than a book level. **Those parameters are constants, not Settings fields**: tuning a challenger against the shadow report would pick the winner after seeing the results, which the fixed readiness test exists to prevent. Challengers are recorded in the shadow log as `nifty:flow` / `sensex:flow` against the incumbent's own index level and judged by the same `readiness` (breakeven from the base index), shown under the incumbent on Settings → Index Signal, and published nowhere else: navbar, screens and bots keep reading W-OBI through `reader` until a challenger's evidence says it should replace it — a decision for the user, not code.

---

## 31. The full API secret is persisted beside the broker token, for one trading day

**Decision**: At login, the full API secret is stored encrypted in `user_broker_session.encrypted_full_secret`, next to the broker token, and shares that token's lifetime. When no request cookie is in scope, `processor._get_full_secret_for_user` reads it before falling back to the stored app half. The lookup order is: request cookie, then an explicitly supplied user fragment, then the persisted secret, then the app half.

**Why the token alone is not enough**: the token identifies the session, and the secret signs each request. breeze_connect sends `X-Checksum = sha256(timestamp + body + secret)` on every call except `customerdetails`. The token was persisted in July so that background work could reach the broker: PB/SL square-off dispatch, bots, the watchdog and the index feed. The secret was not. With no cookie, `reconstruct_full_api_secret(user_id, "")` returns only the app half. `generate_session` still succeeds on that half, because its `customerdetails` probe is unsigned, so the process logged "session created" and cached an SDK whose every signed call failed with `Invalid Checksum`. That lasted until a user request evicted it, and the WS manager's `_sdk` kept the bad instance for as long as the socket stayed up. The 2026-09-10/11 production log shows the pattern exactly. Each session built at startup from the stored token failed on its first signed call (10 Sep 12:40, 11 Sep 07:56). Each one built inside a request worked (11 Sep 06:17, and the rebuilds at 13:08 and 08:08). The first visible symptom was the navbar losing its day's change, because the index previous-close fetch runs on the WS SDK. The more serious one is that after any mid-session restart, portal upgrades included, the PB/SL square-off and bots could not sign orders until someone opened the app.

**What this gives up**: splitting the secret protected against someone with an offline copy of the data, such as an EBS snapshot plus `JWT_SECRET`. It never protected against root on the live host, where the full secret is already in process memory and in every request cookie. For the trading day, that offline copy now yields the whole secret and the day's token. A leaked token dies at midnight. A leaked secret lives until it is regenerated on ICICI, but on its own it cannot trade, because a new session token needs the user's ICICI login and OTP.

**How it is contained**:
- **Its own key**: a Fernet key derived as `sha256(JWT_SECRET + "broker_full_secret_store_v1")`. It is distinct from the token store and the session cookie, so neither ciphertext decrypts the other.
- **One login's worth**: every login overwrites the column, and a login without a secret writes NULL. A secret never outlives the login it came from.
- **Deleted, not ignored**: a row past its IST midnight is deleted when read, at startup (`purge_expired_broker_sessions`), and before the scalping feed lists users. A data-volume snapshot taken after midnight does not contain it.
- **Cleared on a credential change**: `update_credentials` and `rotate_credentials` null the column, and a deliberate logout deletes the row as before. A session-expiry logout keeps it, because keeping it is what lets the square-off still fire (#22).
- **One reader**: only `_get_full_secret_for_user` decrypts it, and only when the request cookie is absent. It never goes into logs, error payloads, the diagnostics bundle or the portal heartbeat.

**Why not Redis**: Redis would keep it off the data volume and survive app-container recreates. But the CloudFormation stack runs `redis:7-alpine` with its default RDB snapshots switched on, written to the root disk, and with `allkeys-lru`. An evicted secret would silently bring the `Invalid Checksum` failure back. Making Redis safe for it means changing the portal stack.

**Rollout**: rows written before the column existed have no secret, so on the first day after upgrading, background sessions stay as they were until the user's next login.

## 32. A bot's stop arms off the WS order feed once its entry orders finish, and REST is used only when the feed looks broken

**Decision**: Bot 2 no longer arms its PB/SL once, straight after placing. If the order feed already reports every one of the bot's orders as done, it arms at once. Otherwise `services/bots/exit_arming` saves the position to `bot_pending_exits` and arms it when the feed reports the fill that completes it. If an unrelated working order on the same expiry is what blocks the guard, it arms when that order ends. Each attempt is one arm-guard read of the order book. REST is used outside an arm attempt only when the feed looks broken, and at most once every 2 minutes per position. The feed looks broken when an order still has no acknowledgement 2 minutes after placement, when the WebSocket is down, when nothing has arrived on the WebSocket for 2 minutes in market hours, or after a restart. The bot never cancels an unfilled leg. If the user cancels it, that cancellation is itself an order event, so the stop then arms on what filled.

**Why**: the arm guard (`strategy_group_arm_guard`) refuses while any order on the expiry is working, and it has to, because that is what prevents duplicate exits after a Reset. Limit orders a few seconds old, freeze-sliced into about nine pieces per leg, are almost never all filled when `place_short_legs` returns. So on the NIFTY expiry of 15-Sep-2026 the single arm attempt failed, and nothing retried it. A 14,885-quantity strangle was left with no automatic exit. The same failure also appeared on every leg of the Telegram reply ("0 of 2 leg(s) placed"), which invited the user to place the trade a second time by hand.

**Why the feed, not a timer**: ICICI acknowledges every accepted order on the account-wide feed, then reports each fill and the terminal state. So silence after an acknowledgement means the order is resting, not that the feed is broken, and polling it would spend the per-minute broker budget (#24) for nothing. An order that was never acknowledged is different, and that absence is the broken-feed signal that justifies a REST read. The listener records every F&O order event, not only ones already being waited on, because the orders go out before the pending exit exists. It is registered before the first slice is placed.

**Why persisted**: this closes the gap between "orders out" and "stop armed", which is when a restart or a portal upgrade would otherwise leave a short open with no stop and no process that knows it. Waiting rows are resumed at startup, and the first check after a restart is REST, because events during the restart reached nobody. Rows from an earlier day are abandoned, since nothing can fill after the close.

**The run log**: a run closes with `exit_arm_pending` and the text "stop arms once every order fills". When the stop arms, `revise_finished_run` rewrites that note to "stop armed at HH:MM" and settles the code to `orders_placed`. The watcher does nothing while its run is still `running`, so it can never race the caller writing the first verdict.

---

## 33. The first shadow evidence disqualified both W-OBI and the flow blend, and the challenger's components are now logged apart

> **Superseded by #39 (2026-09-19).** Kept for the history of why; the code it describes has been retired.

**Decision**: On the evidence of the 2026-09-15 and 2026-09-16 sessions, **neither W-OBI nor the `:flow` challenger may be promoted**, and neither is retired: W-OBI remains the published value because nothing has qualified to replace it, and both keep running in shadow. `index_signal_log` now stores the futures challenger's two halves (`ofi`, `aggressor`) next to the blended `signal`, so the next verdict is about one mechanism at a time rather than about an average of two.

**What two sessions showed** (771 readings per index per label, ~98% carrying a live value, so coverage was not the constraint):

| | directional calls | flips | edge over base rate |
|---|---|---|---|
| `nifty` (W-OBI) | 48 of 771 (6.2%), 42 of them bearish | 30 | unreadable at that sample |
| `nifty:flow` | 357 of 771 (46.3%), 168 bull / 189 bear | 209 | −8.3pp to +1.2pp across every cell |
| `sensex` (W-OBI) | 26 of 771 (3.4%) | 18 | unreadable at that sample |
| `sensex:flow` | 53 of 771 (6.9%) | 46 | +7.8pp at 5m, not stable |

`nifty:flow` is the first version of this signal to produce a judgeable sample, and it is the one that can be called: with n≈100–190 per cell the standard error on a ~40% hit rate is about 4pp, and every cell sits inside that. Its per-day correlations flip sign between the two sessions (+1m: −0.123, then +0.061). W-OBI's own per-day correlations do the same and mostly sit inside two standard errors of zero, which is the same reading as the first shadow day in #30. `sensex:flow` has one significant cell (r = +0.334 at 5m on 09-15) that reverses the next day (−0.138), on 37 bearish calls — one good day, not a signal. It does confirm #30's own caveat that the BSE constituent books are too thin to carry one.

One result is worth keeping as a positive: the incumbent and the challenger are near-independent (correlation of the two published signals: −0.075 on NIFTY, +0.094 on SENSEX). The challenger genuinely measures something else. It simply carries no more information about the next move.

**Why the components are stored separately**: the futures challenger publishes `0.5 * (ofi + aggr)` and returns both halves in `components`, but only the blend was ever written to the log. So the verdict above is a verdict on the blend. A dead order-flow half would mask a working aggressor half, and the reverse, and nothing in the stored rows can tell them apart. That matters more than it would for an arbitrary pair, because **aggressor imbalance is the only mechanism in this system that reads completed trades rather than resting or queued size** — it is the natural next candidate, and the experiment that would judge it had been running for three sessions while discarding the distinction. Two additive columns make the following sessions decisive.

Note what this does *not* cover: `components` carries `order_flow`/`aggressor` only for `KIND_FUTURES`. The constituent challenger (`sensex:flow`) has no aggressor half at all — depth rooms carry no trade prints — so its `components` is a per-stock map and both columns stay NULL. **There is currently no aggressor-imbalance candidate for SENSEX**, and building one would need a per-constituent trade tape, which is the cost #30 already declined.

**Why the columns are added in `shadow_log`, not a migration**: `shadow_log` creates and owns `index_signal_log` outright with `CREATE TABLE IF NOT EXISTS`, and no migration touches it. Splitting one table across two mechanisms is worse than an additive `ALTER TABLE ... ADD COLUMN` guarded by `sqlite3.OperationalError`, which is the pattern `aggressive_order_prefs` already uses on `user_account`. Old rows keep NULL halves, which is the truth about them.

**Why nothing is promoted on two sessions**: #30's readiness gate — 30 calls a side, 10 sessions, at least 3 up and 3 down days — is not met, and it must not be waived because a challenger finally produced volume. Both sessions drifted down (base rates 48–56% down against 35% up at 5 and 15 minutes), which is exactly the one-way week the gate exists to survive.

**Four ways this evidence flatters a signal, all of them hit during the 2026-09-16 review**. These are properties of the data, not of the reviewer, and the next review will hit them again:
- **Pooling sessions manufactures correlation.** Pooled across both days, NIFTY W-OBI showed a consistent −0.12 to −0.21 against the forward move — which reads as a usable contrarian signal, and invites flipping the sign. Split per session, the sign flips (−0.280, then +0.018) and the effect dissolves. Correlate per day, always.
- **Raw hit rates and mean bps flatter whichever side the market took.** On two down days every bearish-leaning signal looks right. Only the edge over the base rate for that horizon is readable; the raw column is decoration.
- **Forward moves must be paired on timestamps, not row offsets.** The bot run logs are gappy by design (no decision row while a position is held — 09-15 has an 11:28–13:29 hole), so `row[i+5]` is not "five minutes later".
- **A signal that rarely speaks cannot be judged at all.** W-OBI's six NIFTY bullish calls across two sessions returned a 100% hit rate at +15m. That is small-sample noise, and a summary that ranked cells by hit rate would have crowned it.

**Volume-confirmed expansion has not yet been measured, and its current configuration cannot measure it.** The momentum scalper's run log carries `close`, `ema`, `session_vwap`, `volume` and `volume_threshold` per minute, so the signal is judgeable from what is already recorded. Across the 122 logged bars of 2026-09-15 the full condition never once co-occurred. Two reasons, and the second is a defect: the session was one-way down, so "above EMA and VWAP" held on 0% of bars for a long-only bot; and the volume test is `volume > 1.5 x the 20-bar mean` while the observed distribution of `volume / volume_ma` has median 0.67, p90 1.14 and p95 1.40. Bar volume is right-skewed, so the mean is dragged upward by the very spikes the filter exists to detect, and 1.5x that mean lands past the 95th percentile: it fires on 4.9% of bars, against 22.1% at 1.0x and 9.8% at 1.2x. "Above-average volume" sounds like a coin flip and is a 1-in-20 event. Any future test of this signal must compare against a trailing **percentile** of the volume distribution, not a multiple of its mean, or it will read "never fired" as "no edge".

**What is settled regardless of which signal wins**: aggressor imbalance as implemented is *not* book-free. `flow.aggressor_step` classifies each tick's traded quantity against the **previous tick's best bid and ask**, because ICICI sends no trade-by-trade tape; it reduces the dependency from L2 top-5 depth to L1 top-of-book, which is a real robustness gain, but it does not remove it. It therefore cannot be reproduced from ICICI history, which serves OHLCV (plus open interest for F&O) at `1minute` and `1second` and carries no bid, ask or tape. Volume-confirmed expansion has no book dependency at either end and is backtestable on the existing futures candle store today. If a book-free aggressor signal is wanted, the estimator has to change on both sides — a tick rule on price alone, live and historical — and that is a deliberate accuracy trade, not a refactor.

---

## 34. The direction signal's third mechanism reads price, volume and open interest, and SENSEX runs it at half strength because ICICI serves no BSE open interest

**Decision**: `app/services/index_signal/expansion.py` is a third mechanism beside W-OBI and the `:flow` challengers. It fires when a price move **and** the traded quantity behind it are both in the top tail of their own recent distributions, and — on NIFTY — it takes a side only when open interest confirms that the move built new positions rather than closed old ones. It needs no order book and no quotes at either end, so unlike everything before it, **it can be backtested**. SENSEX runs the same engine with `require_oi=False`.

**Why a third mechanism at all**: #33 established that W-OBI carries no information across two sessions and that the pre-registered `:flow` challenger, with a judgeable 357-call sample, has no edge either. Both read the book. This one does not read the book at all, which is what lets months of history judge it in an afternoon instead of accumulating live sessions two days at a time.

**Percentile rank, never a multiple of a mean**: bar volume is right-skewed, so its mean is dragged above where most bars sit by the very spikes a volume filter exists to detect. Measured on NIFTY futures (2026-09-15), `volume / 20-bar mean` had median 0.67, p90 1.14, p95 1.40 — so the scalper's `volume > 1.5 × mean` test sat past the 95th percentile and fired on 4.9% of bars, and across 122 logged bars its full condition never once co-occurred. That reads as "no edge" when it is really "never fired" (#33). A percentile says what it means: `volume_percentile = 0.80` is the top fifth of bars whatever the day's absolute level. The price threshold is a percentile for the same reason — 15 bps is unremarkable at 09:15 and extraordinary at 13:00. Both are ranked **window against window**, never a window against single bars.

**The window floor is 15 minutes, and it is arithmetic not taste**: open interest is a position count that moves slowly. Measured across four sessions of NIFTY futures, the median one-minute |OI change| is **0.0093% of outstanding** — about 1,700 contracts out of 18.5 million. Over 15 minutes the median is 0.132%. `ExpansionParams` refuses a shorter window rather than let a caller read rounding error.

**An unwind is neutral, never a reversal call**: price up on rising OI is new longs (bullish); price up on falling OI is short covering, and the rally is an unwind. The unwind quadrants return *no call*, not the opposite one. Scoring them as reversals would be a far stronger claim — "this move will turn" rather than "new money is not behind this move" — and nothing here has evidence for it. Same fail-closed stance as #30's `unavailable`.

**Why SENSEX runs without the OI half**: ICICI serves no open interest for BSE. Measured 2026-09-16 in one session with a working NSE control: NIFTY futures returned OI on 386 of 395 bars (323 distinct values, max 18,039,125), while a SENSEX future *and* a SENSEX option both returned `open_interest: 0` on every one of 376 bars. The field is present and always zero, so it is served as zero rather than absent. The live BFO tick *does* carry OI (the captured fixture shows `OI: 1,547,880`), so a SENSEX OI signal could run live — but it could never be backtested, which is precisely the un-converging position #33 exists to end. SENSEX therefore takes the weaker, testable half, and must be **labelled** as unable to tell a breakout from a blow-off wherever it is shown. Measured on the same real bars, the cost is visible: the full mechanism called 19.0% of bars directional and the OI filter neutralised 47 of 143 expansions, while the half version called 27.9% with nothing filtering the unwinds.

**SENSEX futures are not an option, and this was measured rather than assumed**: on 2026-09-16, NIFTY futures traded a median 3,055 contracts a minute with **no** empty bars; SENSEX futures traded a median of **20**, with **175 of 376 session bars carrying no trade at all**. A trade-based signal there would be blind for 47% of the session. The instrument for SENSEX is the option chain (median 380/minute at the money, 9 empty bars). Note for anyone re-deriving this: a single ATM SENSEX option on its **expiry day** ran 1,337,180 contracts a minute — roughly 3,500× a normal day, and not a number to plan against.

**Zero open interest means absent, never zero**: ICICI serves OI 0 on pre-open bars and on every BSE bar. A zero used as a window's anchor would turn the next real reading into the largest OI rise ever recorded and call it `new_longs`, so `_window_reading` treats any non-positive OI as no reading.

**A window may not span a gap**: without this, the overnight break is measured as a 15-minute expansion on 15 minutes' volume, and the signal opens screaming at 09:15 every day. Any window containing a gap larger than `max_gap_seconds` (300s) is not a reading. A gap does not end the *baseline*, though — yesterday's windows are perfectly good samples of what a typical window looks like, and only the straddling ones are dropped. That is what lets `seed()` warm the distribution from the previous session so the first live call does not wait an hour: seeding cut the warm-up from 370 bars to 148 across five real sessions.

**A call stands for the window that produced it**: a 15-minute expansion is a statement about the next 15 minutes, not a standing opinion, so a call lapses to neutral after `hold_minutes` unless re-fired. It also goes `unavailable` the moment the feed stops, rather than continuing to assert on dead data.

---

## 35. A backtest is a run in the Activity table, and is excluded from every guard that asks whether the bot has already acted

**Decision**: A bot backtest writes a row into `bot_runs` with `trigger = "backtest"`, so one Activity surface shows replays beside live sessions and a replay can be opened for its constituent trades and its audit trail exactly like a live run. Every query that asks *"has this bot already acted today?"* excludes those rows through one shared filter, `repositories/bots.LIVE_RUNS_ONLY`.

**Why one table rather than a separate one**: the alternative is a parallel Activity list, a parallel detail view and a parallel audit download, all of which drift. Sharing the table is what lets a backtest reuse the run detail and the audit link with no second implementation.

**Why the filter lives in one constant**: the hazard is specific and silent. `has_terminal_run_today` and `has_committed_run_today` are what stop the scheduler firing a bot twice a day; the `running` reaper is what closes runs the process abandoned. A backtest row landing in any of those makes a *replay* stand down a *real* session — a bot that quietly does not trade, with a plausible-looking Activity row explaining why. Nothing would alarm. Putting the filter at each call site means the next guard added is the one that forgets it, so the filter is defined once next to the guards and interpolated into each.

**What is deliberately not filtered**: the Activity listing itself (that is the point), and `start_or_resume_session_run`, which already keys on `trigger = 'session'` and so can never see a backtest row.

**Scoring a replay reuses the live report, never its own**: `index_signal/expansion_backtest.replay` writes its readings into the same `index_signal_log` under a `<index>:expansion:backtest` label, and `shadow_log.shadow_report` / `readiness` judge them with the identical fixed test. A backtest that scored itself would be free to be kinder than the live report, which is what #33's "one fixed readiness test, decided in advance" exists to prevent. `shadow_log.purge_label` therefore refuses any label not ending in `:backtest`: a replay must start from an empty series, and live evidence must only ever age out on the retention setting, or the readiness gate's "10 sessions" stops meaning ten real ones.

**A replay is scored over its own range, and retention never touches it** (fixed 2026-09-17): replay rows carry the timestamps of the bars they came from, so two things written for live evidence misread them. `readiness` looked back 60 days from *now*, which scored any older range as empty — `expansion_backtest.score` now passes the range's end as `now` and its length as `lookback_days`. And the retention delete in `shadow_log.record` compared those historical timestamps with today, so a backtest of a range older than the retention setting was deleted as it was written — `:backtest` labels are now excluded, and are cleared only by `purge_label` when the next replay replaces them.

---

## 36. A bot backtest asks for a period and nothing else: unrestricted range, today's lots and margin, real prices, fetched on demand within a daily budget

**Decision**: The clock icon on a bot card opens a dialog with four choices — last trading day, last trading week, last trading month, or a custom range — and nothing more. `backtest_jobs.start_bot_backtest` then fetches whatever history the replay needs (within a daily call budget, outside market hours, on the live broker only), sizes from today's margin, replays on **real ICICI prices only**, and records the result as an Activity row (#35). The `/bots/backtest` page, its probe/coverage/compare panels and the Black-Scholes price toggle are retired from the app.

**The period is not floored, and every replayed day uses today's lot size**: until now replays clipped to `HISTORY_START` (2026-01-01) so that no day depended on an unverified table of past lot changes. The question a card backtest answers is different — "what would this bot, *as configured today*, have done?" — so it sizes with today's lot and today's margin whatever the date (the user's rule). A day when NIFTY traded 75 a lot is replayed at 65, deliberately. `HISTORY_START` survives only as the default start of a range given no dates. One consequence to know: the expiry-weekday maps are verified only for recent history, and a wrong weekday resolves to a contract ICICI has no bars for — which surfaces as a **reported data gap on that day**, never as a mispriced trade.

**Periods are trading days on the exchange calendar**: "last trading day" is the most recent session that has *closed* (today only after 15:30), "last trading week" is five sessions ending there, holidays excluded, and "last trading month" runs from the day after the same date a month back. A custom range that reaches into a session still open is clipped to the last completed one, because a partial day replayed as whole would report a day the bot never finished.

**No bulk pre-cache; fetch on demand, keep what was fetched**: measured 2026-09-17, one bot over a month costs about 70–90 ICICI calls and over six months about 460–550, because one call returns 1000 one-minute bars and a weekly option lives about two calls. `backtest_store.DAILY_CALL_BUDGET` (800 per IST day, across all bots and runs) therefore admits any single run up to roughly six months while stopping a day of stacked long runs well short of the limits the live bots depend on. When the budget is spent, the market is open, or the instance is in mock mode, the replay **runs on what is cached and reports the gap** in the row's notes — the fetch never turns into a failure.

**The cache has a ceiling, but it is a backstop**: `MAX_CACHE_BYTES` is 2 GiB on the 8 GiB data volume. Measured at 164 bytes per option bar with its key, a scalper over six months touches roughly 31 MB, so a dozen bots across several periods sits in the low hundreds of MB. When the cap is exceeded, `enforce_cache_cap` evicts option history **oldest expiry first**, with its fetch records and needs, so a later run simply fetches that expiry again; futures, cash-index and VIX bars are never evicted (about 15 MB for six months of both indices, and needed by every bot and every signal replay).

**Why real prices only**: a Black-Scholes fill is a model's opinion of a price, and a backtest shown on a card beside live P&L must not be flattered by a number no one could have traded at. Days with no traded price are skipped and counted.

**The signals page asks the same one question**: the Backtest button on Settings → Index Signal (volume expansion only — the other mechanisms need order books that history does not carry) opens the same period dialog. `backtest_jobs.start_signal_backtest` fetches the missing one-minute futures bars for NIFTY and SENSEX (`BSESEN` on BFO, verified served 2026-09-17, OI always 0) under the same rules and budget, replays both, and saves the range, notes and summaries in the cache's `meta` table, so the result survives a reload and a restart. It runs as the shared backtest job, so a signal backtest and a bot backtest never run at once. A stopped run keeps the previous result rather than replacing it with half of one. The result is shown on the page rather than as an Activity row: it is evidence about a signal, not a run of a bot.

**What was kept**: the older `/bots/backtest/{overview,probe,fetch,replay,compare}` routes and the model pricer remain for the command-line script and the tests; nothing in the app calls them.

---

## 37. Cached netted margin lives for one IST trading day, never a rolling 24 hours

**Decision**: `processor._netted_span_for_legs` caches ICICI's netted `span_margin_required` in Redis under a key that carries the **IST trading date** as well as the exact leg composition (`portfolio_netmargin:{date}:{user}:{exchange}:{legs}`), and the entry expires at the next IST midnight. Within a day an unchanged book reuses the figure across polls; a new day, or any change to a leg's contract, quantity or action, prices it again.

**Why**: the key used to be composition only, with a rolling 24h TTL. A book carried overnight therefore kept the previous session's figure for most of the next one — the entry was written the first time the book was priced and nothing but a leg change could replace it. On 2026-09-17, SENSEX expiry, Portfolio showed ₹3.63Cr for a short strangle while ICICI blocked ₹5.15Cr (`margin_calculator` returned 51,540,520.64 that morning). Two correct rules combined into a wrong number:

- ICICI folds ELM into its own figure on a contract's expiry day, so the Portfolio ELM overlay is zeroed that day to avoid counting it twice (see the margin-reconciliation model: ELM is otherwise an additive 2%-of-notional overlay).
- That zeroing assumes the SPAN figure beside it is *today's*. The cached one was yesterday's, which contained no ELM — so the page showed ELM counted zero times, about ₹1.5Cr short on that book.

**Every consumer shared the stale value**, because there is one cache behind one helper:

- Portfolio page — group and portfolio netted SPAN, the SPAN + ELM tile and carry return (`_compute_netted_margins`, via `get_positions`, which the dashboard bootstrap and CAS Bingo's `positions_for_underlying` also call).
- Strategy Builder's portfolio-aware margin (#23) — `M(existing)` in `route_strategy_builder`, the propose-trades engine, and the covered/uncovered shorts scan. A stale `M(existing)` subtracted from a fresh `M(existing + proposed)` makes the *incremental* figure wrong in either direction, and lots were sized against it.

The engine's own per-build margin dictionaries (`margin_key`, `structural_margin_key`) live only for one build and were not affected; nothing in the frontend persists margin figures.

**The day is a ceiling on staleness, not a guarantee of freshness**: SPAN also moves intraday with the underlying and volatility, and this cache still returns the first figure of the day for an unchanged book. That was already true within a day and is accepted for the same reason as before — the Portfolio page polls continuously and `margin_calculator` is on the rate-limited, per-user serialized budget (#24). The day boundary is not negotiable because it is where margin rules actually change (expiry-day ELM, a contract's last day) and where a whole session's staleness hid a crore-scale difference.

**Trade-off**: one extra `margin_calculator` call per distinct book (per group, and per underlying for the portfolio total) on its first price each day. Entries written under the old undated key format are simply never read again and age out on their existing TTL, so the fix takes effect on deploy with no cache flush.

---

## 38. Signal variants: the expansion mechanism is read through named, pre-registered variants, and a bot picks one rather than owning a window

> **Superseded by #39 (2026-09-19).** Kept for the history of why; the code it describes has been retired.

**Decision**: `app/services/index_signal/variants.py` holds **signal variants** — the expansion mechanism (#34) with its windows fixed and a direction attached: a price/volume window, an open-interest window (or none), a hold, and `follow` or `fade`. Each is published under its own Redis key (`signal:index:variant:<id>`, read through `reader.get_variant_signal`), shadow-logged under its own label (`nifty:expansion:<id>`) and judged by the same fixed readiness test (#33). Bot 3's `entry_signal` names either its own momentum signal or a variant; Bot 4's `entry_filter` can hold a fly while a named variant has a live call, or while India VIX is rising. Settings → Index Signal lists, creates and deletes variants.

**Why (2026-09-19)**: the 21-session expansion backtest found NIFTY calls reliably wrong-way (39% right at +5 min, mean −1.3 bps; P(mean<0) ≈ 0.97 on a day-block bootstrap), but the fade is ~3–6 index points, below an option round trip, and 68–78% of it came from 3 days. The honest next step was to run candidate readings side by side, each with its own evidence, and let paper trading on real option prices decide — not to hand-flip a sign in one bot. The starting set: the incumbent (15m / 15m OI / hold 15 / follow — it keeps the `nifty:expansion` label and its month of evidence, and cannot be deleted), its fade, 5m price/volume confirmed by 15m OI, and 5m price/volume with no OI.

**The bot never decides a window**: a variant's hold is how long its call stands, and a Bot 3 trade on a variant is closed when that hold elapses (`ReasonCode.SIGNAL_WINDOW_ENDED`), replacing the 90-second time-invalidation that was built for momentum runners — a fade measured over 15 minutes would otherwise be timed out long before its move. The hold is stamped on the cycle at entry (`detail.hold_seconds`), so changing the bot's signal or deleting the variant never changes how an open trade exits. The stop and the ladder still apply throughout. One trade per call: a call re-fired while held is the same call, extended (`ExpansionEngine` keeps `call_started_at`), and the fresh-signal rule keys on that exactly as it keys on a momentum run's first candle.

**A variant is created and deleted, never edited**: evidence belongs to the exact definition that produced it. The id is derived from the parameters, so a duplicate definition is refused rather than counted twice, and deleting a variant deletes its live and replay rows (`shadow_log.purge_variant_label`, which refuses anything but a user variant's label). Deletion is refused while any bot is set to the variant: a bot on a deleted variant would read `unavailable` and never trade, with nothing saying why.

**Only the OI window has the 15-minute floor**: #34's floor exists because one-minute OI change is rounding error. A 5-minute *price* move confirmed by 15 minutes of OI is legitimate, so `ExpansionParams` now takes `oi_window_minutes` separately and applies the floor to it alone; a variant that reads no OI has no floor. The rollover-week exclusion likewise applies only to variants that read OI.

**Fade is a transform, not a second engine**: variants that differ only in direction share one engine (and the incumbent's windows share the published engine). `variants.apply_direction` swaps bullish/bearish and negates the strength, so the shadow report scores the *traded* direction; `unavailable` and `neutral` pass through untouched, because an unreadable signal is never a trade (#30).

**Backtests replay the same readings**: `expansion_backtest.replay_states` is the one replay loop — the signal backtest logs it under `<label>:backtest`, and Bot 3/Bot 4 backtests act on it (`backtest_common.variant_readings`, built over the range plus a week of warm-up so the baseline carries as it does live). `run_backtest` refuses a variant config without its readings, so a card backtest can never silently replay the momentum signal for a bot that trades a variant. The signal backtest now also applies the rollover exclusion, which it previously skipped.

**VIX filter**: judged on ICICI's 1-minute INDVIX bars both live (today's bars, at most one call a minute, only on a pass that would otherwise enter inside a window) and in replay (cached under `spot_candles` / `INDVIX`, fetched with every fly backtest). That ICICI serves INDVIX at 1-minute granularity was **not verified against the live broker** when this was written; if it does not, the filter reads "no series" and holds every entry, which is the fail-closed reading.

**Defaults**: Bot 3's `entry_signal` defaults to the 15-minute fade — the user's decision, to gather paper evidence; existing stored configs pick it up on read. Bot 4's filter ships `none`. Both scalpers still ship in paper mode, and a changed `entry_signal` or `entry_filter` is a material config change, so any earlier paper unlock does not carry over.

---

## 39. Signals are a fixed grid of replayable mechanisms, nothing about a reading is stored, and a bot may trade a signal only after a 30-day backtest

**Decision** (2026-09-19; the full requirement set and every decision behind it are in `docs/signals-streamline-plan.md`). NIFTY/SENSEX direction signals are a fixed grid: **volume expansion** (#34) and **momentum** (Bot 3's EMA/VWAP/volume signal, lifted out of the bot) × **1, 5 and 15 minutes** × **NIFTY and SENSEX**, twelve series in `app/services/index_signal/`. Every series reads only one-minute OHLCV+OI futures bars — NIFTY near-month on NFO, BSESEN near-month on BFO — which is exactly what ICICI's `get_historical_data_v2` serves. W-OBI, both `:flow` challengers, the constituent depth feed and weight fetching, user-created variants (#38), the shadow log and its readiness verdict are retired, with their tables. This supersedes #30, #33 and #38; #34's mechanism stands, and #36's backtest rules are extended.

**Why only what history serves**: a signal that reads something history does not carry (depth, bid/ask, the tick's `avgPrice`) can only be judged by accumulating live sessions, two a week — #33's non-converging position. A signal that reads bars can be judged over months in an afternoon, and its live readings need no recording: once ICICI serves the day's bars, a backtest of that day *is* the live session's audit trail. So nothing about a reading is stored; Redis holds only each series' current payload.

**Live equals replay by construction**: one `SeriesEngine` (`series.py`) owns a call's lifetime for both paths; the publisher feeds it tick-built bars, `replay_series` feeds it history. Four things make the two agree, each found while building it:
- **Readings are timed at the bar's close.** #34's engine timed a call from the bar's start, which the live publisher (seeing a bar only once it closes) could never see — a one-minute call would have lapsed before it was published.
- **A re-fire at the lapse moment extends the call.** A momentum candle is judged exactly when the previous candle's call would end; consecutive firing candles are one call, one trade.
- **Momentum's VWAP is rebuilt from bars live too** (OHLC/4 × volume, pre-open included), not read from `avgPrice` — 0.98 pts mean difference, never a side flip (#36's measurement).
- **Live warms from the same bars a replay warms from.** Each trading day the engines are rebuilt from the history cache's last two sessions plus today's bars; missing sessions are fetched (about one call per index, advisory — the only history call made in market hours). Levels (EMA, VWAP, window anchors) reset every session; only size rankings carry over, and no window spans the overnight break, so a gap at the open is never read as a move. *Amended by #40*: the momentum EMA is now carried over shifted by the overnight gap, which keeps that last property — a flat gapped open sits exactly on the line — while ending a daily blind spot that ran to 11:30 on the 15-minute series. Today's bars — inputs, not readings — stay in Redis until midnight, so a restart rebuilds the day exactly.
The live bar builder writes a flat zero-volume bar for a quiet minute (as ICICI's history does) only within five minutes of the last print; a longer silence stays a gap. The first bar after a (re)start has unknown volume. Tick-built bars match history on ~97% of closes (#36), and those rare differences are the only way live and replay can disagree.

**Momentum's volume test is a percentile**: the candle's volume must rank in the top fifth of the trailing 20 candles (#33's finding that 1.5× the mean sat past the 95th percentile). Its parameters are fixed constants; the per-bot tunables are gone.

**The gate is coverage, not merit**: a mechanism is available to bots once a completed signal backtest's range spans ≥30 calendar days on the mechanism's current version (`gate.py`; gaps allowed). The verdict is shown beside it for the user to judge. It applies to Simulation and Live alike, is checked when a bot is armed or an armed bot is saved (switching off and editing a switched-off bot are never refused), and on every runtime pass — a version bump closes a mechanism under a running bot. Backtests are never gated. **Consequence on deploy: nothing is available until a 30-day signal backtest has run on the new versions**, so every signal-reading bot stands down, with a run-log reason, until then.

**Bots choose a cell and a direction** (`SignalChoice`: mechanism, duration, follow/fade). Bot 3 trades it; the 90-second time stop is retired; stop and ladder apply. *Superseded by #40*: a trade was also closed when its call ended, which made the signal's window the maximum hold. It is now closed by the stop, the trail, a call the other way (`signal.call_reversed`, which the replay uses too) or the square-off. Bot 4's `signal_quiet` filter holds a fly while the chosen series has a live call. CAS Bingo reads flips from its chosen series, recomputed for the day from today's bars (`publisher.today_series`) rather than a log; its strength threshold is gone (every expansion call is already a top-fifth move; momentum has none); the credit rule's "move since the open" is measured on the futures' own open so the basis stays out of a 0.5% trigger; the direction applies to the debit spread only. Stored configs map onto the grid on read (the 15m fade stays the 15m fade; `momentum` becomes momentum 1m).

**Bot backtests compare every signal setting**: Bot 3 twelve ways (mechanism × duration × direction), Bot 4 seven (no signal filter, plus each series' quiet test), CAS Bingo six or twelve depending on strategy; the saved setting's trades are the Activity row's own, and one zip per run holds the comparison, each setting's trades/daily/decisions, and the trail (`bots/backtest_combos.py`). CAS Bingo is newly backtestable (`cas_bingo/backtest.py`), with two stated approximations: credit spreads are sized by maximum loss per lot (margin has no history), and liquidation of other positions is not replayed.

**SENSEX runs on thin data, labelled as such**: BSESEN futures trade a median 20 contracts a minute and carry no OI (#34), so SENSEX expansion reads price and volume only, and its 1-minute series is mostly silent. The backtests and the gate decide whether it is usable; nothing here assumes it is.


## 40. A signal is scored at every horizon in both directions against a size-aware cost bar, and a signal trade is no longer closed by its own clock

**Decision** (2026-09-21, from reading the 2026-04-01 → 09-18 run: 12 series, 117 sessions, 17,200 calls). The signal backtest scores each series at 1, 5, 15 and 30 minutes, **follow and fade**, reporting the average move net of costs and how far that average stands out from the day-to-day scatter; the cost bar is priced on the size actually traded and now includes the spread; a signal trade is closed by its stop, its trailing stop, a call the other way, or the square-off — never by its call running out. Expansion goes to version 3 and momentum to version 2, so **both mechanisms are closed to bots until a fresh ≥30-day backtest runs** (#39's gate).

**What the run showed, and why each piece of the old scoring hid it.** The grid is not information-free: an expansion call is followed by a move *against* it, reliably — NIFTY 1m −0.44 bps at +1 minute (t −4.1 across 107 sessions), SENSEX 1m −0.52 (t −6.7 across 117). Three separate things kept that off the page.
- **Hit rate cannot tell "knows nothing" from "knows something, inverted".** Both print as a poor hit rate. Five of the twelve series came back `worse`, and the page's headline — which counted only `verdict == "edge"` — said *"No series showed an edge"* over a run whose findings were all fades. Both directions are now scored and named.
- **The horizon was welded to the window** (#39's decision 5), so a 1-minute call was only ever judged one minute later. Its information peaks at +15: NIFTY −1.16 (t −2.3), SENSEX −1.53 (t −5.8). The forward moves were already in `readings.csv`; nothing was reading them.
- **The bar was priced on one lot.** About ₹47 of a ₹61 round trip is flat brokerage and its GST, which does not grow with size: 0.80 bps at one lot, 0.31 at five, 0.24 at ten. Every verdict was measured against a bar roughly 3.3× the one a real position pays. The opposite error ran alongside it — `breakeven.py` left the bid-ask spread out entirely and said so — so the bar was also too kind at the sizes where brokerage stops mattering. Both are fixed: `cost_lots` is a Signals-page setting (a fact about the trader, not a tuning knob), and the spread comes from `scalping/spreads.spread_stats()`, the same observed median the bot backtests use, labelled `observed` or `default`.

**`t`, not a hit rate, is the test.** Each horizon reports the average net move and how many times its own day-to-day scatter that average is, taking each session's average first — pooling overlapping calls manufactures confidence (#33). `stands_out` needs a positive net, t ≥ 2 and ≥20 sessions, so a large average built on a few lucky days cannot become a headline.

**What survived the arithmetic, and what did not.** Fading the 1-minute expansion held 15 minutes: SENSEX +1.14 bps net in a held-out Jul–Sep half (t 3.2), NIFTY +0.48 (t 1.1). Nothing else replicated — strength quartiles, OI quadrant, time of day, VWAP stretch and a day-volatility regime all failed a train/test split. The regime split in particular reversed sign once the day's range was measured *as at the signal*, rather than over the whole day: a look-ahead that looked like a finding.

**Spikes that follow a dead minute are no reading** (expansion v3). The price move is measured from the close of the bar W minutes back; when that bar never traded, ICICI still serves it with its close carried forward, so the "W-minute move" happened over however long the market had been silent. BSESEN has no trade in 36% of minutes. Faded at +15, 1-minute calls whose anchor traded were worth +1.64 bps (t 5.6) and calls whose anchor was dead +0.41 (t 0.5). The rule is applied at every window, though the evidence is concentrated at the short one, because it is the same defect at every window and a threshold picked off the results is how a backtest gets fitted to itself. It gates the current reading only, never the baseline — dropping a third of SENSEX's baseline would move the percentile thresholds for unmeasured reasons.

**The momentum trend line is carried over, shifted by the overnight gap** (momentum v2). EMA(9) on d-minute candles rebuilt daily said nothing until 09:15 + 9d — 11:30 at fifteen minutes, on **every one of the 117 sessions**, first call at 13:00, and no call at all on 101 of them. Fifteen minutes is the reading the navbar shows. The seed is yesterday's final EMA plus (today's first candle open − yesterday's final candle close), which keeps what decision 15 actually protected: a flat gapped open closes *exactly on* the line, so the gap alone can never fire a call. The seed decays as any EMA does (13% of the weight after nine candles, under 2% after eighteen), which is also why a live engine warmed on two sessions and a replay warmed on a week converge. With no previous session there is no seed and the nine-candle wait applies, which is the fail-closed answer.

**The call's clock is gone.** Closing a trade when its call ended made the signal's window the maximum hold, so choosing the 1-minute signal silently chose a 1-minute maximum hold and the trailing stop — the entire mechanism for letting a winner run — never got to move. 89% of 1-minute calls ended by simply lapsing; median call life was 1.0 minutes with a 90th percentile of 1.0. A call merely going quiet is now nothing. A call **the other way** still closes the trade: that is not the clock running out, it is the signal contradicting the position, and `unavailable` is not a reversal (#30), so a feed blip holds rather than flattens. The known cost, accepted deliberately: a quiet trade can now sit open until the hard square-off, so a position can tie up margin for far longer than its signal's window.

**What is deliberately not built.** A cross-check between the two mechanisms — fade an expansion spike only when momentum does not confirm it — was the one conditioner that replicated (all four train/test cells positive, +0.5 to +1.1 bps net). It is left out because it breaks the rule that a series is a pure function of its own bars, and that rule is worth more than a weak result. If it returns it belongs on the bot side, as a filter, not inside a mechanism.

**The numbers above are futures moves times a 0.5 delta, with no decay and no gamma.** They rank signals against each other. Whether any of it survives real option prices is the bot backtest's question (#36), and nothing here should be armed before that has answered it.

---

## 41. A backtest is bounded by memory as well as by data, and stops itself rather than being killed

**Decision** (2026-09-23, from three consecutive Bot 3 backtests that never finished). A replay holds a **rolling window of days**, not the whole period; it holds the **last two signal series**, not all six; each signal setting's decisions are **written to the run's zip as that setting finishes** rather than kept to the end; and before it starts, and between settings, a run **reads how full the container is and refuses to go on** past `backtest_jobs.MEMORY_CEILING` (80%). The refusal is an ordinary failed run with a sentence saying what to do; the route answers 503, not 500, because the app is full rather than broken.

**What was actually happening.** The card reported `backtest_interrupted` — "the app restarted during the replay" — and the natural reading was that the instance had been stopped underneath it. It had not. In each case only **uvicorn** died: no `Shutting down`, no `Application shutdown complete.`, no traceback, no `backtest … job failed` from the job's own `except`, just a log that stops mid-stream and a new PID seconds later, while the **chain-builder worker in the same container ran straight through**. That is the signature of `SIGKILL` from the kernel's OOM killer, which picks the fattest process in the cgroup and leaves supervisord (PID 1) and its other children alone; `autorestart=true` then brings uvicorn back, and the *next* startup's `reap_orphaned_backtests()` is what finally marks the run. The container is capped at `--memory 1400m` on a 2 GiB `t4g.small` with Redis holding another 450m, so the ceiling is real and close.

**Why it took a whole trading session to show.** The same replay survived 74 minutes on a process that had just booted and died in four on one that had been up through a full session of ticks, chains and snapshot cells. Nothing about the backtest changed; the headroom did. A guard that reads the *actual* reading, rather than a rule about period length, is the only kind that can tell those two runs apart.

**Where the memory went.** Bot 3's backtest replays twelve signal settings (#39's grid) over one period, and several things grew across all twelve at once: the shared `OptionBook` kept every contract of every day for the length of the job; `readings_cache` kept all six signal series; `record_decisions` produced one row per minute each setting was flat, all held until the zip was written at the very end; and the zip was then built from every one of them in one dict. The peak was at the finish, which is why a run that looked healthy for minutes died just as it was about to have something to show.

Measured on a synthetic month and nine months (`tracemalloc`, so Python allocations only — real RSS is higher):

| term | 1 month | 9 months | after |
|---|---|---|---|
| `readings_cache`, all six series | 54 MB | 470 MB | ~160 MB, two series |
| `OptionBook`, whole period | 54 MB | grows with the period | ~5 MB, flat |
| decisions, twelve settings held | 33 MB | ~300 MB | one setting's: 3 MB / 25 MB |

The first estimate of the decisions term was three times too high; Python interns the repeated reason strings, and the rows are smaller than they look. Measuring first is why the readings cache — assumed at the outset to be irreducible working set — turned out to be the largest term of all at nine months. (Held-six and held-three were measured directly at 470 MB and 252 MB; held-two follows from those two points, because walking all twelve settings under `tracemalloc` a third time costs more than the answer is worth.)

**The bound is on days, not on contracts or on bytes.** A replay walks its days in order and never looks back, so `CACHED_DAYS = 2` costs nothing but re-reads, and a re-read is an indexed query against a local SQLite file. A byte budget would have needed a size model for `HistCandle`; a day window needs only the access pattern, which the replays already guarantee. `OptionBook.needs` is deliberately outside the bound — it is the run's output, not its working set.

**Bounding the readings cache turned out to be free, for a reason worth pinning.** The cache exists so that settings sharing a series build it once, and the obvious reading is that all twelve settings collectively need all six series, so nothing can be dropped. They do — but not *at once*. `SeriesKey` is `(mechanism, duration, index)`, direction is not part of it, and `backtest_combos` loops direction innermost, so the two settings that share a series (follow and fade) always run **back to back**. Keeping the last `KEEP_SERIES = 2` therefore rebuilds nothing at all. Two rather than one is not margin: Bot 3 and the fly need one series live, but **CAS Bingo needs two**, because one replay of it walks both enabled indices and asks for that setting's series once per index — at one it would rebuild on all twelve. `ReadingsCache.rebuilt` counts drops that were later rebuilt and a test asserts it stays at zero for every bot, so if a combo order ever stops being adjacency-friendly it says so rather than quietly paying for it.

**Why `anon` and not `memory.current`.** `memory.current` counts the page cache, which a backtest's own SQLite reads inflate by hundreds of megabytes and which the kernel reclaims long before it kills anything; tripping on it would refuse runs that were never in danger. `app/core/memory.py` reads the cgroup's **anonymous** memory — the part with nowhere to go — and falls back to this process's RSS where there is no cgroup. A reading that cannot be taken at all (a dev machine, a test) is **not** treated as a refusal: the guard exists to replace a silent kill, and a machine that cannot be killed this way does not need it.

**What this does not do.** The guard fires between settings, not inside one, so a single enormous custom range can still outgrow the container within one replay. What remains inside one setting now scales with the period rather than with the period times twelve: two signal series, one setting's decisions, and the futures and cash bars the replay loads. Raising the instance past `t4g.small` was considered and declined for now — it is the portal's CloudFormation stack, a per-customer cost, and a run that needs more than this is a run that should be asked for in halves. Note too that memory is not the only ceiling on a long period: the option bars have to be cached first, and `backtest_budget` spends at most a few hundred ICICI calls a day outside market hours, so a nine-month run is several days of fetching before it has much to replay.

---

## 42. A backtest fetch is planned against the deployment's remaining allowance, and the app refusing its own call ends the fetch

**Decision** (2026-09-23, from a nine-month Bot 3 fetch that spent half its wall clock on calls that never left the process). The one-click backtest plans its fetch against **`min(backtest budget remaining, advisory headroom)`** — `api_usage.advisory_headroom()`, what is left before the reserve line — and says so in the run's notes when the second is the binding one. And an `advisory_shed` refusal raises **`AllowanceSpent`**, ending the fetch at the first one, because it is a fact about the day rather than about the window.

**What happened.** A 2026-01-01 → 09-21 run fetched cleanly for 35 minutes, then produced **1,631 identical failures** and reported "No progress this round: every request errored." The failures were HTTP 429s, but ICICI never saw them: they carried `icici_throttled: false, advisory_shed: true`, the app's own refusal from `icici_api_pacing.build_shed_error`. The deployment had crossed `AMBER_MAX` (4,500 of 5,000) for the day, and a backtest fetch is deliberately marked advisory, so it is the first thing shed. The run then replayed on the data it had: 100 of 178 sessions for the saved setting, and **zero** for the setting that trades most often.

**Two budgets that could not see each other.** The run was told it had 3,998 calls — `backtest_budget`'s own daily counter, which the operator sets. The deployment had already spent about 3,030 of ICICI's shared 5,000 that day, so the real headroom was about 1,470. Nothing connected the two numbers, so the fetch confidently planned for calls that would be refused on arrival. `backtest_budget` is still the operator's knob; it just no longer gets to promise what the day cannot pay.

**Why the first refusal is the whole answer.** `Fetcher._tick()` increments the run's call count and sleeps `CALL_SPACING_SECONDS` *before* the SDK call, so treating a shed as a per-window error cost a second of wall clock and a unit of the run's own budget for each one — 34 minutes and 1,631 units after the answer was already known. Nothing about window 1,632 could differ from window 1: the gate reads a daily counter that only rises. So it raises, and everything already fetched is kept, which is what makes "fetch again after midnight" a resumption rather than a restart.

**`AllowanceSpent` is a sibling of `BudgetExhausted`, not the same thing.** Both are `Stopped` and both end a fetch early, but one is this run's budget and the other is the whole deployment's day, and the remedies are opposite: raise the backtest budget, versus wait for IST midnight (raising the budget then makes things worse, not better). They are reported as separate notes for that reason. Neither is a failed run — a fetch that stops early leaves a gap, and a gap is reported, never dressed up as an error (#36).

**What this does not do.** It does not reserve allowance *for* a backtest, and it does not spread a long fetch across days by itself. A nine-month backfill still needs several days of fetching, and the honest plan is now visible up front instead of being discovered 35 minutes in. It also does not change what counts as advisory: a backtest is decoration next to an order, and that ordering is the point of the reserve, not a limitation of it.
