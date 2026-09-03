# Bots — MVP design

Status: **MVP built, then reworked.** Both bots and the section are implemented and tested.
The original decisions were settled 2026-08-31; **section 9 records the 2026-09-02 rework,
which reverses several of them.** Where the two disagree, section 9 wins.
Two bots ship today; the section is built to hold more.

This document records *decisions and their reasons*. Route shapes, schemas, and file
layout are deliberately left to implementation except where a decision constrains them.

---

## 0. Terminology correction (read this first)

The original framing was "a bot to leverage stock holdings to sell **covered PEs**". That
does not hold up under Indian F&O mechanics, and the MVP is scoped around the correction:

* **Stock options are physically settled.** A short PE assigned ITM means you *buy*
  shares — that needs **cash**, not shares. Holdings do not cover a short PE.
  A short **CE** is what holdings genuinely cover: you deliver stock you own.
* **Margin:** a short PE requires full SPAN + Exposure. Holdings reduce that only when
  **pledged**. `breeze_connect` exposes no pledge API — `get_demat_holdings` reports
  `quantity` vs `demat_avail_quantity` (see §9.6: the two gaps are *pledged* and *blocked
  for trade*, and they are separable) and pledged collateral
  surfaces as an undifferentiated lump in `get_funds` / `get_margin`. Per-scrip margin
  attribution to a pledge is therefore **not available and must not be claimed in the UI**.

Consequence: Bot 1 writes **both legs with different constraint models** — CE capped by
holdings, PE capped by delivery cash. See §3.

---

## 1. Section design

New **top-level `/bots` section** (nav peer of Strategy Builder / Portfolio), containing:

1. **Bot list** — one card per bot instance, showing lifecycle state:
   `idle` · `awaiting-approval` · `armed` · `fired` · `disarmed` · `skipped`.
2. **Per-bot config + run history.**
3. **Shared run log** — a first-class cross-bot tab: every scan, proposal, order, fill,
   skip, and *the reason for each*. This is not optional polish. Bot 2 trades unattended;
   an unexplained no-trade day is indistinguishable from a broken bot without it.

Every terminal outcome carries a machine-readable reason code plus human text. "Skipped:
no broker session by 12:00" and "Skipped: 1 lot exceeded margin cap" must be
distinguishable in the log without reading backend logs.

---

## 2. What this reuses (do not rebuild)

| Need | Existing |
|---|---|
| Chain + live quotes | `chain_builder` worker, `reference_data/active_chains.py`, `chain_readiness.wait_for_canonical_chain()`, `ChainBuildStatus` / `SectionGate` on the frontend |
| SPAN estimate | `reference_data/span_baseline_store.compute_span_margin_required()`, `span_portfolio_scan.py` |
| SPAN with netting | `resolve_portfolio_span_margin` (see `route_strategy_builder.py`) |
| ELM bands | `ELM_INDEX_STD` / `ELM_INDEX_DEEP_OTM` / `ELM_STOCK_STD` / `ELM_STOCK_DEEP_OTM` (+ `_THRESHOLD`) in `app/core/config.py` |
| Autonomous order placement | `squareoff_dispatcher.py` — license guard, `is_breeze_rate_limited`, freeze-qty chunking, Telegram, audit. Bot 2's placement path mirrors this module's structure. |
| Stop-loss machinery | `repositories/squareoff_rules.arm_rule()` + `strategy_group_arm_guard` + `strategy_group_lifecycle` |
| Order pricing | `aggressive_limit.py` (limit_tolerance mode) |
| Session-lapse nag | `squareoff_watch.py` → portal → Telegram (works even when the deployment is powered off) |
| License gate | `deployment_license_status.trading_mutations_allowed` |

**Bot 2 must arm its stop through `strategy_group_arm_guard`, not straight into the repo** —
the guard is where arm-time validation lives.

---

## 3. Bot 1 — Holdings Option Writer

> **Superseded in part by section 9.** Bot 1 now also runs autonomously. The constraint
> models below (CE capped by holdings, PE by delivery cash) are unchanged and still binding.

**Mode: propose → user approves → place.** This is a monthly, considered decision; there is
no time pressure that justifies unattended firing, and the delivery-cash allocation step
(below) is explicitly a human decision.

### Universe
Demat holdings ∩ scrips with NSE F&O contracts (scrip master). NSE only. Stock options in
India are **monthly-only** — the expiry picker offers monthly expiries (current / next),
never weeklies.

### CE leg — default on, per-scrip opt-out

The genuinely covered trade. Hard cap:

```
max_ce_lots = floor(deliverable_qty / lot_size) - existing_short_ce_lots
```

*(§9.6 refined `held_qty` to `deliverable_qty` — the full holding minus what is blocked for
trade. Pledged stock stays in.)*

**`held_qty` comes from `get_portfolio_holdings`, never `get_demat_holdings`.** Demat
reports **0 quantity for pledged shares**, so any pledged scrip would silently undercount
coverage. Portfolio holdings reports the full position, pledged included -- the number the
cap is meant to be built on. `exchange_code` is required (`NSE` for equity).

