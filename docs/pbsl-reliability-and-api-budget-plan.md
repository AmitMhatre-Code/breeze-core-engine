# PB/SL reliability and ICICI API budget governance — plan

Status: written 2026-08-03. **All six defects addressed** — see "Implementation status"
at the end. P1-5 shipped in a different (cheaper) form than planned; the reasoning is
recorded there.

Scope: six related defects found while investigating why a Profit Booking leg failed with
"You have been throttled by ICICI" on 2026-08-04, and why 4252 `get_order_list` calls were
consumed on 2026-07-31.

They are related: every one of them is a consequence of the app having no notion of *how
much broker budget it is spending, on whose behalf, and how important the call is*.

---

## Summary of defects

| # | Defect | Severity | Where |
|---|---|---|---|
| 1 | Armed SGs do not evaluate at all after a process restart | **Critical** | `squareoff_dispatcher.hydrate_group_rules_on_startup` |
| 2 | One un-dismissed Reset SG burns ~3600 `get_order_list`/hour | **Critical** | `strategy_group_lifecycle.attach_reset_details` |
| 3 | No rolling per-minute rate awareness; ICICI throttles anyway | High | `icici_api_pacing.GlobalIciciApiPacer` |
| 4 | Failed exit leg is never retried; SG is one-shot | High | `portfolio_pnl_engine._evaluate_rules` |
| 5 | Foreign-order detection is WS-only, no REST backstop | High | `strategy_group_lifecycle._handle_foreign_order` |
| 6 | Position composition goes stale; only warmed by Portfolio page load | Medium | `portfolio_pnl_engine.sync_positions_from_response` |

---

## P0-1 — Armed SGs are inert after a restart

### Evidence

`hydrate_group_rules_on_startup` (`squareoff_dispatcher.py:85-127`) restores three things on
boot: the in-memory rules via `set_group_rule`, the per-rule WS chain pin via
`sg.pin_subscription`, and the account-wide order feed via `ensure_order_feed`.

It does **not** restore positions. `set_group_rule` (`portfolio_pnl_engine.py:201-226`)
writes only to `_group_rules`.

`_legs_by_user` has exactly one writer — `register_positions`
(`portfolio_pnl_engine.py:134-145`), reached only from `sync_positions_from_response`
(`:156-182`), called only from `GET /portfolio/data` (`route_portfolio.py:159`).

`run_pnl_tick` reads `_legs_by_user` and returns early when it is empty
(`portfolio_pnl_engine.py:548-551`). All group-rule evaluation lives in `_evaluate_rules`,
invoked *inside* that per-user loop (`:569`).

**Net effect:** after any restart the UI shows the SG as Armed, the WS chain is hot, the
order feed is live — and the engine evaluates nothing until a human opens the Portfolio
page. Protection looks alive and is not.

This is acute because portal-approved upgrades restart the container mid-session.

### Fix

1. In `hydrate_group_rules_on_startup`, for each distinct `user_id` with a live SG, call
   `processor().get_positions(user_id)` and feed it to `sync_positions_from_response`.
   Cost: one call per such user, once, at boot.
2. Guard it: hydration must not raise if the broker session is absent or expired (mirror
   the existing per-rule `try/except` idiom in that function).
3. Emit a distinct log line and Telegram alert when a live SG exists but positions could
   **not** be warmed — that is the "protection is suspended" state and must not be silent.
