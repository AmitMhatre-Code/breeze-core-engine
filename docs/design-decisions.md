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

**Trade-off**: More `margin_calculator` calls per build — roughly 2 standalone-only calls per unique structure becomes 3 (no meaningful overlap with the book, the common case) or 4 (real overlap, needing the secant), plus one shared `M(existing)` call per build that's usually served from the 24h Redis cache `_netted_span_for_legs` already maintains for the Portfolio page. Builds against a scrip where the user holds a large existing book will be noticeably slower.

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