**But Bot 1 needs both endpoints.** Verified against a real response (2026-08-31):
`get_portfolio_holdings` carries **no pledge marker at all** -- every row has the same
shape whether pledged or not. So:

```
pledged_qty = portfolio_qty - demat_qty
```

is the only way to learn what is pledged. That is worth surfacing per scrip rather than
discarding: pledged shares must be unpledged before they can be delivered on CE assignment,
which takes a settlement cycle and releases the collateral margin they were backing. The
proposal should show "N of M lots currently pledged" so the settlement-timing obligation is
visible at approval, instead of being an abstract caveat in this document.

Other verified field facts: `change_percentage` is the **day's** price move, *not* the
holding's return (real row: AMARAJ avg 751.47, cmp 892.65, change_percentage -3.11) --
never render it as P&L. `current_market_price` is populated and usable as a spot fallback.
`product_type` / `expiry_date` / `strike_price` / `right` / `action` are `null` on equity
rows, not `""`.

Remainder below one lot is ignored. A per-scrip **exclude** flag exists for holdings the
user will never sell.

### PE leg — opt-in per scrip
Not capped by holdings at all. Capped by **one global delivery-cash budget** (a rupee
ceiling the user sets):

```
Σ (strike × lot_size × lots)  ≤  delivery_cash_budget
```

**Allocation is manual.** The proposal lists every eligible PE with its delivery exposure
and premium; the user picks which to keep until the budget is spent. No ranking heuristic —
the bot shows the numbers and the running total, the user decides. Rejected: deriving the
budget from `get_funds`, which silently expands with unrelated account activity.

### Common per-scrip mechanics
* **Safety %** per scrip (global default, per-scrip override). Strike = `spot × (1 ∓ safety%)`,
  snapped **away from spot** to the nearest available strike — down for PE, up for CE. Always
  the conservative direction.
* **Premium quoted at the bid**, never LTP. Stock-option spreads are wide enough that an
  LTP-based premium estimate is misleading.
* **Existing short positions net per scrip across all expiries**, not per-expiry. A short
  Sep PE and a short Oct PE both consume coverage today.
* **SPAN + ELM shown per row and as a basket total**, computed through
  `resolve_portfolio_span_margin` so the netting benefit against existing positions is real
  rather than a naive sum.

### Proposal freshness
A proposal is a **priced snapshot with a TTL**. Quotes go stale; premium and margin must be
re-priced at approval time and the user shown any material drift before orders go out. A
proposal that cannot be re-priced (chain not ready, session gone) fails closed.

---

## 4. Bot 2 — Expiry-Day Index Writer

**Mode: fully autonomous within configured caps.** Time-critical by nature.

### Expiry-day detection
Derived from the **scrip master expiry list**, never a hardcoded weekday. SEBI has moved
these before and `market_calendar.py` only knows holidays, not expiries.
NIFTY on NFO; SENSEX on BFO (`BSESEN`, per the NSCCL baseline pfCode mapping).

### Session-availability window (the main reliability problem)
The ICICI session lapses overnight. On an expiry morning with an enabled bot:

* The nag starts at **max(app start on EC2, 08:00 IST)**.
* **Telegram reminder every 15 minutes until 12:00 IST**, stopping *immediately* once a
  valid session appears.
* Entry fires at the scheduled time (default ~09:30) if a session exists; otherwise it
  fires **as soon as a session appears, any time up to 12:00**. 12:00 is both the nag
  cutoff and the trading cutoff — no session by 12:00 ⇒ skip the day with a logged reason.

Two nag paths exist and must not be duplicated: the **local** path (app running) and the
**portal** path (`squareoff_watch` → portal → Telegram, which already covers a
powered-off deployment). Extend the portal path rather than writing a second one.

*Known trade-off, accepted:* an entry at 11:45 has surrendered most of the day's theta
while keeping full gamma risk. Judged acceptable in exchange for one less configurable time.

### Sizing
* Budget = **% of free margin, configured per index** (e.g. NIFTY 30%, SENSEX 30%), so a
  same-day collision is bounded by construction.
* SENSEX and NIFTY expiries do not currently coincide. Because they *could*, the user sets
  an explicit **priority** between them; the higher-priority index sizes first.
* Pre-trade estimate from the **SPAN baseline**, then **verified against `margin_calculator`**
  before firing. Baseline-only sizing is not sufficient to commit real capital.
* If even one lot exceeds the cap → skip with a logged reason. Never partial-fund.

### Entry

> **Superseded by section 9:** the side is now a shortlist ranked by margin yield, and may
> be a short strangle.

CE **or** PE — a static per-bot preference. No directional inference in the MVP.
Strike from the safety % against spot at fire time. Freeze-qty chunking as in
`squareoff_dispatcher._leg_qty_per_order`.

### Exit — auto-armed SG rule

