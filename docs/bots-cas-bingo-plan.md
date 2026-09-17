# CAS Bingo — expiry-day closing-auction bot (design)

Bot 5. Trades the contract expiring **today** on NIFTY (NFO) and/or SENSEX (BFO, `BSESEN`)
inside two user-set windows around the Closing Auction Session (CAS). Every requirement below
was put to the user and confirmed before any code was written (2026-09-12); where a choice was
made, the alternative that lost is named so nobody "fixes" it back.

Read first: `docs/bots-mvp-plan.md` §2, §5, §9 (what bots reuse), `docs/bots-scalping-plan.md`
§2.2 (the SG collision), §4.3 (buy-first sequencing), §7 (paper fills), and
`docs/design-decisions.md` #24 (serialized broker calls) and #30 (the one signal source).

---

## 0. The regime this bot trades in, recorded once

Since 2026-08-03, F&O cash stocks stop continuous trading at 15:15, take no orders
15:15–15:20, collect auction orders 15:20–15:30 (random close 15:28–15:30) and match
15:30–15:35. Derivatives keep trading. The index freezes at its 15:15 level and then prints an
**indicative** value built from constituents' auction equilibrium prices, and the expiry
settles on the auction close. SENSEX expiries have swung 2–3% inside CAS.

Two consequences the user accepted with eyes open:

* **The W-OBI signal is `unavailable` from ~15:15 to ~15:20** (constituent books are empty),
  so no spread can trigger then. It simply waits.
* **After ~15:20 the signal reads auction books**, which the shadow report has never scored.
  A `ready` readiness verdict is evidence about continuous trading, not about those minutes.
  Simulation mode is the only evidence CAS Bingo gets for them.

SEBI's consultation of 2026-09-12 (comments due 2026-10-03) may move expiry settlement off
the auction. Re-read this section if it is adopted.

---

## 1. Modes