4. Add a bounded retry: if the broker session is not available at boot, re-attempt on a
   backoff until it is (a restart often precedes the user's first login of the day).

### Decided — stay inert, with recurring re-login reminders

Each rule persists `legs_snapshot` from arm time (used by `check_armed_drift`,
`strategy_group_lifecycle.py:117-135`), so seeding `_legs_by_user` from it was an option.
**Rejected.** Firing exit orders against composition that changed while we were down is a
worse failure than a disclosed gap. The engine stays inert until a real position fetch
succeeds.

The gap is made safe by making it *loud and recurring*, not by guessing:

5. **Recurring Telegram re-login reminder.** While any SG is `armed`/`triggered`/`fired`
   **and** positions cannot be warmed (no valid broker session), send a Telegram reminder
   **every 30 minutes** until the user logs back in and the warm succeeds.
   - Fire the **first reminder immediately**, not after the first 30-minute interval — the
     protection gap starts at boot, not 30 minutes later.
   - State plainly that automated PB/SL protection is **suspended**, name the affected
     groups, and give the re-login action.
   - **Stop** as soon as a position warm succeeds; send one confirmation that monitoring
     has resumed.
   - **Suppress outside market hours.** A 2am reminder is noise that trains the user to
     ignore the channel. Reuse the existing exchange-calendar / market-hours helper rather
     than a new time check.
   - **Persist the reminder state** (last-sent timestamp per user) so a restart loop cannot
     reset the clock and spam — this defect's own trigger is restarts, so the reminder path
     must be restart-safe itself.
   - Reuse the existing `telegram_alerts` surface; do not add a second notification path.
6. **Do not let Telegram be the only channel.** If the user has never connected the bot,
   every reminder above is a no-op and the suspension is *completely silent* — the worst
   case this defect can produce. Pair the reminder with an in-app banner on the same
   condition, and treat "live SG armed + Telegram not connected" as a state worth nudging
   about at arm time, before it matters.

---

## P0-2 — Reset SG polling burns the daily budget

### Evidence

The chain, per poll tick:

1. `useSquareOffRules` polls `/squareoff-rules` at `usePnlRecomputeRefetchMs`
   (`useSquareOffRules.ts:23`) — default `pnl_recompute_interval_seconds = 2.0`
   (`settings_api.py:179`, `pnl_engine_settings.py:62`).
2. `list_rules` → `_attach_reset_details`, which early-returns **unless** some rule has
   status `reset` (`route_squareoff_rules.py:70-71`). Dormant until the first Reset, then
   permanent — `list_active_rules` keeps `reset` rows until explicitly disarmed
   (`repositories/squareoff_rules.py:90`).
3. `attach_reset_details` loops rules and calls `live_orphans` **per reset rule**
   (`strategy_group_lifecycle.py:475-484`) → `_order_statuses` (`:433`) → `breeze.get_orders`
   (`:402`). N+1.
4. `processor.get_orders` issues **two** `get_order_list` calls — it loops `[NFO, BFO]`
   unconditionally (`processor.py:1233-1241`).
5. No caching anywhere on this path.

**30 polls/min × 2 exchanges × N reset rules = ~3600 `get_order_list`/hour** for a single
reset rule with a Portfolio tab open. The 5000/day cap dies in ~1.4 hours.

The data being fetched is the **hazard/orphan advisory banner** — cosmetic — starving the
budget the exit orders need.

### Confirmed measurement — 2026-07-31

Both `api_usage_daily_by_route` and `api_usage_daily_by_api` count *broker calls* (each
sums to the same 4730 total), grouped by originating route and by API name respectively.

| By route | Calls | | By API | Calls |
|---|---|---|---|---|
| `GET /portfolio/squareoff-rules` | **4236** | | `get_order_list` | **4252** |
| `POST /strategy-builder/propose-trades` | 292 | | `margin_calculator` | 297 |
| `POST /order/break-chunk` | 55 | | `place_order` | 51 |
| `GET /book/data` | 26 | | `cancel_order` | 25 |
| `POST /book/cancel-one` | 25 | | `get_margin` | 24 |
| `GET /portfolio/data` | **19** | | `get_portfolio_positions` | **22** |
| *(all others)* | 77 | | *(all others)* | 59 |
| **Total** | **4730** | | **Total** | **4730** |

**The prediction holds exactly.** `GET /portfolio/squareoff-rules` accounts for **4236 of
4730 calls — 89.6% of the day**, and essentially all of it is `get_order_list` (4252). The
day closed at 4730/5000: **95% of the cap, with 270 calls to spare.**

Working back through the mechanism: 4236 calls ÷ 2 (`NFO` + `BFO` per `get_orders`) ≈ **2118
requests**, at the 2s poll interval ≈ **71 minutes**. So roughly **an hour and ten minutes of
one Portfolio tab sitting open with one Reset rule consumed 90% of the daily budget.**

Two further findings from the same data:

- **`get_portfolio_positions` ran only 22 times all day** (19 via `/portfolio/data`). The
  position registry — the thing P0-1 and P2-6 are about — was warmed **19 times** while the
  rules endpoint was hit ~2118 times. Direct confirmation that composition was stale for
  nearly the entire session.
- **`place_order` ran 51 times.** The genuinely critical traffic is *tiny*. A reserved floor
  of even 500 calls would never once have blocked real trading — which makes the
  cross-cutting reservation both cheap and obviously correct.

Second-largest consumer, `margin_calculator` at 297 via `propose-trades` (6.3%), is
proportionate to real user activity and is **not** part of this defect. Worth a look later
for caching, not now.

### Fix

1. **Short-TTL order-book cache**, keyed `(user_id, window, exchange)`, TTL 10–30s, shared
   by every consumer of `_order_statuses`. At 30s TTL this is a 15× cut on its own.
2. **Batch `attach_reset_details`** — one book fetch for all reset rules, passing the
   existing `status_by_id` parameter down. `reconcile_fired_rules_for_user` already does
   exactly this (`strategy_group_lifecycle.py:271-285`); `live_orphans` never got the same
   treatment. Reuse that shape rather than inventing a second one.
3. **Scope the exchange** — rules carry `exchange_code`; do not blindly query both NFO and
   BFO. Halves everything.
4. **Decouple cadence from the badge** — rule status is a pure DB read needing no broker
   call, so keep it at 2s. Move hazard/orphan detail to on-demand (banner expand) or ≥60s.

Expected combined effect: ~3600/hour → low tens/hour.

---

## P1-3 — No rolling per-minute budget; throttling is only ever reactive

### Evidence

`GlobalIciciApiPacer` (`icici_api_pacing.py:46-99`) stores only `_last_call_mono` — the
timestamp of the last call — and sleeps to enforce a minimum gap. It is a **fixed spacer,
not a token bucket**: it cannot know how many calls happened in the trailing 60s.

ICICI's real quota is ~100 req/min (noted in `portfolio_pnl_engine.py:292-293`, the reason
the P&L loop is WS-driven rather than REST-polled). At the default 0.5s spacing our own
pacer permits up to 120/min — already over.