On fill, Bot 2 arms a Strategy Group rule via `strategy_group_arm_guard`. The user's exit
policy is expressed as:

* **Loss limit — a multiple of premium collected.** Default 1x (stop at 100% loss).
  Genuinely a P&L quantity, so it maps onto the existing rupee `loss_limit_pnl`:
  `loss_limit_pnl = N x entry_price x qty`.
* **Profit target — an absolute option price** the user sets (e.g. exit when the option
  touches Rs 0.10 or Rs 0.05). This is **not** a rupee P&L and must not be converted into one.
  *(Superseded by section 9: the user now sets a share of the premium, from which this price
  is derived. It is still a price target on the rule, and still never a rupee P&L.)*

**Required change: an optional price-based target on the SG rule.**
Converting a price target into `profit_target_pnl` at fill time looks equivalent but is not.
`portfolio_pnl_engine._evaluate_user_pnl` computes `leg_pnl = signs * (ltps - avg_prices) *
quantities` from the **broker-reported** `average_price`, not the bot's recorded fill price.
Any divergence -- partial fills at different prices, or a broker average that folds in
charges -- silently moves the trigger away from the price the user asked for.

The engine already carries per-leg `ltp` in the snapshot it evaluates rules against, so the
correct fix is an additive optional `target_option_price` on the rule, evaluated as
`ltp <= target_price` alongside the existing rupee checks in `_evaluate_rules`. Existing
rules and the manual PB/SL feature keep `profit_target_pnl` untouched.

**Bot-only surface (decided).** `target_option_price` is set programmatically by Bot 2 and is
NOT exposed in the manual PB/SL UI. It must therefore be optional on the arm request, and the
manual screen simply never sends it -- no frontend work on that screen, and no second way for
a user to reach the same rule field. If it ever proves useful manually, exposing it later is
additive.

Two notes:

1. **This removes the single-leg restriction.** A per-leg price target is expressible for
   any number of legs ("every leg at or below its target"), where one *group* rupee target
   never was. Bot 2 stays single-leg in the MVP, but a future straddle mode is no longer
   blocked by the exit model.
2. **Tick-size floor at low premiums.** `target_premium_pct` (the exit *limit order* band,
   capped at 20%) is +/-Rs 0.02 on a Rs 0.10 option -- below the Rs 0.05 tick. The exit limit
   price degenerates near the target. Accept and document; do not silently widen the band.

No re-entry after a stop-loss in the MVP.

---

## 5. Cross-cutting

* **Runtime:** the bot runner is an **asyncio task in the API process**, alongside the portal
  heartbeat and the reference-data scheduler. It needs the broker session cache, the WS
  feed, and the P&L engine, all of which live there. `chain_builder` is a separate OS
  process only because it is CPU-heavy; that reason does not apply here.
* **Persistence:** new tables in `users.sqlite3` behind a migration — bot config, bot
  instance state, proposals, and run-log entries.
* **License:** every bot respects `require_trading_not_revoked`. Read-only mode disarms
  bots and says so on the card; it is not a failure state.
* **Audit:** decisions and orders go through the existing `audit/` logger, not ad-hoc logging.
* **Single-tenant:** one trader per deployment, so no multi-user scheduling contention.
* **Frontend wiring:** new backend paths need matching `rewrites()` entries in
  `frontend/next.config.js` *and* the nginx confs (`nginx.conf`,
  `deploy/nginx.all-in-one.conf`).

---

## 5a. Symbol namespaces — resolved, no bridge needed for bots

Two namespaces exist, but they split by **data source**, not by API:

| Namespace | Used by |
|---|---|
| ICICI **ShortName** (`HDFBAN`, `INFTEC`, `INDHO`) | every broker API — `get_portfolio_holdings`, quotes, orders, `margin_calculator` — **and** `scrip_master` |
| **NSE symbol** (`HDFCBANK`, `INFY`) | exchange-sourced files only: `fo_bhavcopy`, `exchange_margin_baseline` |

All ICICI APIs speak ShortName, so holdings join `scrip_master` directly. **No ISIN or alias
bridge is required for either bot.** A holding absent from `scrip_master` is genuinely not
F&O-eligible: the table ingests only the NFO and BFO segments, so cash-only scrips (`INDHO`,
`JUBLIF`, `LIBEES`) never appear and are correctly excluded.

Bot 1 sources everything ICICI-native and touches no exchange-keyed table:

| Need | Source |
|---|---|
| Lot size, expiries, tradeability | `scrip_master` (ShortName) |
| Spot | `current_market_price` on the holdings row, or live quotes |
| Option bid / premium | existing chain pipeline (ShortName) |
| SPAN | `margin_calculator` — already the default margin source |

SPAN is the load-bearing one. `MARGIN_SOURCE_BREEZE` is the default in both
`route_settings.py` and `processor.py`, and even under `MARGIN_SOURCE_EXCHANGE` a contract
missing from the baseline emits a `baseline_missing_contract` warning and falls back to
`breeze.margin_calculator`. Bot 1 runs occasionally over ~13 scrips, so the API cost is
acceptable — and a live call beats a baseline estimate anyway, since it reflects real netting.