| Card | Stored as | Behaviour |
|---|---|---|
| **Off** | `enabled=false` | Never fires on its own. The run sheet (card's play icon, available in every mode) prices all five structures (§6) and the user picks one. The signal is not consulted. |
| **Simulation** | `enabled=true, mode="simulation"` | Full autonomous logic on live prices; fills simulated at the touch with Settings → Trading Costs (`scalping/paper.py`); liquidation is reported, never placed. **Not** gated on signal readiness — it is how evidence is gathered. |
| **Autonomous** | `enabled=true, mode="live"` | Places real orders. Spread entries require the signal's readiness verdict to be `ready` for that index. |

**HITL (Telegram approval) is deliberately absent.** The user dropped it: the existing re-ask
cadence (5–120 min) cannot serve a signal flip inside a ~15-minute window, and a short-TTL
variant was judged not worth it. Simulation takes its place as the "try it without risk" mode.

No paper-evidence gate on Autonomous (the user chose "runs regardless" over Bots 3/4's gate).

---

## 2. When it may trade

* **Expiry day only**, per index, from the scrip master (`bots/scheduler._expiring_today`),
  never a weekday rule.
* **Two entry windows, both live** (the user rejected "pre-CAS observes, CAS enters"):
  `pre_cas_window` default 14:30–15:15, `cas_window` default 15:15–15:29. Both editable,
  must end by the market close.
* **One entry per index per day.** The first cycle row for an index (even an aborted entry)
  ends that index's day. A stopped-out position is not re-entered. Planning failures that
  place nothing (chain warming, a quote missing) do not end the day — they are retried.
* Standard gates, in order: licence (read-only disarms entries), broker session, trading day,
  expiry day, inside a window, no unresolved intent row, SG conflict (§8).

---

## 3. Triggers

All signal reads go through `index_signal/reader.py` for the current state and
`index_signal/shadow_log.load_rows` for today's history. The shadow log is durable, so a
restart mid-window does not forget a flip. `unavailable` is never `neutral`.

A **flip** is a `transition` row into `bullish` or `bearish` whose timestamp falls inside an
entry window today. The user chose live state flips over bucketed 5-minute bars.

### 3.1 Debit spread — strong flip, then sustain
1. A flip into side S (bullish ⇒ call debit spread, bearish ⇒ put debit spread).
2. **Strong:** since that flip, while still on S, |signal| has reached
   `debit.strong_threshold` (default 0.50, above the navbar's 0.30 entry). Read from the
   flip row, every later sample row, and the live reading.
3. **Sustained:** the state has stayed on S, with no transition, for
   `debit.sustain_minutes` (default 3) since the flip.
4. Enter while the state is still S.

### 3.2 Credit spread — move from open, then any reversal flip
1. The day's open for the index (§5) must be known.
2. A flip whose logged spot is at least `credit.move_trigger_pct` away from the open, and
   whose direction is **against** that move: index up ⇒ a bearish flip ⇒ **sell a CE**
   (bear call credit spread); index down ⇒ a bullish flip ⇒ **sell a PE** (bull put).
   Any flip, no strength requirement (the user's choice).
3. Enter while the state is still on the flipped side.

This rule governs the **pre-CAS window only** since 2026-09-13; inside the CAS window the
credit spread is §3.2b's.

### 3.2b Credit spread inside the auction — sell what should settle worthless (2026-09-13)
Decided by the user after the paper-log review: the flip rule cannot work inside the auction,
because 15:15–15:20 the signal is `unavailable` and after 15:20 it reads auction books the
readiness evidence never scored. The replacement reads no signal at all.

1. **Nothing before 15:20** (`triggers.AUCTION_ORDER_ENTRY_IST`). Only once auction orders
   are taken do constituents publish equilibrium prices, so only then is the index's
   *indicative* value a settlement estimate.
2. **Side**: the indicative index against the day's open. Up ⇒ sell calls (bear call
   credit), down ⇒ sell puts (bull put credit).
3. **Strikes from the indicative index**, not the open: the sold leg at least
   `credit.auction_gap_pct` beyond it (default 1.0%, a user setting — SENSEX has swung 2–3%
   inside CAS), the hedge one spread width further out (`outer_pct − inner_pct`). Measured
   from the live index feed only, never a chain row's spot.
4. **Sell only if it still pays**: net credit at the touch ≥ `credit.auction_min_credit_pct`
   of the width (default 10%). An option that should expire worthless but still costs that
   much is the spike being faded. Below it the pass retries — premiums can still spike. This
   threshold was my addition, flagged to the user: "still costs real money" needed a
   definition, and credit/width is the market's own price of a breach.
5. Held to expiry under §7.1's exits, as before.

### 3.3 Long strangle — the clock
No signal. Fires at `strangle.entry_time_ist` (default 15:15, must sit inside a window),
or on the first pass after it if the bot came up late, until the window it sits in closes.

### 3.4 Readiness gate
Autonomous spread entries read `shadow_log.readiness(label)["status"]` (cached 5 minutes —
it scans 60 days of rows). Anything but `ready` ⇒ no entry, logged as `signal_not_ready`.
Simulation, Manual and the strangle are not gated. Nor is §3.2b's auction credit spread
(2026-09-13): readiness is evidence about the signal, and that rule does not read it.

---

## 4. Structures and strikes

Strikes snap to the nearest **listed** strike from the live chain, rounded **away** from the
reference (Bot 2's `_pick_strike` rule), so a distance is always at least what was asked.

| Structure | Buy leg (placed first) | Sell leg | Reference |
|---|---|---|---|
| Bear call credit (after a rise) | CE at open × (1 + outer%) | CE at open × (1 + inner%) | **day's open** |
| Bull put credit (after a drop) | PE at open × (1 − outer%) | PE at open × (1 − inner%) | **day's open** |
| Bull call debit (bullish) | CE at spot × (1 + inner%) | CE at spot × (1 + outer%) | spot LTP at deploy |
| Bear put debit (bearish) | PE at spot × (1 − inner%) | PE at spot × (1 − outer%) | spot LTP at deploy |
| Long strangle | CE at spot × (1 + call%) **and** PE at spot × (1 − put%) | — | spot LTP at deploy |

`outer% > inner%` is validated. Debit `inner% = 0` means ATM.

**A credit spread's sold leg may be in the money versus spot at entry** — measured from the
open, a 1.5% rally with a 1% inner distance sells an ITM call. The user confirmed this is the
bet (the reversal thesis is a return toward the open). The proposal shows it plainly.

"Spot LTP" after 15:15 is the exchange's indicative auction value — which is also what the
expiry settles on.

### 4.1 Sizing — never partial-funded
* **Credit:** largest whole-lot count whose `margin_calculator` figure for both legs together
  (BUY leg included, so the hedge nets) is ≤ `credit.margin_lakhs × 1e5`. One call for one
  lot, one estimate, at most three calls — `iron_fly_bot.size_fly`'s walk.
* **Debit:** `floor(debit.premium_budget_inr / (net debit per unit × lot))`, priced at the
  buy leg's ask minus the sell leg's bid.
* **Strangle:** `floor(strangle.premium_budget_inr / ((CE ask + PE ask) × lot))`.
* Under one lot ⇒ skip with a logged reason.

---

## 5. The day's open

Nothing stored it before this bot. `index_spot_feed._on_raw_tick` now keeps the tick's own
`open` field (the SDK's exchange-quote ticks carry it) per index per IST day, and
`index_spot_feed.day_open(label)` reads it, falling back to the `open` on the REST
`get_quotes` cash row fetched with the previous close. No open ⇒ credit spreads skip
(`day_open_unavailable`); they never fall back to the previous close — a gap-up day would
then read as a move.

---

## 6. Manual run sheet

One sheet, every enabled index expiring today, **all five structures priced side by side**
(bull put credit, bear call credit, bull call debit, bear put debit, long strangle), each
sized from the user's settings, each with its liquidation plan if margin falls short, and a
per-structure **Execute**. The credit-spread warning (§10) sits on both credit rows. Execute
re-plans that one structure at fresh prices before placing — a figure nobody quoted is not a
figure to trade on (`bots-mvp-plan.md` §9.7). Manual trades become cycles the exit loop
manages, exactly like autonomous ones.

---

## 7. Execution

* **Buy leg first, confirmed filled, then the sell leg.** `live.place_and_confirm`, one leg at
  a time through the serialized pacer (#24). A strangle is two buys, CE then PE.
* **Sell leg fails ⇒ unwind the buy leg** through the exit ladder (Bot 4's
  `_abort_live_entry` shape); the cycle closes as `entry_partial_unwound`. An unwind that
  itself fails leaves the row open, disarms the bot and alerts.
* **Intent row before any order** (`repo.open_cycle(..., detail={"pending": True})`), so a
  crash mid-placement is reconciled at startup (`guards.reconcile_pending_cycles`).
* `live._rest_order_state` gained an `exchange_code`: it hard-coded NFO, so a SENSEX (BFO)
  fill could never be confirmed over REST.

### 7.1 Exits — the bot's own tick loop
Chosen over the SG rule engine (whose price target counts short legs only, and whose group
rule would merge with Bot 2's). Per structure, against the **net entry premium**, valued at
prices the position could actually be closed at (shorts at the ask, longs at the bid —
`iron_fly_bot.cost_to_close`):

* Credit: target when captured ≥ `target_pct` % of the credit; stop when the loss ≥
  `stop_loss_pct` % of the credit.
* Debit / strangle: target when the gain ≥ `target_pct` % of the debit; stop when the loss ≥
  `stop_loss_pct` % of the debit.

Exit order: shorts bought back first, then longs sold. If neither fires, the position
**settles at expiry** (the user chose this over a hard square-off). After the close the cycle
is closed as `expired_settled` at intrinsic value against the last index level — an
**estimate**, flagged as such, because the official settlement price is published later.

---

## 8. SG rule conflict — refuse

A PB/SL group rule is keyed on (stock_code, expiry) and would absorb CAS Bingo's legs. If one
is live on the index's expiring contract, CAS Bingo **does not enter** (`sg_rule_conflict`).
The user chose this over disarming the rule or scoping rules to their legs. Consequence,
accepted: on a day Bot 2 armed its rule, CAS Bingo sits out that index.

---

## 9. Liquidation to free margin

When the free margin (`get_margin_situation`) cannot cover the chosen structure's requirement
(credit: its `margin_calculator` figure; debit/strangle: the premium):

1. **Candidates:** every **short** option on that index and today's expiry, from
   `get_positions` (Bot 2's, hand-placed, anything). A long is never sold for margin.
2. **Eligible** only if it is profitable enough: current ask ≤
   `(1 − min_captured_pct/100) × average_price` — e.g. 80% ⇒ buy back only at or below 20% of
   the price it was sold at. The buy-back limit is capped at that price, so the liquidation
   can never be unprofitable.
3. **Order:** most premium captured first (the user's choice).
4. **How much:** walk candidates, estimating each one's margin release **locally** —
   `span_portfolio_scan.resolve_portfolio_span_margin` on the same-expiry book with and
   without the legs (bit-identical to ICICI on SPAN, per the margin harness), plus the index
   ELM overlay those shorts carried (2%, 3% deep OTM), minus the buy-back cost — until the
   estimate covers the shortfall × (1 + `safety_buffer_pct`). Partial quantity of the last
   candidate is allowed in whole lots.
5. **Verify with the limits call, not `margin_calculator`:** after the buy-backs fill, re-read
   `get_margin_situation` once. Short? Take the next candidate, at most two more rounds.
   Still short ⇒ skip the entry, logged; the buy-backs stand (each was profitable on its own).

**Zero `margin_calculator` calls are spent on liquidation** — the user asked to minimise them.
The local engine's known blind spot (ICICI charges up to ~22% more on short *calls*) is what
the safety buffer and the verify loop absorb.

Simulation reports the plan ("would buy back X") and simulates the entry as if it succeeded.
Manual shows the plan in the sheet and executes it on the user's click.

---

## 10. Warnings

On the Credit spread choice in settings, the Autonomous confirmation, and both credit rows of
the manual sheet:

> ICICI may square off your positions at an extreme loss if MTM or margin requirements spike
> during CAS.

Plus, on the card and the confirmation: during CAS the index is an indicative auction value;
SEBI's consultation (comments due 3 Oct 2026) may move expiry settlement off the auction.

---

## 11. Where it lives

* `db/bots_migrate.py` — `BOT_CAS_BINGO = "cas_bingo"`. No schema change: config is a JSON
  blob, positions are `bot_cycles` rows (`structure` names the shape, `detail.index` the index).
* `domain/bots.py` — `CasBingoConfig` and its blocks.
* `services/bots/cas_bingo/` — `market.py` (index-parameterised chain, quote, lot size,
  expiry), `triggers.py` (pure), `plan.py` (strikes + sizing), `liquidation.py`,
  `execution.py` (paper + live, entries + exits), `runtime.py` (the loop thread).
* `api/v1/route_bots.py` — `POST /bots/cas-bingo/plan`, `POST /bots/cas-bingo/execute`;
  mirrored in `next.config.js`, `nginx.conf`, `deploy/nginx.all-in-one.conf`.
* Frontend — `CasBingoCard`, `CasBingoSettings`, `CasBingoSheet`.

Tested in mock mode only (ICICI's static-IP constraint).