On a 429/503 the retry loop (`:234-265`) makes 4 attempts with backoff 0.5→1→2s (capped
3.0s, `_MAX_BACKOFF_SEC`/`_MAX_HTTP_ATTEMPTS` at `:12-13`) ≈ 3.5s of sleep, then gives up
and synthesizes the user-facing message via `build_throttle_error` (`:131-154`). ICICI's
cooldown is minute-scale. We fail fast against a slow throttle.

Note the message the user sees is **ours**, not ICICI's raw text.

### Fix

1. **Add a rolling 60s counter** per user, checked *before* dispatch, self-throttling at a
   safe ceiling (~90 of ~100). The daily counter already timestamps every call via
   `record_breeze_call` — reuse that machinery rather than adding a parallel store.
2. **Keep the existing backoff**, but make it call-class aware (see P1-4 and the
   classification section) — reads should still fail fast; exit-order placement should not.
3. Keep the existing `is_daily_limit_reached` pre-check (`:224-225`) — it correctly avoids
   burning the retry loop when the cap is already blown.

---

## P1-4 — A failed exit leg is never retried

### Evidence

`_evaluate_rules` **pops the group rule from the armed registry before dispatch**
(`portfolio_pnl_engine.py:521-527`), deliberately, so a slow listener cannot let the next
tick double-fire. Correct for double-fire safety — but it means a rule is strictly
one-shot. There is no next tick for it.