### Pre-existing bug found here (not a bot blocker)

`_get_span_baseline_sheet_raw` resolves the baseline sheet through
`underlying_aliases(stock_code)`, which returns only the input itself for stocks —
`("HDFBAN",)` against a baseline keyed `HDFCBANK`. **The Exchange Risk Baseline setting
therefore never hits for stock options**; it silently falls back to Breeze on every row.
Only the eight aliased indices work. It degrades correctly, which is why it has gone
unnoticed, but a user selecting that setting is not getting it for stocks. **Deferred by decision** -- not fixed as part of the bots work, and it gates nothing here
since bots use the Breeze margin source. That is the one place an ICICI-to-NSE map would
earn its keep; ISIN is the promising bridge (`get_demat_holdings` returns `stock_ISIN`, and
the SecurityMaster CSV likely carries it -- the ingest keeps 10 columns and drops it).
Verify the raw CSV before committing to that approach.

## 6. Mock broker holdings fixture — DONE

Live broker calls only work from the production static IP, so all local development runs in
`ICICI_BROKER_MODE=mock`. **Mock mode is not a synthetic broker** -- `MockBreezeSdk` is a thin
shell over *real* local reference data: real spot from `fo_bhavcopy`, real expiries read live
from the scrip master, real `LotSize` / `QuantityLimit` / `MarginPercentage`, real
`best_bid_price`, and a real `exchange_margin_baseline` for SPAN.

The one missing input was the user's holdings. **Holdings are synthetic** (agreed
2026-08-31) -- production behaviour will be validated on the static IP later.

Delivered in `dev/fixtures/responses.py` + `dev/mock_broker.py`:

* `mock_portfolio_holding_rows()` -- 11 scrips in the ICICI ShortName namespace, covering
  a holding **below** one lot (WIPRO), **exactly** one lot (NTPC), multi-lot with an ignored
  remainder (ONGC), **partly pledged** (SAIL, CIPLA), high delivery cost to exercise the
  delivery-cash budget (MARUTI), a scrip absent from `fo_bhavcopy` so the §5a namespace gap
  stays visible in dev (RELIND), and one with **no F&O contracts** that must be filtered out
  (IRCTC).
* `mock_demat_holding_rows()` -- **deliberately reproduces the pledged-zeroing quirk** rather
  than correcting it, so a regression back to `get_demat_holdings` fails visibly in mock
  instead of quietly undersizing in production.
* Stock-option shorts added to `mock_portfolio_position_rows()` (CIPLA short CE, GAIL short
  PE) so the "don't oversell" netting path has something to net against.

**Response shape is now verified** against a real production call (2026-08-31) and the
fixture matches it field for field, including the `null`-not-`""` columns, the
`booked_profit_loss` / `realized_profit` / `open_position_value` / `portfolio_charges`
fields, and `change_percentage`'s day-move semantics. Values remain synthetic.

**Do not paste a real portfolio into this fixture.** `dev/fixtures/responses.py` sits under
`backend/src/` and ships inside the image deployed to every customer's AWS account.

## 7. Out of scope for the MVP

* Pledging / unpledging holdings (no Breeze API).
* Directional CE-vs-PE inference for Bot 2.
* Re-entry after a stop-loss.
* Bot 2 straddles / both legs on one index+expiry (no longer blocked by the exit model, but out of scope).
* Rolling positions at expiry.
* Per-scrip PE budgets, or budget derived from free funds.

---

## 8. Build progress

### Done — section scaffolding (2026-09-01)

| Piece | Where |
|---|---|
| Schema (`bots`, `bot_scrip_prefs`, `bot_runs`, `bot_proposals`) | `app/db/bots_migrate.py`, wired into `main.py` |
| Typed config + reason codes | `app/domain/bots.py` |
| Persistence | `app/repositories/bots.py` |
| Routes | `app/api/v1/route_bots.py`, mounted at `/bots` |
| Proxy wiring | `next.config.js`, `nginx.conf`, `deploy/nginx.all-in-one.conf` |
| UI | `app/bots/page.tsx`, `components/bots/*`, `lib/use-bots.ts`, nav entry in `AppShell` |
| Tests | `tests/test_bots_repository.py` (17), `tests/test_route_bots.py` (13) |

Decisions taken during the build, worth not re-litigating:

* **API paths are static, with `bot_type` as a query parameter** (`/bots/config?bot_type=…`,
  not `/bots/{bot_type}`). The app proxies by enumerating exact backend paths, and `/bots`
  is also a page. A dynamic `/bots/:botType` rewrite would swallow every future frontend
  sub-page — `/bots/holdings_writer` would proxy to the backend instead of rendering. Same
  shape `/strategy-builder` and `/uncovered-shorts` already use.
* **Config is one JSON blob per bot, typed in `domain/bots.py`, not columns.** The two bots
  share no fields; typed columns would give one wide mostly-NULL table.
