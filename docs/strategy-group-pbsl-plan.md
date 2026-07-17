# Strategy Group (SG) PB/SL — Implementation Plan

Status: **implemented** (backend + frontend + tokens), with one caveat that has not
changed and cannot be closed locally — see "Risks / open items". Supersedes the ad-hoc
`(stock_code, expiry_display)` rule matching in `portfolio_squareoff_rules` /
`portfolio_pnl_engine`.

**As-built map** (where each section landed):

| Plan | Code |
|---|---|
| Payload contract (§3) | `app/services/order_notifications.py` + `tests/fixtures/order_notifications.py` (the three real captures, verbatim) |
| Self-fill vs manual (§4) | `app/services/strategy_group_lifecycle.py` |
| Schema + one-active-SG invariant (5.1) | `app/db/squareoff_rules_migrate.py` |
| Tick routing (5.3) | `ws_tick_pipeline._route_order_notification` |
| Order-notification subscribe (5.4) | `breeze_websocket_manager._subscribe_order_notifications` |
| Arm precondition / re-arm guard (5.7) | `app/services/strategy_group_arm_guard.py` |
| WS pinning (5.8) | `strategy_group_lifecycle.pin_subscription`, re-pinned by `squareoff_dispatcher.hydrate_group_rules_on_startup` |
| Orders association (5.9) | `squareoff_rules.rules_owning_orders` + `route_book` |
| Reset semantics (5.12) | `repo.mark_reset` (never cancels), `route_squareoff_rules.cancel_orphan_orders` (user-initiated only) |
| Warning UX (5.13) | `lib/portfolio/reset-warning.ts` (single source of copy/tiering), `ContraOrphanBanner`, `SquareOffRuleModal`, `OpenPositionsTable`, `orders/page.tsx` |

Tests: `test_order_notifications.py`, `test_squareoff_sg_migration.py`,
`test_strategy_group_lifecycle.py`, `test_squareoff_rules.py`, `reset-warning.test.ts`.

Requirements source: the Strategy Group specification agreed 17-Jul-2026 (§ refs below).
Payload facts verified against real ICICI order-notification captures (`docs/Temp/order_placed`,
`order_modified`, `order_cancelled`) and `breeze_connect==1.0.65` source.

---

## 1. Root cause of the reported bugs

Both reported bugs trace to one defect: **nothing in the system records which legs or which
orders a PB/SL rule owns.** Every association is re-derived from `(stock_code, expiry_display)`:

| Site | Code | Consequence |
|---|---|---|
| Arm-time upsert | `repositories/squareoff_rules.arm_rule` | Arming a 2nd/3rd strategy on the same scrip+expiry **silently UPDATEs the same row**. Only one rule ever existed → "PB/SL card only showed the last one" (bug #2). |
| Rule registry | `portfolio_pnl_engine._group_key` | Key is `f"{stock}|{expiry}"`, no leg identity. |
| Trigger evaluation | `portfolio_pnl_engine._evaluate_rules` | Sums P&L over *every currently-open leg* sharing stock+expiry — including legs from unrelated strategies → commingled exit orders (bug #2). |
| Live-leg join | `portfolio_pnl_engine.group_legs_for_user` | Same key → Orders page shows the wrong legs. |
| Portfolio badge | `OpenPositionsTable.ExitRuleBadge` | Looks up by group key → a new strategy inherits a stale rule's status (bug #1). |

ICICI's position feed has no strategy concept — once filled, a position is just
(stock, strike, right, qty). This app is the only place strategy identity can exist, and today
it does not.

**The spec resolves this not by adding leg-level rules, but by making the Strategy Group itself a
first-class, single-instance entity with an explicit lifecycle.** Under the spec, the 3 strategies
in bug #2 *are* one SG; the fix is that adding legs to an Armed SG triggers a visible **Reset**
(§9) rather than a silent overwrite.

---

## 2. Model

**Strategy Group (SG)** = all open legs for `(user_id, exchange_code, stock_code, expiry_display)`.

Critical distinction the current code lacks:

- The **group key** `(scrip, expiry)` is *reusable over time*.
- An **SG** is a specific instance with a lifecycle. Many SGs share a key across history; exactly
  **one is non-terminal at a time** (§1, §11).

`Active` is **derived, not persisted** — an SG with open legs and no rule row. A row exists only
from Armed onward. This satisfies §11 (a new SG after Completed is automatically independent —
there is nothing to reactivate) with no extra state.

### States

| Spec state | DB `status` | Notes |
|---|---|---|
| Active | *(no row)* | Derived from open positions. |
| Armed | `armed` | Monitoring. |
| — | `triggered` | **Internal transient only** (sub-second, guards double-fire). Renders as Armed. |
| Fired | `fired` | Exit orders placed. **Patient waiting state** (see §4 below). |
| Completed | `completed` | New. |
| Reset | `reset` | New. Absorbs today's `fire_failed`. Carries `reset_reason`. **Terminal, but may still have live orphaned exit orders — see 5.12.** |
| — | `disarmed` | Explicit user disarm → SG returns to (derived) Active. Not a spec state. |

---

## 3. Order-notification payload — verified facts

### Delivery
`subscribe_feeds(get_order_notification=True)` opens a **separate** socket.io client
(`sio_order_refresh_handler` → `config.LIVE_FEEDS_URL`) from the price-tick client
(`LIVE_STREAM_URL`), but `SocketEventBreeze.on_message` (breeze_connect.py:96-101) dispatches to
**`breeze.on_ticks`** — the same callback `breeze_websocket_manager.py:186` already points at
`ws_tick_pipeline.ingest_tick`.

→ **Order events land in `ingest_tick` and must be discriminated there.**
Discriminator: presence of `orderReference` (price ticks never carry it).

### Two schemas by `messageType`
- `4`/`5` — cash/equity shape (has `openQuantity`). **Out of scope**, ignore.
- `6`/`7` — F&O shape. All captures are `"6"`. **No `openQuantity` field.**
  Derive: `pending = orderTotalQuantity − executedQuantity − cancelledQuantity − expiredQuantity`.

### Scaling — uniform ×100
**Every price field is the rupee amount to 2dp with the decimal point stripped.** Verified:
user set limit ₹3.00 → `limitRate: "300"`; modified to ₹2.80 → `"280"`; strike 26000 →
`strikePrice: "2600000"`. Divide by 100, no exceptions.

### `orderStatus` enum (decoded from `config.TUX_TO_USER_MAP`)

| Class | Values |
|---|---|
| Non-terminal | `Requested`, `Queued`, `Ordered`, `Partially Executed`, `Freezed` |
| Terminal — success | `Executed` |
| Terminal — failure | `Cancelled`, `Rejected`, `Expired`, `Partially Executed And Cancelled`, `Partially Executed And Expired` |

(`All` is a filter value, never a live status.)

### A modify emits no distinct event
The modified tick is another `orderStatus: "Ordered"`, same `messageType`, same `orderReference`
— only `limitRate` changed. Placed vs modified are indistinguishable except by diffing prior
state. Order events by `messageSequence` (monotonic: `…788212` → `…796231` → `…802257`).

### Identity
`orderReference` (`202607173800017846` = `YYYYMMDD` + `38`(=`pipeId`) + 8-digit seq) is
**confirmed identical** to REST `place_order`'s `Success.order_id` and the order book's
`order_id`. Stable across the order's whole lifecycle. Matches the existing real-order-id
fixture in `tests/test_modify_order_single.py:27`.

`userId` (`"VIKRAMMH"`) **is** the app's internal `user_id` (`auth/context.py:65`:
"user_id = ICICI for API calls").

### Fields that lie — MUST NOT be used

| Field | Evidence |
|---|---|
| `averageExecutedRate` | `"1550900.000000"` on the *placed* tick with `executedQuantity: "0"`. Meaningless. Never compute realized P&L from notifications. |
| `stopLossOrderReference` | SDK bug — assigned `data[37]`, the **same index** as `acknowledgeNumber` (breeze_connect.py:697-698). Both captures show identical values. Not a real reference. |
| `squareOffMarket`, `quickExitFlag` | Flipped `N`→`Y`→`N` across a plain price modify. **`squareOffMarket` must NOT be used to identify square-off orders** despite the name. |
| `totalAmountBlocked` | `"*"`. |
| `channel` | Not a reliable manual-vs-app discriminator. Use `orderReference` matching. |

---

## 4. The §6 / §9-§10 collision and its resolution

When a Fired SG's own exit orders fill, **positions change**. Read literally, §9 ("removal of
existing legs → Reset") would reset the SG at the exact moment it should reach Completed.

**Resolution (confirmed):** the discriminator is order identity.

- Execution tick whose `orderReference` **∈** the SG's stored `order_ids` → *our* exit working as
  intended → drives Fired → Completed. **Never** Reset.
- Execution tick for the SG's scrip+expiry whose `orderReference` **∉** our set → manual
  intervention → **Reset** (§10).

This is why `orderReference == order_id` is load-bearing: without it there is no principled way
to separate "our exit filled" from "user squared off manually".

**Fired is a patient waiting state** (per the §8 amendment dropping "otherwise remains
unexecuted"): an exit order resting unfilled (`Ordered`/`Queued`/`Partially Executed`) keeps the
SG Fired indefinitely. This self-resolves at EOD — an unfilled Day order goes `Expired`, which is
on the §8 Reset list.

---

## 5. Changes

### 5.1 Schema — `portfolio_squareoff_rules` (migration in `db/squareoff_rules_migrate.py`)

- Extend `status` with `completed`, `reset`. Migrate `fire_failed` → `reset` + `reset_reason`.
- **`reset_reason TEXT NULL`** — user-facing warning (§8/§9/§10 all require explaining why
  monitoring stopped).
- **`legs_snapshot TEXT NULL`** — JSON `{scrip_key: quantity}` captured at arm time. Powers §9
  drift detection. Quantity-sensitive (any qty change invalidates).
- **`resolved_at TEXT NULL`** — Completed/Reset timestamp.
- **Partial unique index** enforcing §1 at the DB level:
  ```sql
  CREATE UNIQUE INDEX ux_sg_one_active ON portfolio_squareoff_rules
    (user_id, stock_code, expiry_display) WHERE status IN ('armed','triggered','fired');
  ```
  This makes "one active SG per scrip+expiry" an invariant rather than a convention — it
  structurally forecloses the bug #2 class.

Table name kept (`portfolio_squareoff_rules`) to avoid a repo-wide rename; SG == one row.

**Back-compat:** existing `armed` rows have no `legs_snapshot`. Backfill from current live
positions at migration time and log. Tradeoff: forgives any drift that occurred before deploy,
but preserves live protection — preferable to resetting real armed rules on upgrade.

### 5.2 New — `app/services/order_notifications.py`

`parse_order_notification(raw) -> OrderNotification | None`:
- Discriminate on `orderReference`; accept `messageType` 6/7 only.
- Normalize: `order_id=orderReference`, `status=orderStatus`, `stock_code`,
  `exchange_code=orderExchangeCode`, `expiry_display=expiryDate` (already DD-Mon-YYYY),
  `strike=int(strikePrice)/100`, `right=optionType`, `action=orderFlow`,
  `limit_price=int(limitRate)/100`, qty fields + derived `pending`, `sequence=messageSequence`,
  `user_id=userId`.
- Explicitly drop the lying fields (section 3), with a comment citing why.

> Note on § refs in this doc: `§N` always means the **Strategy Group spec**; bare `section N` /
> `N.M` means a section of this plan.

### 5.3 `ws_tick_pipeline.ingest_tick`

Branch **early**: if `orderReference` present → route to the SG lifecycle handler and return;
do not enqueue as a price tick or notify raw quote listeners.

### 5.4 `breeze_websocket_manager`

- After `ws_connect()`, call `sdk.subscribe_feeds(get_order_notification=True)`.
- **Re-subscribe on reconnect** (`_ws_connect` guards `if self.orderconnect == 0`; a dropped
  connection must re-arm the subscription).
- Guard for mock mode.

### 5.5 New — `app/services/strategy_group_lifecycle.py`

`on_order_notification(n)`:
1. Find the non-terminal SG for `(user_id, stock_code, expiry_display)`. None → ignore.
2. `n.order_id ∈ sg.order_ids` (ours):
   - `Executed` → if **all** `sg.order_ids` Executed **and** no open legs remain → `completed`.
   - Terminal-failure → `reset`, reason names the residual open quantity where applicable.
   - Non-terminal → stay `fired`.
3. Not ours:
   - `Executed` / `Partially Executed` (i.e. it moved positions) and sg ∈ {armed, triggered,
     fired} → `reset` (manual intervention).
   - Otherwise ignore — a *resting* manual order changes no composition until it fills.

`reconcile(user_id)` — the backstop, invoked on Portfolio/Orders fetch (page load / tab focus,
per the agreed "event-driven + refresh on tab switch" model). Compares SG state against live
positions **and** the REST order book to catch anything a dropped WS event missed. While `fired`,
reconciliation must use the **order book** (any executed order for the scrip+expiry not in
`sg.order_ids` → manual → reset), *not* a position diff — a position diff cannot attribute a
fill.

### 5.6 Drift detection (§9)

- While `armed`/`triggered`: compare live open-leg `{scrip_key: qty}` against `legs_snapshot` on
  the P&L tick and on reconcile. **Any** diff, including quantity → `reset`.
- While `fired`: **do not** snapshot-diff (our own exits legitimately change legs). Rely solely
  on `orderReference` discrimination (section 4).

### 5.7 Arm precondition (spec §4) — doubles as the re-arm / duplicate-fire guard

New check in the arm route: reject if **any** non-terminal order exists for the scrip+expiry
(status ∈ {Requested, Queued, Ordered, Partially Executed, Freezed}). Costs one REST call at arm
time — acceptable, user-initiated, once.

This precondition was written for *entry* orders, but it is load-bearing for a second reason: it
is **the only thing preventing duplicate exit orders after a Reset.** A Reset does not cancel the
SG's already-placed exit orders (see 5.12) — they can still be resting at the exchange. Without
this check, re-arming and re-firing would stack a *second* exit order on top of each live one,
double-exiting a leg and potentially flipping the position net-contra.

Traced across every Reset path (manual cancel of one leg, broker rejection, spec §10
intervention, partial fills — `Partially Executed` is non-terminal, so it blocks too), the guard
holds. Two properties make it reliable rather than accidental:

- **REST-authoritative, never WS-derived.** A dropped notification must not be able to make an
  orphaned order look terminal. Always hit the order book.
- **The rejection message must name the real cause** — "2 exit orders from a previous PB/SL
  attempt are still live; wait for them to fill/expire, or cancel them" — not the generic "all
  legs must first be executed".

### 5.8 WS subscription pinning — **new; nothing does this today**

Verified: the only `register_holder_chain` caller is `breeze_websocket_manager.py:450`, reached
solely from `route_strategy_builder`'s chain endpoint with a **browser-supplied**
`subscription_holder`. So today, closing the tab lets the chain go cold → quotes stale → **PB/SL
silently stops protecting**. `hydrate_group_rules_on_startup` re-arms rules in memory but pins
nothing, so a restarted instance has armed rules that cannot fire.

- Arming pins a server-side holder `holder_id = f"sg:{rule_id}"` for the SG's chain.
- Released on `completed` / `reset` / `disarmed`.
- Re-pinned by `hydrate_group_rules_on_startup`.

This is a latent safety bug independent of the two reported; it is in scope because headless
operation was agreed as required.

### 5.9 Order association / Orders page (spec §12)

- `exit_rule_orders.split_rule_spawned_orders` already matches by exact `order_id` — keep.
- **`reset` SGs must NOT participate in the exclusion set** — spec §12 requires their orders
  revert to displaying as independent individual orders in the main Order Book.
- **`completed` SGs DO stay grouped** — spec §12 requires the completed PB/SL group and its
  orders remain visible in History.

**"Association removed" is a display rule, not a data rule.** The `reset` row keeps its
`order_ids` in the DB — that is what lets the History card offer the bulk-cancel action (5.10),
what makes the re-arm guard (5.7) explicable to the user, and what preserves the audit trail —
while the orders themselves render in the main Order Book. This dissolves what otherwise looks
like a three-way contradiction between audit retention, Orders-page grouping, and the ability to
act on orphans.

### 5.10 Frontend

- Portfolio: one badge per SG (Armed / Fired / Completed / Reset). Reset surfaces `reset_reason`
  as a warning. `useSquareOffRulesByGroup` returns the single non-terminal SG (the invariant now
  guarantees at most one) plus history separately.
- Orders page: SG rows include Completed and Reset in History; Reset rows show the warning.
- **Reset SG card** additionally shows the derived orphan state (5.12): "N exit orders from this
  attempt are still live — re-arm blocked", plus a **"Cancel remaining exit orders"** action.
  Both are computed from the order book, not stored. Full warning design incl. hazard tiers,
  copy, surfaces and dismissal rules: **5.13**.

### 5.11 Mock broker

Add an order-notification simulation hook so the state machine is testable locally — required,
since live-mode broker calls only work from the production static IP.

### 5.12 Reset semantics — orphaned orders

> **Principle: Reset withdraws future automation; it does not retract actions already taken.**

A Reset does **not** cancel the SG's still-live exit orders. The tempting alternative
(auto-cancel on Reset) was rejected on one case that settles it: the SG fired on a **stop loss**,
the market is running against the user, one leg's order is rejected → Reset → auto-cancel would
kill the other three *working* exit orders, leaving the user unprotected in a moving market
because an unrelated leg failed. Those orders are the user's own configured intent, already in
flight; cancelling them is an unrequested mutation that can itself fail (rate limit,
already-filling) and in the stop-loss case is actively harmful.

What this obliges us to build:

- **The Reset warning must state plainly that live orders remain and may still execute**, naming
  how many and which legs. Without this the warning actively misleads: "monitoring stopped"
  reads as *"nothing automated will happen to my positions"*, yet orphans still fill.
- **Sharper hazard to surface:** if the user manually closed a leg in the interim, that leg's
  orphaned exit order no longer closes anything — **if it fills, it opens a new contra
  position.** A resting order that can put on unrequested risk must be visible.
- Orphans render in the main Order Book as ordinary live orders (5.9), individually cancellable.
- An **explicit, user-initiated "cancel remaining exit orders"** action on the Reset SG card —
  the escape hatch when the user doesn't want them. Never automatic.
- **Re-arming after Reset creates a NEW SG row** (spec §11: historical SGs are never
  reactivated). The partial unique index (5.1) permits it, since `reset` is not in the
  non-terminal set.
- **Re-armability is derived, never stored.** A Reset SG whose orphans are all terminal is
  re-armable; one with live orphans is not (blocked by 5.7). Compute from the order book and show
  it on the card so the block is legible *before* the user attempts it. No sixth state, nothing
  to drift out of sync with the broker.

### 5.13 Reset warning UX

The Reset warning carries five facts (stopped / why / N orders live / one may open a contra
position / what to do) across four surfaces whose budgets range from a ~10-character chip to a
full modal. One message cannot say all of it without becoming a paragraph in which the sentence
that matters most gets skimmed past.

#### Hazard tiers — derived server-side, never stored

| Tier | Condition | Chip | Escalation |
|---|---|---|---|
| **1 — Settled** | No live orphans (all terminal) | `Reset` (**neutral**) | Card only |
| **2 — Still working** | Live orphans that still correctly close open legs | `Reset · N live` (amber) | Card only |
| **3 — Contra risk** | ≥1 orphan whose leg is already closed, or reduced below the order's qty | `Reset · action needed` (red) | **App-level banner** |

Tier 1 is **neutral, not amber**: nothing is at stake, and colouring a benign reset amber trains the
user to discount tiers 2 and 3. The gradient is neutral → amber → red.

**Visual mock:** `docs/ui-revamp/mock-pbsl-reset-warning.html` — standalone (no `support.js`
runtime), all four surfaces, both themes, verified against rendered pixels.

Tier-3 detection: for each live orphan, resolve the position it was placed to close (`scrip_key`).
If no open position remains — or remaining qty < order qty — filling would **open or flip** a
position. (The qty case is real: a partially-reduced leg means only the *excess* goes contra.)

**Derived ≠ frontend-computed.** The backend owns tier computation — the order book and the
position registry already live there — and exposes it as a derived field on the SG record per
request. The frontend renders; it never joins order book against positions itself. This also
keeps the app-level banner (below) to one light poll rather than a global data join.

#### Copy

Convention inherited from `lib/deployment-license.ts` — **state — consequence. action.** Name the
leg, and name what *the user* (or the broker) did, in their words. Never dump raw broker error
text unwrapped; for genuine broker errors the ICICI text stays (it is diagnostic) but wrapped in
human framing.

| Tier | Message |
|---|---|
| 1 | "Profit Booking / Stop Loss stopped — you added NIFTY 25800 PE to this group. No exit orders are outstanding. Set it again to resume." |
| 2 | "Profit Booking / Stop Loss stopped — the exit order for NIFTY 26000 CE was rejected by the broker. 2 exit orders are still live and may still execute. You can't set a new rule until they fill, expire, or you cancel them." |
| 3 | "Profit Booking / Stop Loss stopped — you closed NIFTY 26000 CE manually. **Its exit order is still live: if it fills, it will open a new Buy position.** Cancel it, or let it fill and accept the position." |

Tier 3 is not a louder tier 2 — different verb, different stake. Tier 2 says *"your exit may still
complete"*; tier 3 says *"you may accidentally open a trade."*

#### Surfaces

- **Portfolio group badge — must stop being tooltip-only.** Today the failure reason lives only in
  a `title` attribute (`OpenPositionsTable.tsx:384-390`): invisible on touch, unannounced by
  screen readers, trivially missed. A tooltip is not an acceptable carrier for a message about
  unrequested risk. Reason becomes visible text, matching the Orders page's existing treatment
  (`app/orders/page.tsx:2470-2474`).
- **`SquareOffRuleModal`** — full message + "Cancel remaining exit orders" action.
- **Orders page SG row + mobile card** — existing visible-text pattern, extended with tier + action.
- **App-level banner — tier 3 only.** Reuse the `LicenseStatusBanner` / `ApiLimitExhaustedBanner`
  shape (`role="status"`, red, `border-b`), linking to the SG. A resting order about to open an
  unrequested position is at least as urgent as "API limit hit", which already earns a banner.
  **Deliberately not used for tier 2** — tier 2 is common enough that a persistent banner would
  train the user to ignore it, blunting tier 3.
  - Consider `role="alert"` (assertive) rather than the house `role="status"` (polite) for tier 3
    — a deliberate deviation, since this one is a live financial risk rather than a status note.

#### Telegram — all Resets

Add `notify_squareoff_reset` alongside the existing `notify_squareoff_fired`
(`telegram_alerts.py:63`). **Not optional.** Once 5.8 makes PB/SL headless, a Reset can happen
with nobody watching — and that is exactly when it is most likely (EOD expiry, an overnight-
adjacent broker rejection, a manual trade from the ICICI app). In that window an in-app warning
is decorative. Tier 3 messages carry the explicit contra-position sentence.

#### Dismissal

`disarm_rule` currently allows dismissing `fired`/`fire_failed`. A Reset card is **not dismissable
while any orphan is live** — the same derived condition that gates re-arming (5.7). Dismissal
would remove the hazard from the UI while the orders keep working; **the UI must not be able to
lie about live risk.** Tier 1 is dismissable.

#### Prerequisite: `-on-tint` colour tokens (DESIGN_LANGUAGE §2.4)

Building the mock surfaced that **every light-theme base/tint pair failed WCAG AA** — including the
danger note carrying tier 3, the most safety-critical string in this feature (`--down` on
`--down-tint` = 4.05:1, below the 4.5:1 small-text floor). This was **not** specific to this
feature: `bg-down-tint text-down` already ships today (`app/orders/page.tsx:372`), so every existing
danger chip is affected. Dark theme passes on every pair.

Fixed at the source rather than worked around here — `DESIGN_LANGUAGE.md` §2.4 now defines
`--{up,down,amber,accent,gtt}-on-tint`, amends §5.8/§5.9 and the Tailwind map, and switches the
neutral badge from `--faint` (2.48:1 light / 3.15:1 dark — failed both) to `--muted`. Deliberately
**not** a global darkening of `--down`/`--amber`: those pass as bare text on `--panel` (4.63:1 /
5.02:1), so darkening them would recolour every P&L figure to fix a problem those call sites don't
have.

Implementation must add these tokens to `globals.css` + the Tailwind theme before the tier chips are
built. `-ink` (text on a solid **fill**) and `-on-tint` (text on a **wash**) are different tokens —
see the naming note in §2.4.

---

## 6. Tests

- Parser: fixtures built from the three real captures (`docs/Temp/order_*` → `tests/fixtures/`),
  incl. the ×100 scaling and the lying fields.
- State machine: every transition, especially self-fill vs manual, and both partial-terminals.
- `ux_sg_one_active` invariant.
- Drift detection incl. quantity-only change.
- **Re-arm guard (5.7):** re-arm blocked while a prior fire's exit orders are still resting;
  allowed once they are all terminal. The regression this prevents (duplicate stacked exit
  orders → net-contra position) is the highest-consequence bug in this design — test it directly,
  including the `Partially Executed` case.
- Reset never cancels orphans (5.12); the explicit bulk-cancel action does.
- Re-arm after Reset creates a new SG row, not a reactivation.
- **Hazard tiering (5.13):** tier 1/2/3 classification, especially the tier-3 boundary — leg fully
  closed, and the partial case (remaining qty < order qty → only the excess goes contra).
- Reset card is not dismissable while orphans are live; tier 1 is.

---

## 7. Deviations from the plan, decided while building

- **`--down-ink`/`--amber-ink` was not built as specified.** Two problems surfaced:
  `-ink` already means *text on a solid fill* (`--accent-ink` is white on the teal
  button), so reusing it for *text on a wash* would have made one suffix mean two
  contradictory things; and the audit was under-scoped — `--up` (3.11:1) and `--accent`
  (3.25:1) fail *worse* than the two we'd spotted. Shipped as `--{hue}-on-tint` instead
  (DESIGN_LANGUAGE §2.4), covering all five hues.
- **`--accent-on-tint` deliberately equals `--accent-strong`**, not a new darker value.
  Every accent-tint call site already uses `text-accent-strong`, which clears AA at
  4.73:1; inventing a darker accent would have shifted pixels for zero accessibility gain.
- **The neutral badge stayed on `--faint`.** The doc's `--faint` values were stale —
  `globals.css` had already been retuned for AA (5.03:1 light / 4.99:1 dark) and passes.
  The doc was synced to the app, not the reverse.
- **Tier 1 is neutral, not amber.** Nothing is at stake; amber would cry wolf and blunt
  tiers 2 and 3.
- **`groupEffectiveStatus` no longer derives "exited" from orders.** The frontend cannot
  tell our own exit filling from a manual square-off — only order identity can, and that
  lives server-side. The backend's status is now authoritative for Group rules; `exited`
  remains a Leg·GTT-only derivation (GTT has no server-side lifecycle).
- **The migration resolves pre-existing conflicts before creating the unique index.** Old
  data can legitimately hold a `fired` row plus a newer `armed` row for one key;
  `CREATE UNIQUE INDEX` would have failed outright on upgrade.

## 8. Risks / open items

- **Cannot verify live locally** (ICICI static-IP constraint). Parser is built against real
  captures + SDK source; the state machine is testable via the mock hook. **Needs a production
  verification pass** before being trusted.
- `Freezed` semantics unconfirmed — treated as non-terminal.
- Cash-schema (`messageType` 4/5) ignored — this is an options-only feature.
- **Single-user assumption** (agreed): the order-notification feed rides the WS singleton bound
  to one `_sdk_user_id`. A second user arming an SG would not get fill tracking — **fail
  loudly/visibly** rather than silently under-function.
- Bug #1's literal "Armed" label implies the stale rule's DB status was `armed`/`triggered`, not
  `fired`. The SG model makes this moot (a new SG cannot inherit a prior SG's row), but if a rule
  was stranded at `triggered` — e.g. an exception between `mark_triggered` and
  `mark_fired`/`mark_fire_failed` in `squareoff_dispatcher._handle_group_rule_hit` — that
  stranding path should be closed defensively in the same pass.