The leg loop in `squareoff_dispatcher.py:176-233` is sequential and correctly serialized;
a failed leg is recorded in `leg_results` and surfaced via
`notify_squareoff_fired(..., failed=True)` and nothing more. The position stays partially
unwound until a human intervenes.

### Fix — patient, status-gated retry for exit legs only

1. Retry a failed leg on a bounded schedule (every ~5–10s, total ~45–60s) rather than the
   current ~4s fail-fast, so it can outlive a minute-scale ICICI throttle.
2. **Before each attempt, re-read the rule status from the repo** (`repo.get_rule`, a local
   SQLite read, no broker call). If it is no longer `fired` — i.e. the WS feed already
   Reset it because it saw a foreign fill — **abort silently**. This is what makes a long
   retry safe rather than dangerous.
3. **Idempotency**: a timeout does not prove the order did not reach ICICI. Check order
   status / position before re-firing, or the retry can duplicate a leg.
4. **Update the alert copy — in scope for this fix, not a follow-up.** Today it says "check
   the app," which invites the user to go fire the leg manually on ICICI's portal — the
   exact behaviour that creates contra-position risk. Since we can now guarantee we stand
   down when they act, say so: *"Retrying automatically for up to 60s. If you place this leg
   yourself we'll detect it and stop."* The copy change is what converts the retry from a
   silent behaviour into one the user can safely reason about; shipping the retry without it
   leaves the contra-position invitation in place.

### Why the retry is safe

`_handle_foreign_order` (`strategy_group_lifecycle.py:290-303`) already detects the user
trading from anywhere — this app, ICICI web, ICICI mobile — by **order identity, not
channel** — and Resets the rule on a fill via `_reset` (`:306-316`). Step 2 above simply
gates every retry attempt on that existing signal.

Residual risk: a retry firing in the gap between the user's fill and its WS notification
landing. Bounded, and strictly better than either today's no-retry or a naive unguarded
patient retry.

---

## P1-5 — Foreign-order detection has no REST backstop

### Evidence

Primary detection is WS-only: `on_order_notification` → `_handle_foreign_order`, fed from
`ws_tick_pipeline.py:187-209`. Sub-second when the socket is healthy.

The apparent fallback is largely illusory. `check_armed_drift`
(`strategy_group_lifecycle.py:117-135`) does a position diff against
`group_legs_for_user` — the in-memory registry — which per P0-1 is warmed only by a
Portfolio page load, and whose frontend query is `refetchInterval: false`
(`frontend/src/app/portfolio/page.tsx:51`). When the registry is cold it deliberately
returns `None` (`:130-134`, fail-safe silent). An armed SG with no Portfolio tab open gets
no position-diff detection at all.

The order-feed watchdog (`breeze_websocket_manager.py:396-414`) only *re-arms the
subscription*. There is no catch-up step, so fills missed during a silent reconnect are
lost permanently.

Note the asymmetry: a REST backstop was already built for the `fired`→`completed`
transition (`reconcile_fired_rules_for_user`) precisely because WS-only stranded SGs. The
`armed`→foreign-Reset transition never got the equivalent.

### Fix

1. **Periodic order-book reconcile for armed rules**, reusing the P0-2 cache. One cached
   fetch then serves three consumers: orphan detail, fired-reconcile, and foreign-order
   detection.
2. **Order-identity based, not position-diff** — any executed order on a contract in the
   group whose `order_id` is not in `repo.order_ids_for_rule(rule)` is a foreign fill →
   Reset. Identical logic to `_handle_foreign_order`, so no second opinion, and it sidesteps
   registry staleness entirely.
3. **Cadence ~30–60s while any rule is armed.** With caching and exchange scoping this is
   ~375–750 calls/day worst case. Detection goes from *never* (WS deaf) to ≤60s.
4. **Reconcile immediately on watchdog re-arm** — a re-subscribe is the signal we may have
   been deaf. Cheap, high value.