* **Config is validated on read as well as write.** A blob written by an older build
  inherits current policy defaults instead of KeyError-ing inside a mid-run bot. Reads are
  forgiving (corrupt config falls back to defaults rather than breaking the bot list);
  writes are strict (a bad value is a 400 the user can see).
* **`PATCH` merges rather than replaces**, because the UI edits one panel at a time.
* **Enabling a bot is gated by `require_trading_not_revoked`** even though it places no
  order — arming something that will trade later must be refused up front, not accepted
  and then silently skipped every run.
* **One pending proposal per bot**, DB-enforced; a new scan supersedes the old rather than
  leaving the user to choose between two sets of stale prices. Proposals expire on read.

### Done — Bot 1, Holdings Option Writer (2026-09-01)

| Piece | Where |
|---|---|
| Holdings with pledging resolved | `processor.get_holdings()` |
| Scan / sizing / strike / pricing | `app/services/bots/holdings_writer.py` |
| Freeze-chunked order placement | `app/services/bots/placement.py` |
| `POST /bots/scan`, `POST /bots/proposal/approve` | `route_bots.py` |
| Proposal UI | `components/bots/ProposalPanel.tsx` |
| Tests | `test_bots_holdings_writer.py` (29), `test_bots_placement.py` (12), `test_route_bots_scan.py` (19) |

Verified end to end against the mock fixture: 9 legs proposed from 12 holdings, with CIPLA
correctly sized 3 − 1 already-written = 2, SAIL at 3 lots carrying its pledge note, ONGC at
`5000 // 2250 = 2`, and HINPET / IRCTC / LIBEES excluded for the right three reasons.

**§5a confirmed empirically.** The chain resolved for every holding *including* `RELIND`,
which has no `fo_bhavcopy` row. Bot 1 is ICICI-native end to end and no symbol bridge is
involved.

#### Change of substance: indicative pricing off-market

The design said premium is quoted at the **bid**, never LTP. That still holds when it
matters, but it cannot hold at scan time: **0 of ~29,841 NFO bhavcopy rows carry a bid.**
The bhavcopy is an end-of-day file with no order book, so outside market hours there is no
bid for any stock option — and a monthly write is normally *planned* off-market.

Resolution: a leg carries `premium_basis`. With a live bid it is `bid`; otherwise the
premium is priced off the last trade and marked `ltp_indicative`, shown in amber in the UI.
**`approve` refuses to place any leg that is still indicative when it re-prices**, and the
UI disables the button. So the user can plan on a Sunday and cannot sell into a book that
does not exist. The original constraint is enforced at the only moment it can be.

#### Other decisions taken during the build

* **PE is proposed at one lot per scrip** (`PE_LOTS_PER_SCRIP`). Holdings do not cap puts,
  and the budget is allocated by the user *across* scrips, so the bot surfaces each
  candidate with its assignment cost rather than deciding how deep to go on any one name.
  Puts also arrive **unselected**; calls arrive selected.
* **Approval re-prices and fails closed.** A selected leg that vanished, or whose bid fell
  more than `MATERIAL_DRIFT_PCT` (10%), aborts placement with a 409 — and the re-scan has
  already replaced the proposal, so the user re-approves against live prices.
* **Placement is best-effort per leg, never all-or-nothing.** A rejection on one scrip must
  not abandon orders already working on another; a leg that placed some chunks and then
  failed is reported as *partial* rather than as a clean success or failure.
* **Mock `margin_calculator` now scales with notional** (~8%) instead of a flat ₹1000/share.
  The flat figure was tuned for a 75-share NIFTY lot and produced tens of lakhs of SPAN per
  lot on a 14,100-share SAIL lot, which made every mock-mode margin number meaningless.

### Done — price target, Bot 2, and the run reaper (2026-09-01)

**`target_option_price` on the SG rule.** Additive column, threaded through
`squareoff_rules_migrate` → `SquareOffRuleRecord` → `repo.arm_rule` → `GroupRule` →
`_price_target_reached`, and restored on startup hydration so a restart cannot silently drop
a bot's exit. Deliberately **absent from `ArmSquareOffRuleRequest`**, which is what makes it
bot-only: the manual PB/SL screen has no field to populate and arms through the route, while
the bot arms through the repository. It fires when **every short leg** in the group is at or
below the target — booking a group because one side collapsed would leave the other naked —
and long legs are ignored, since "buy this back cheaply" is meaningless for a leg we are long.

**Bot 2.** Split into a pure `decide()` and an IO-doing `fire_index()`, so the session
arriving at 11:47, the cutoff passing with nobody logged in, and an app booting after the nag
window opens are all testable without a broker, a market, or the real clock. Driven by a
daemon thread (`services/bots/scheduler.py`) in the API process, matching
`reference_data/scheduler.py`.