---

## P2-6 — Position composition staleness

Covered structurally by P0-1's fix. Beyond boot, add a periodic composition refresh (~60s)
while any SG is armed. Prices come from WS and are live; it is *composition* that rots.
Classify as critical-tier (below).

---

## Cross-cutting — call classification and a reserved budget floor

This is the piece that stops the next polling regression from starving exits again. Without
it, every leak above must be found and fixed individually, forever.

**Classify every outbound Breeze call as `critical` or `advisory`:**

- **critical** — order placement, exit-leg placement and its retries, armed-rule
  foreign-order reconcile, position composition refresh for an armed SG.
- **advisory** — hazard/orphan banner detail, badge decoration, general page reads,
  history views.

**Reserve a floor of the daily quota for `critical` only.** `api_usage.py` already bands at
`GREEN_MAX = 4000` / `AMBER_MAX = 4500` / `API_CALLS_LIMIT_PER_DAY = 5000` (`:15-17`) —
build on those rather than inventing new thresholds. Above the reserve line, advisory reads
are served stale from cache or refused; critical calls always proceed.

The same split drives shedding under the P1-3 rolling-minute limiter: advisory traffic
sheds first.

Note this cleanly resolves the tension found in P1-5: the *same* endpoint feeds both a
cosmetic banner and a safety-critical check. They deserve opposite treatment under pressure,
which is only expressible once calls are classified.

**Alerting.** `get_usage_warning` (`api_usage.py:333-343`) already warns in the final 1000
band. Escalate: if an SG is armed and projected burn exhausts the quota before market close,
that warrants a loud Telegram alert. Also give the daily-cap case its own alert shape in
`squareoff_dispatcher` — today it collapses into the same generic `failed=True` notification
as a transient throttle, though it means "automated protection is off until midnight IST."

---

## Sequencing

1. **P0-1** hydration — smallest change, largest exposure closed. Ship first.
2. **P0-2** cache + batch + exchange scoping — stops the bleed, and is the prerequisite that
   makes P1-5 affordable.
3. **Cross-cutting** classification + reserved floor — the structural guarantee.
4. **P1-3** rolling-minute limiter — builds on the classification for shedding.
5. **P1-4** patient exit retry — depends on P1-3's call-class-aware backoff.
6. **P1-5** armed-rule reconcile — depends on P0-2's cache.
7. **P2-6** periodic composition refresh.

## Testing notes

- Backend suite: `cd backend && PYTHONPATH=./src .venv/bin/python -m pytest tests/`
- Use `ICICI_BROKER_MODE=mock` throughout — live-mode broker calls only work from the
  production static IP and must never be exercised locally.
- P0-1 needs an explicit restart test: arm an SG, restart the process, assert
  `_legs_by_user` is populated and `run_pnl_tick` evaluates *without* any `/portfolio/data`
  request.
- P0-2 needs a call-count assertion, not just a behavioural one: N reset rules must produce
  exactly one order-book fetch per TTL window, not N per poll.
- P1-4 needs a test that a Reset arriving mid-retry aborts the remaining attempts.

---

## Implementation status

### P0-1 — done (2026-08-03)

| File | Change |
|---|---|
| `app/db/squareoff_protection_migrate.py` | **new** — `squareoff_protection_reminders` table |
| `app/repositories/squareoff_protection.py` | **new** — suspension state accessors |
| `app/services/squareoff_protection_guard.py` | **new** — warm / tick / loop |
| `app/services/telegram_alerts.py` | `notify_protection_suspended` + `notify_protection_resumed` |
| `app/services/squareoff_dispatcher.py` | hydration now warms positions per user |
| `main.py` | migration wired at startup; guard loop started and cancelled with the other lifespan tasks |
| `tests/test_squareoff_protection_guard.py` | **new** — 12 tests |

Decisions made during implementation, beyond the plan as written:

- **One loop, two jobs.** The guard tick both retries the warm after a restart *and*
  refreshes leg composition — they are the same broker call, so P2-6 came along with P0-1
  rather than needing a second loop.
- **Market-open gate does double duty.** `protection_guard_tick` returns immediately while
  the market is closed. That keeps the loop from spending broker budget overnight *and*
  satisfies the "no 2am reminders" requirement with one condition instead of two.
- **Status is checked before syncing, not after.** `sync_positions_from_response` calls
  `clear_positions` for anything it cannot parse as a position list — including error
  payloads. Passing it a failed fetch would *wipe* an already-warm registry, converting a
  transient broker hiccup into the exact inert state this work removes. `warm_positions_for_user`
  therefore rejects non-200 responses before the sync ever sees them. Covered by
  `test_broker_error_does_not_wipe_an_already_warm_registry`.
- **Suspension means "the broker fetch failed", not "the registry is empty".** A user can
  legitimately hold zero open positions for a moment; treating that as suspended would
  alarm them while the session is healthy. Covered by
  `test_warm_succeeds_with_zero_open_positions`.

Budget cost: one `get_positions` per user with a live SG per 60s tick, market hours only —
~375 calls/day, ~7.5% of the 5000 cap. The defect it replaces cost 4236 calls in ~71
minutes.

### P0-2 — done (2026-08-03)

| File | Change |
|---|---|
| `app/services/order_book_cache.py` | **new** — 30s TTL cache, per-user invalidation |
| `app/services/processor.py` | `get_orders(..., exchange_codes=None)` — opt-in scoping |
| `app/services/strategy_group_lifecycle.py` | `_order_statuses` reads through the cache and takes `exchanges`; `live_orphans` accepts `status_by_id`; `attach_reset_details` batches; `on_order_notification` invalidates |
| `app/api/v1/route_squareoff_rules.py` | orphan-cancel invalidates the cache |
| `tests/test_order_book_cache.py` | **new** — 11 tests, asserting *call counts* |
| `tests/conftest.py` | autouse cache clear between tests |

Measured effect, one reset rule with a Portfolio tab open:

| | Before | After |
|---|---|---|
| Broker calls/hour | ~3600 | ~120 |
| Scaling with N reset rules | linear (N+1) | flat — one read serves all |
| Exchanges queried | always 2 | only those the rules use |

**Item 4 of the fix list ("decouple cadence from the badge") turned out to need no
frontend change.** Its purpose was to stop the 2s poll cadence from driving broker cost —
which the cache already does, since poll frequency no longer maps to fetch frequency. The
frontend still polls at 2s for badge freshness, but that is now a pure SQLite read.
Changing it would have cut HTTP requests, not broker calls, and broker calls are the
scarce resource. Left alone deliberately.

Decisions made during implementation:

- **Cache is scoped to the SG surfaces, not bolted onto `processor.get_orders`.** The
  Orders page calls the same method on an explicit user refresh; answering *that* from a
  30s cache would be a worse bug than the one being fixed.
- **The TTL is not the freshness mechanism.** Real order state arrives over the WS feed,
  which invalidates. The TTL only bounds how long a *quiet* book goes un-refetched — which
  is exactly the wasteful traffic. Orphan-cancel invalidates too, because `rearm_blocked`
  is derived from that read and stale-blocking a re-arm is user-visible friction on a
  safety action.
- **Errors are never cached** — pinning a transient broker failure for 30s would make
  every SG surface look broken off one bad call.
- **A scoped read cannot satisfy an unscoped one.** An NFO-only book answering a question
  about BFO orders returns "not found", which callers read as "not live" — the dangerous
  direction. Scoped and unscoped reads use distinct cache keys.

### Cross-cutting classification + reserved floor — done (2026-08-03)

Built first, because P1-3 needs it to decide who sheds and P1-4 needs it for class-aware
backoff.