| Piece | Where |
|---|---|
| Decision + sizing + exit arming | `app/services/bots/expiry_index_writer.py` |
| Sweep, nag, run logging | `app/services/bots/scheduler.py`, started in the lifespan |
| Tests | `test_squareoff_price_target.py` (16), `test_bots_expiry_index_writer.py` (24), `test_bots_scheduler.py` (11) |

Verified live: with the bot enabled, the sweep read the scrip master, found that **NIFTY
expires 01-Sep-2026** (a Tuesday — my own weekday assumption was wrong, which is exactly why
§4 says read the master, not a weekday), computed a ₹525,000 budget as 30% of the ₹17.5L free
margin, classified the trigger as `session_arrival` because it was long past the entry time,
and failed closed on a missing off-market spot without placing anything.

#### Decisions taken during the build

* **The double-fire guard is structural, not conventional.** `decide()` refuses to act twice
  in a day, but a test that stubbed `decide` proved the scheduler had no guard of its own.
  Anything that *writes* — a run row or an order — now re-checks the day in the scheduler
  too. A nag is exempt: it writes nothing and must keep going until the session appears.
* **`profit_target_pnl` is pushed out of reach when a price target is set.** The column is
  NOT NULL and positive, so Bot 2 sets it to 100× the premium collected; the two are
  alternatives, not a pair, and a reachable rupee target would front-run the price target.
* **No indicative-price fallback in Bot 2**, unlike Bot 1. This bot only ever runs during
  market hours, so a missing bid means the book really is empty.
* **A position opened without its stop armed is reported as `failed`**, with "WITHOUT a stop"
  in the run text. It is the worst state this bot can leave behind and must never read as a
  clean fire.

#### The stuck-`running` reaper

`repo.reap_stale_runs()` closes runs left `running`, on two schedules:

* **At startup, with no age bound.** The deployment is single-instance by design — one
  backend process owns this SQLite file — so any `running` row at startup is *definitionally*
  stale; nothing else could still be working on it. This is the case that actually happens,
  since the portal recreates the container for version upgrades.
* **Every sweep, bounded to 30 minutes.** Catches a run that hangs without the process dying
  (a blocked broker call with no timeout), while never killing a scan that is merely slow.

The reason code is its own — `interrupted`, not `internal_error` — because "we never found
out what happened" is genuinely different from "it errored", and the run log is where that
distinction matters. The reason *text* points at the Order Book rather than implying nothing
happened: an interrupted run may already have placed orders before it died.

A heartbeat/lease scheme was considered and rejected: it only earns its keep when runs can
legitimately outlive a restart or run concurrently, and neither is true here.

### Next

Nothing is blocking. Remaining known items:

1. The Exchange Risk Baseline stock alias bug (§5a) — deferred by decision, independent.
2. Bot 2 UI beyond config and the run log: there is no "what is my bot holding right now"
   panel, since the position surfaces in Portfolio and its stop in the Exit Board.
3. No re-entry after a stop-loss, per §7.


---

## 9. The 2026-09-02 rework

Driven by a full redesign of the `/bots` page. Every assumption below was put to the user
and confirmed before any code changed; the three UI layouts were chosen from mock-ups.

### 9.1 Reversed decisions

| Was | Now | Why |
|---|---|---|
| Bot 1 proposes only; unattended firing is "not justified" (§3) | Bot 1 also runs **autonomously**, on a firing day N trading days before expiry | The delivery-cash allocation step that made it a human decision is now expressed as a **per-scrip priority**, so the bot has a rule where it previously had none |
| Bot 2's side is a static `right` (§4) | A **shortlist** of naked CE / naked PE / short strangle | The user wanted to shortlist and let the bot pick |
| Profit booking is an absolute option price (§4) | A **share of the premium**; 100% means let it expire | Symmetry with the loss limit, which was already a premium multiple |
| Strangles are out of scope (§7) | In scope for Bot 2 | The exit model already supported multi-leg; §7 said so |

### 9.2 The strangle ranking trap

A shortlist needs a comparison rule, and **absolute premium is the wrong one**: a strangle
is both legs, so it collects more than either alone *every time*. Shortlisting all three
with an absolute-premium rule would silently retire naked CE and naked PE.

Ranking is therefore **premium per rupee of margin**. Both sides go to
`margin_calculator` in a single call so the exchange's netting is real — pricing each side
alone and summing would overstate a strangle's cost by the whole netting benefit and bias
the contest against the shape the netting exists to reward.

`test_strategies_are_ranked_by_yield_not_by_absolute_premium` is the load-bearing test: a
strangle collecting more premium than the naked put still loses, because the extra premium
does not pay for the extra margin.

### 9.3 Profit booking at 100%

`profit_book_premium_pct` sets a per-leg price target of `entry × (1 − pct/100)`, still
passed to the rule as `target_option_price` and never converted into rupees — the §4
reasoning about `average_price` is unchanged.

**At 100% the target price is zero, which no limit order can reach**, so no profit target is
armed at all and the position runs to expiry with only the stop-loss live. That is the
honest reading of "let it expire worthless"; the alternative — buying back at the ₹0.05 tick
floor — is a different trade from the one the user asked for.