| File | Change |
|---|---|
| `app/services/icici_call_class.py` | **new** — `critical`/`advisory` ContextVar, `advisory_calls()` / `critical_calls()` scopes |
| `app/services/api_usage.py` | `advisory_budget_exhausted()` — reserve line at the existing `AMBER_MAX` (4500) |
| `app/services/icici_api_pacing.py` | `build_shed_error()`; both gates wired into `request_breeze_http` |
| `app/api/v1/route_squareoff_rules.py` | hazard-banner read wrapped in `advisory_calls()` |

- **Unmarked calls are `critical`.** Shedding is opt-in, so an unaudited call site behaves
  exactly as before. The opposite default would silently start dropping unaudited traffic
  the first time a user neared their cap.
- **A ContextVar, not a parameter,** because enforcement lives in the `requests` transport
  monkeypatch, which cannot see its caller's arguments. `contextvars` propagate across
  `asyncio.to_thread`, so the P&L engine's worker threads inherit the class.
- **The reserve reuses `AMBER_MAX`** rather than inventing a constant — the UI already
  turns amber there and tells the user to save calls for placing orders. This makes that
  advice structural instead of advisory.
- **A shed is not reported as a broker throttle** (`advisory_shed: True`,
  `icici_throttled: False`). Nothing failed and ICICI was never asked; calling it a
  throttle would send users chasing a problem at the broker that does not exist.

### P1-3 — done (2026-08-03)

`GlobalIciciApiPacer` now keeps a trailing-60s deque per user (`_MAX_CALLS_PER_MINUTE = 90`
against ICICI's ~100, leaving headroom for the same broker account being used from ICICI's
own web/mobile app, which spends the identical quota and is invisible here).

`wait_for_minute_slot` reserves a slot before dispatch:
- **advisory** — refused immediately when the window is full. Queueing them would hand
  scarce slots to whoever arrived first, which is the random-loser behaviour the
  classification exists to end.
- **critical** — waits for the oldest call to age out, up to `_MAX_SLOT_WAIT_SEC` (20s),
  then proceeds anyway. Refusing to place an exit order because of our own bookkeeping
  would be worse than risking a throttle we can now retry through.

Retry attempts inside `_attempt_loop` are counted too — each is a real request, and a
throttle storm would otherwise be invisible to the window meant to prevent it.

### P1-4 — done (2026-08-03)

`_place_chunk_with_retry` in `squareoff_dispatcher.py`, delays `(5, 10, 15, 20)` ≈ 50s
against ICICI's minute-scale cooldown — versus the transport layer's ~4s, an order of
magnitude short.

Two invariants make a long retry safe rather than more dangerous:

1. **Only an explicit throttle is retried.** A throttle is a *refusal* — ICICI states it
   did not accept the order, so re-sending cannot duplicate it. A timeout or transport
   error carries no such guarantee and is surfaced, never retried. This is why the retry
   needs no order-book round-trip to stay idempotent. Daily-limit exhaustion is also not
   retried: it will not clear before midnight IST.
2. **`_still_firing()` re-reads the rule's status before every retry.** Anything other
   than `triggered` — most importantly a Reset from `_handle_foreign_order` when the WS
   feed sees the user's own fill from ICICI web or mobile — aborts the retry. The user can
   act during the wait and we stand down instead of racing them into a contra position.

**Alert copy (item 4) shipped with it, not after.** A new `notify_squareoff_retrying`
fires at the *first* retry, not after the last — the point is to reach the user during the
wait, since a user who sees no exit appear and has been told nothing will reasonably go
place it on ICICI's app. It states we will stand down if they act, which the code actually
honours. The final failure message no longer says "check the app"; it names the risk that
a partially-filled exit has already changed the position.

### P1-5 — done (2026-08-03), but NOT as planned

**The plan called for a periodic order-book foreign-fill reconcile. That would now be
largely redundant, and it was not built.** Fixing P0-1 changed the premise.

The plan asserted foreign-order detection was WS-only, because `check_armed_drift` reads
the position registry and the registry was only ever warmed by a Portfolio page load. The
guard loop from P0-1 now refreshes it every 60s — which turns the drift check into a
genuine REST-based fallback, independent of WS, that did not previously function.

Measured before building anything (throwaway probe, since removed):

| Foreign fill while armed | Detected? |
|---|---|
| Partial — quantity or leg set changed | **Yes**, via drift, ~60s. Newly working. |
| Full — every leg closed manually | **No.** Rule stays `armed` indefinitely. |
| Round-trip with net-zero composition change | No — and correctly so: the thresholds still describe the position that is actually held. |

So only the *empty* case was still open, and its cause is structural: `_evaluate_rules`
skips a group rule when no tracked leg matches (`if not matching: continue`), and
`run_pnl_tick` only iterates users present in the position registry at all — a user who
closes everything drops out of the registry and their SG is never evaluated again. It sits
armed forever: the user believes they are protected on a position they no longer hold, and
the one-live-SG-per-key index blocks them re-arming that group.

`reconcile_fully_closed_groups` in `squareoff_protection_guard.py` closes exactly that,
riding the warm the guard already performs — **zero additional broker calls**, versus the
~375/day a separate order-book reconcile would have cost to re-detect what drift already
catches.

Guards, each with a test:
- **Only after a successful warm.** "No legs" and "we could not read your positions" are
  the same observation from here; acting on the second would tear down live protection
  over a broker hiccup.
- **`armed` only.** A `triggered` rule is mid-placement with its own orders not yet
  recorded against it; a `fired` rule's own exits legitimately empty the group, which is
  what `reconcile_fired_rules_for_user` resolves into Completed.
- **Requires a non-empty `legs_snapshot`**, or nothing can be concluded.

`tests/test_squareoff_foreign_close.py` also pins the *partial* case, which passes only
because of P0-1's refresh — if that refresh is ever removed, the test fails and names the
reason instead of the fallback silently going dead again.

### Second incident — 2026-08-03 (same bug, one gap it exposed)

The quota was exhausted again the day this work was written: 5000/5000, with
`GET /portfolio/squareoff-rules` = **4749** and `get_order_list` = **4765**. Same shape as
2026-07-31, which independently re-confirms the diagnosis.

**The fix above was not running.** All of it was uncommitted in the working tree; the
deployed image was still at `34196b6c`. Nothing here had reached production.

The scenario was instructive though: a single `reset` rule left over from the previous
**Friday**, no new rules armed, ~4749 calls in about 80 minutes of the Portfolio tab being
open (4749 ÷ 2 exchanges ÷ 0.5 polls/s ≈ 79 min). Taking new positions was incidental —
what mattered was a stale `reset` rule existing at all, since `list_active_rules` keeps
those until explicitly disarmed and `_attach_reset_details` fires on every 2s poll while
one is present.

P0-2 as built would have bounded this to ~240 calls (cache TTL × 1 exchange). Better by
20×, but still 240 calls spent learning nothing — because those exit orders were placed
with `validity="day"` (`processor.place_order`) and had expired at Friday's close.

`orders_could_still_be_live()` now short-circuits them to **zero** broker calls.

This is behaviour-preserving, not a new judgement: `live_orphans` reads *today's* window,
which never contained Friday's order_ids, so those rules already resolved to "no orphans,
not rearm-blocked". The short-circuit just stops paying for the round-trip that
rediscovers it. A missing or unparseable `fired_at` still falls through to the read —
reporting "settled" when we cannot tell would clear `rearm_blocked` and let a new SG stack
on top of a live order.

### Still open

Nothing from this plan. Two follow-ups noted along the way, neither urgent:
- `margin_calculator` (297 calls on 2026-07-31 via `propose-trades`) is proportionate to
  real use but is now the largest remaining consumer — a caching candidate.
- `tests/test_changelog_latest_version.py` asserts `2.1.2` against a changelog at `2.4.0`.
  Pre-existing, unrelated to this work, and the only failing test in the suite.