On a strangle the group takes **one** target, the minimum across its legs, so it books only
when both sides are cheap. Booking because one side collapsed would leave the other naked.

### 9.4 Cross-bot priority

A `priority` column on `bots` (not in the config blob — it is cross-bot, and ordering wants
to be queryable). The scheduler now sweeps **users**, not bot types: for each user, bots run
in priority order and each returns the margin it committed, which is subtracted from what
the next one sees.

Ordering alone would have been nearly a no-op — both bots size off free margin, so whoever
ran first would take what it needed regardless. Sizing-first is what makes the number mean
something.

### 9.5 Per-scrip sizing

`bot_scrip_prefs` gains `ce_lots`, `pe_lots` and `priority`. Both lot columns are
**nullable, and NULL means the pre-rework behaviour** — every covered lot for calls, one lot
for puts — so a deployment upgrading into this keeps writing exactly what it wrote before,
with no backfill.

A call lot count is a **target inside the coverage cap**, never a way past it: asking for 5
lots on a 3-lot holding writes 3 and says so on the row. The alternative — honouring the
number — would sell naked calls off a bot whose entire premise is that its calls are covered.

Allocation walks legs in priority order against two independent budgets (SPAN + ELM against
free margin; delivery exposure against the user's own budget figure). A leg that does not fit
is skipped and **the walk continues** — priority orders funding, it does not gate it, and
punishing a cheap scrip for sitting behind an expensive one would leave margin unused.

### 9.6 A holding splits three ways, and only one of them is not coverage

The original design tracked one encumbrance, pledging, and treated the rest of a holding as
deliverable. That is one category short. `get_demat_holdings` returns **both** `quantity` and
`demat_avail_quantity`, and the real response carries a genuine gap between them
(`quantity: "1", demat_avail_quantity: "0"`). So:

```
pledged   = portfolio_qty - demat_qty          (collateral)
blocked   = demat_qty - demat_avail_quantity   (earmarked: pending sale, settlement hold)
available = demat_avail_quantity               (free)
```

exhaustively, and `available + blocked + pledged = portfolio_qty`.

**Only `blocked` is excluded from call coverage.** Pledged stock is genuinely owned; it has
to be unpledged before expiry to deliver, which is a step the user takes and an obligation to
surface — not a reason to leave the coverage unwritten. Blocked stock is already committed
elsewhere and is not the user's to deliver at all. `deliverable_quantity()` is the single
place this is decided, and the drawer renders the same split so the lot cap is explainable
from the row above it.

When the demat call fails, all three are `None` — **unknown, not zero** — and the full
holding is used. Erring the other way would stop the bot writing anything across the whole
portfolio the moment one broker call failed. The `pledged_quantity=None` convention that
already carried this distinction now carries it for all three.

The mock fixture gained a blocked case (ONGC, 2250 of 5000 blocked) chosen so the path
changes a *lot count* in mock — 2 lots to 1 — rather than only a label.

### 9.7 UI

Chosen from mock-ups: square cards in a two-up grid (identity and state above, the
autonomous pill and a manual Start pinned to the bottom edge); a **tabbed right-hand
drawer** for settings, extending the existing `Modal` rather than a second implementation of
focus-trapping and scroll-locking; and a **full-screen sheet** for the manual run, whose
lifetime matches a priced snapshot you act on or abandon.

The manual run is now shared by both bots (`POST /bots/plan` sizes Bot 2 without placing).
Editing a leg re-prices through `POST /bots/proposal/reprice` rather than scaling in the
browser: margin is not linear in lot count, and a figure nobody quoted is not a figure to
decide on.

The scrip list in the drawer is read live from `get_portfolio_holdings` +
`get_demat_holdings` on every open, never stored — holdings change without the bot being
told. Prefs for a scrip the user has since sold are **kept**, so selling and rebuying a name
does not wipe its configuration.

### 9.8 Deliberately unchanged

* The manual PB/SL screen keeps its rupee targets and % limit bands. The absolute paise
  target only ever existed inside Bot 2's config and was never exposed there.
* Bot 1's stop-loss on approval: re-price before placing, abort above 10% bid drift, and
  refuse outright on off-market indicative prices. The unattended path fails closed the same
  way.
* The Exchange Risk Baseline stock alias bug (§5a) — still deferred, still independent.

---

## 10. Semi-autonomous bots — Telegram approval (2026-09-03)

A third run mode, between "I approve every trade in the app" and "trade without me". The bot
still runs on its own schedule and still sizes the trade itself, but instead of placing it,
it sends the priced legs to the user's linked Telegram chat with Approve / Reject buttons.
**Silence never trades.**

Set per bot: `approval_mode: "auto" | "telegram"`, on both configs, defaulting to `auto` so
an upgraded deployment keeps exactly the behaviour it had.

### 10.1 Why this was cheap

Almost none of it is new machinery. The portal has been the fleet's single Telegram consumer
since the link rearchitecture (§ `telegram_link_routing.py`), and approvals ride the same
three hops one token namespace along — `approval-register` → webhook → `link-claim` — so
there is no chat-id-to-deployment table, no migration, and no backfill for users who linked
before this existed. Both bots already had a plan/execute split (`plan_index` +
`execute_plan`; Bot 1's allocation then `place_short_legs`), so a proposal is an insertion
between two halves that were already separate. `bot_proposals` already had the full
pending → approved/rejected/expired/superseded lifecycle with a TTL.

### 10.2 Proposing is not acting

The one real obstacle. `has_terminal_run_today` counts **any** run row today, which is
correct for the autonomous path — a run row there really does resolve the day. But a
semi-autonomous bot writes a `proposed` run the moment it asks, so under that rule the first
proposal would end the day: no re-ask, no eventual placement, ever.

`has_committed_run_today` is the narrower predicate, used **only** on the new path:
`running`, `completed`, `failed` and `skipped` count; `proposed` does not.
`has_terminal_run_today` is deliberately untouched, so nothing changes for a user who never
turns this on.

Run-log shape: one `proposed` run per day (finished immediately, so the stale-run reaper
never sees a run that is waiting on a human), N proposals against it — `create_proposal`
already supersedes — and a second run row when an approval actually places.

### 10.3 Decisions taken

| Question | Answer | Why |
|---|---|---|
| Unanswered when the proposal expires | Re-propose at fresh prices every `nag_interval_minutes` until `cutoff_ist` | A 15-minute TTL against a 09:30–12:00 window would surrender the morning on one missed message |
| What the message offers | Approve all / Reject / Review in app | Per-leg selection and the lots/strike edits need reprice; that stays where reprice lives |
| Rejection | Terminal for the day | A rejection is a decision, not a deferral |
| Approval refused on drift | Report what moved, then re-propose | Same reasoning as the timeout |
| Scope | Per bot | Bot 2 can be gated on expiry morning while Bot 1 trades on its own |

### 10.4 Safety properties worth not breaking

* **The portal routes; the deployment decides.** A callback arrives because the portal
  matched a token to us, but `consume_approval_token` alone decides whether it still
  authorises anything. That split is what keeps a router incapable of placing a trade.
* **A tap is not a placement.** Approval goes through `services/bots/proposals`, the same
  re-price-and-refuse path as the app's review screen, which is why the approve logic was
  lifted out of `route_bots` first. An approved proposal whose bid has moved past
  `MATERIAL_DRIFT_PCT` places nothing — and the outcome message says so rather than "done".
* **Tokens are single-use and burned by supersession.** A new proposal burns the old
  token, so a user scrolling back cannot approve prices the bot has already replaced.
  Approve and Reject share one token, so the first tap wins.
* **Read-only mode is checked explicitly.** `require_trading_not_revoked` is an HTTP
  dependency and there is no request on this path.
* **A bot that cannot ask says so.** `telegram` mode with no linked chat logs
  `approval_unreachable` rather than failing as a quiet no-trade day; the settings drawer
  warns about it up front.

### 10.5 The portal trap

`set_webhook` sent `allowed_updates: ["message"]`, so callback queries were not delivered at
all — there is no error for this, the buttons simply do nothing. Adding `"callback_query"`
is not sufficient on its own: `reconcile_telegram_webhook` returned early whenever the URL
matched, and the fleet's webhook is already armed, so the new subscription would never have
been applied. The reconcile now compares `allowed_updates` too, and re-arms on drift with
`drop_pending_updates=False` (a re-point must never discard live handshakes).

### 10.6 Rollout order

Portal first — the new endpoint and the subscription are inert without engine support.
Reversed, the engine sends buttons the portal drops on the floor and the user taps a dead
control on an expiry morning.

### 10.7 The re-price scan's leftovers

`_approve_holdings` re-prices by running a **real scan**, and a scan legitimately creates a
proposal — superseding the very one being approved. That has two quite different meanings
depending on how the approval ends, and the two were previously conflated:

* **On the drift path it is the point.** The refusal message says "a fresh proposal is
  ready", and it is: the new proposal carries the prices the market actually moved to.
* **On the success path it is debris.** The orders are out. A proposal still sitting there
  offers the user the same trade a second time.

So the success path now calls `repo.supersede_other_pending(user_id, bot_type, approved_id)`
and the drift path keeps its proposal — but in `telegram` mode it no longer strands it.
`hitl._ask_again` mints a token for the fresh proposal and sends it straight away.

That second half fixes a real stall, not just tidiness. `next_action` reads any pending
proposal as "already asked", so an unadopted one would block the scheduler's re-ask for its
whole TTL *and then* the nag interval — around half an hour of an expiry morning — while the
user, having tapped Approve and been told the price moved, was never shown what it moved to.

Bot 2 re-derives its plan without persisting one, so there is nothing to adopt and its own
cadence takes over. The invariant is "never strand a proposal", not "always re-ask instantly".
