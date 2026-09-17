# Bots 3 & 4 — Intraday scalping bots (design)

Status: **designed, not built.** Every assumption below was put to the user and confirmed
on 2026-09-06 before this document was written; the answers are recorded inline as
decisions, not options.

This is a companion to `docs/bots-mvp-plan.md` and follows its convention: it records
*decisions and their reasons*. Route shapes, schemas and file layout are left to
implementation except where a decision constrains them. Read the parent document first —
sections 2 (what to reuse), 5 (cross-cutting) and 9.4 (cross-bot priority) apply here
unchanged unless contradicted below.

Origin: a Gemini consultation the user ran on options scalping, whose recommendations are
adopted where they hold up against this codebase and departed from where they do not. The
departures are marked.

---

## 0. The expectancy note, recorded once

The source conversation's own opening answer is that buying an option and expecting it to
mean-revert a few basis points is "one of the fastest ways to lose capital". That objection
was put to the user and resolved by **choosing an underlying-driven signal** (§3.2) rather
than option-price mean reversion. It is recorded here so that a future reader does not
rediscover the objection and mistake the design for an oversight.

The residual honest caveat, which no design choice removes: round-trip friction on one
NIFTY lot is roughly **₹100, about 1.5–2 option points**. Every parameter in §3 has to clear
that before it earns anything. §6.4 makes that number visible rather than assumed.

---

## 1. Why these are a different shape from Bots 1 and 2

Bots 1 and 2 make **one decision per day**. The 30-second scheduler tick in
`services/bots/scheduler.py` is ample, a human can be asked before anything is placed, and
`has_terminal_run_today` meaningfully resolves the day.

Bots 3 and 4 make **many decisions per session**, on a signal that is stale in seconds, with
no human in the loop by construction. Three consequences follow, and they are the reason
this is a new runtime rather than two more entries in the existing scheduler:

| | Bots 1 & 2 | Bots 3 & 4 |
|---|---|---|
| Cadence | 30s scheduler tick | WS tick-driven, coalesced |
| Human gate | `approval_mode: auto \| telegram` | **auto only** — approving 25 scalps a day is not a workflow |
| Run log | one terminal run per day | one **session** run per day, N **cycles** beneath it (§9) |
| Exits | Strategy Group rule engine | **own exit loop** (§2.1) |

`approval_mode` is therefore not offered on either config. Telegram still receives
*notifications* — session start, each circuit breaker, the daily stop — through
`telegram_alerts`, but never a button that gates a trade.

---

## 2. What this reuses, and the two places it cannot

Everything in `docs/bots-mvp-plan.md` §2 still applies. Additionally:

| Need | Existing |
|---|---|
| Live bid/ask per contract, zero API cost | `ws_tick_pipeline.register_tick_listener` — `ltp/bid/ask` already extracted at `_extract_price_fields` |
| Raw ticks (for futures volume) | `ws_tick_pipeline.register_raw_tick_listener` |
| Fill confirmation without polling | `register_order_notification_listener` + the order-feed watchdog |
| Chain subscription on demand | `reference_data/active_chains.py`, `breeze_websocket_manager.subscribe_option` |
| Freeze-qty chunked placement | `services/bots/placement.py` (needs a buy-side sibling, §3.6) |
| Marketable-limit exit pricing | `squareoff_dispatcher._leg_limit_price` |
| Serialized broker calls, 429 retry | `services/icici_api_pacing.py` — **do not bypass**, design-decision #24 |
| Black-Scholes / IV | `services/iv_compute.py` (used by the backtest, §8) |
| India VIX | `services/dashboard_vix.py` |
| Index spot | `services/index_spot_feed.py` |

### 2.1 Why the exits cannot ride the Strategy Group rule engine

Two independent blockers, both verified in code:

1. **`target_option_price` counts short legs only.** `portfolio_pnl_engine._price_target_reached`
   documents this explicitly — a price target means "buy this back cheaply", which is
   meaningless for a long leg. Bot 3 is long. Its profit target is the opposite comparison
   and does not exist in the rule model.
2. **The rule model has no trailing stop.** A `GroupRule` carries fixed rupee targets and an
   optional price target. The three-level ladder in §3.5 has no expression there at all.

So both bots run **their own tick-driven exit loop**. Order *dispatch* still goes through the
existing serialized, freeze-chunked, marketable-limit path — only the decision logic is new.
Bot 4's fly is short-legged and could in principle have used the rule engine, but its
credit-decay and delta-drift exits are absent there too, so both bots share one loop rather
than splitting the exit story across two mechanisms.

### 2.2 The Strategy Group collision — a real hazard, guarded

`portfolio_pnl_engine.set_group_rule` is keyed on **`(stock_code, expiry_display)` alone**
(`_group_key`), and a rule sweeps *every* leg in that group. Bot 2 arms exactly such a rule
on NIFTY's expiry-day contract.

If a scalper opens a NIFTY position on an expiry for which any SG rule is live — Bot 2's, or
one the user armed manually from the PB/SL screen — that rule's P&L silently absorbs the
scalper's legs, and when it fires it squares them off. The scalper would see its position
vanish underneath it.

Both bots default to `trade_on_expiry_day: false` (§5.3), which avoids the Bot 2 case by
construction. That stops being true the moment the flag is flipped, and it never covered
manually armed rules. So the guard is explicit and unconditional:

> **A scalper refuses to open a cycle when a live SG group rule exists for its
> `(index, expiry)`.** Logged as `sg_rule_conflict`, a skip and not a failure.

Checked at cycle entry, not session start — a rule can be armed mid-session from the UI. The
scalper never arms an SG rule of its own, so it cannot create the collision in the other
direction.

---

## 3. Bot 3 — NIFTY Momentum Long Scalper

**`bot_type: "momentum_long_scalper"`.** Buys a single ATM option on an underlying momentum
signal, manages it with a trailing ladder, and repeats. Long only; risk per trade is capped
at the premium paid.

### 3.1 Universe

NIFTY only, NFO. Nearest weekly expiry from the scrip master (never a weekday rule — same
reasoning as `docs/bots-mvp-plan.md` §4). SENSEX/BFO is additive later through Bot 2's
existing `INDEX_EXCHANGE` map and is deliberately not in this build.

### 3.2 Signal — the underlying, never the option

Evaluated on **1-minute NIFTY futures candles**, adopted from the source conversation:

```
bullish := close > EMA(9) AND close > session VWAP AND volume > MA(20, volume) x 1.5
bearish := close < EMA(9) AND close < session VWAP AND volume > MA(20, volume) x 1.5
```

`bullish` buys the ATM **CE**, `bearish` the ATM **PE**. No trade otherwise. A signal that
fires while a position is open is **ignored, not queued** — see §3.4.

**Futures, not the index, and this is not a preference.** The NIFTY *index* has no traded
volume, so both the volume filter and VWAP are unimplementable against it. The futures
contract is the only source of either. This is a correction to the source specification,
which says "NIFTY spot" throughout while also requiring a volume surge.

**Candle construction.** Built in-process from raw futures ticks
(`register_raw_tick_listener`, which fires *before* any parsing — so a futures tick reaches us
even though `parse_icici_tick` requires a strike and cannot identify one), not polled.
Bars bucket on **arrival time, not `ltt`**: the captures show `ltt` arriving as
`"Mon Jun 29 09:59:59 2026"` — the SDK's `strftime('%c')`, a locale-formatted local-time
string — while `MockBreezeSdk` emits a bare int. Two types for one field, neither reliable;
WebSocket latency is far below a one-minute bar. Polling `get_historical_data_v2` every minute
would cost ~375 REST calls a day against an account budget the whole dashboard shares, to
obtain data the WS feed is already delivering free.

**Warm-up.** `MA(20, volume)` needs 20 candles and `EMA(9)` needs nine; VWAP needs none
(read from `avgPrice`, below). The build took **no historical backfill** — candles are
built from live ticks only — and instead the feed runs for the **whole session from 09:15,
regardless of whether either scalper is armed** (§5.6). So the EMA and volume MA are warm
by ~09:35, which is also the earliest a session window may start (§5.2), and a bot armed
at any later point in the day is immediately ready rather than blind for 20 minutes. Total
REST cost of the entire signal path: **zero** — the WS feed delivers all of it.
*(This supersedes the earlier plan of one `get_historical_data_v2` backfill call at session
start; with the feed always running from the open it is not needed.)*

**Verified 2026-09-06 against real captures, not assumed.** `ws_tick_normalize.normalize_tick_cell`
keeps OI and order-book depth (`totalBuyQt` / `totalSellQ`) but **not** traded volume — the
chain UI never needed it. The raw tick does carry it, confirmed two ways: `breeze_connect`'s
own parser maps `ttq` (cumulative traded quantity) and `ttv` (cumulative traded value) for
every `len(data)==23` NSE F&O tick, and the captures in `tests/fixtures/icici_ticks/` show
both populated.

Volume per candle is `ttq` **differenced**, never `ltq` summed. The pipeline drops ticks on a
full queue by design (`ws_tick_pipeline._dropped_ticks`); a summing builder loses that
quantity permanently, while differencing recovers on the next tick because it carries the
whole day's total. Two tests pin the exact behaviour: a mid-bucket drop changes nothing at
all (only the last reading per bucket is used), and losing a bucket's *final* reading shifts
volume into the next bar, which then agrees again immediately.

**VWAP comes from `avgPrice`, cross-checked against `ttv`/`ttq`.** The captures establish the
identity:

| capture | `ttv` | `ttq` | `ttv`×1e7 / `ttq` | `avgPrice` |
|---|---|---|---|---|
| nifty_call_24000 | `"4473.68C"` | 505,558,040 | 88.48994 | 88.49 |
| nifty_call_25000 | `"10.08C"` | 77,532,130 | 1.30011 | 1.3 |

`ttv` is a **string in crores with a unit suffix**, and `ttv`×1e7/`ttq` is exactly the tick's
own `avgPrice`. Because both counters run from the exchange's session open, that value *is*
the session VWAP — so **VWAP needs no warm-up at all**, and only the EMA and volume MA do.

`avgPrice` is primary because it is a plain float in every capture, while `ttv`'s suffix
vocabulary is unknown (`C` is the only one observed). Making a string suffix load-bearing
would mean an unrecognised unit leaves the bot unable to compute VWAP and refusing to trade
for a whole session. `ttv`/`ttq` is still parsed and compared every tick, and a divergence
past `VWAP_CROSS_CHECK_TOLERANCE` is logged once and surfaced in `warmup_status`, so a
broker-side change to either is caught rather than silently trusted. An *unrecognised suffix*
disables the cross-check only — never the bot.

*(This supersedes the original three-way fallback here, which assumed the field names were
unverified and offered `get_historical_data_v2` polling as a last resort. That path is not
needed and its ~375 calls/day are not in the API arithmetic.)*

**Available but not used in this build:** `total_buy_qty` / `total_sell_qty` are already
normalized on every option tick, so the source conversation's order-book-imbalance filter is
obtainable for free. Left out of v1 to keep one signal under test rather than two.

### 3.3 Sizing — fixed rupee outlay, one position at a time

```
lots = floor(premium_outlay_inr / (ask_premium x lot_size))
```

One lot minimum; below that the signal is skipped and logged (`outlay_below_one_lot`). Lot
size comes from the scrip master, never a constant — the source conversation says 65, which
is not the current NIFTY lot size.

**Exactly one open position at any instant.** The outlay is therefore the maximum capital at
risk at any moment, and the daily cumulative stop is the only other brake. This is what makes
the trailing ladder's absolute-point parameters (§3.5) tolerable: with one position of known
size, "+10 points" has one unambiguous rupee meaning at a time.

*Accepted trade-off:* absolute points behave differently across premium levels — 10 points is
a 9% move on a ₹110 option and 33% on a ₹30 one. Put to the user, who chose absolute points
over percentage-of-premium. Paper mode (§7) reports both so the choice can be revisited from
evidence.

### 3.4 One position at a time — what the bot does with a signal it cannot take

Ignored, and logged at debug rather than as a cycle. Queueing it would mean entering on a
signal that has already aged past its own one-minute candle, which is the opposite of the
strategy.

**One trade per signal run (decided 2026-09-13).** The signal is a *state*, not an event:
close above EMA and VWAP on volume stays true minute after minute. So "the next qualifying
candle after the position closes" used to mean the very next pass — a stopped-out position
was re-bought two seconds later on the same reading. The 10–11 Sep paper days did this seven
times: one winner, **−₹3,706 net**, against +₹5,406 for the two days overall.

The rule: after an entry, the bot may enter again only once the signal has **switched off**
— a completed candle that decisively did not fire that side (volume below threshold, no
confluence, or the opposite side) — **and then fired again**.

- A candle that went off *while the position was still held* counts. The 11 Sep 13:55
  re-entry (+₹1,769) came after the signal lapsed mid-hold, and is exactly the trade to keep.
  Requiring the lapse *after* the exit would have blocked it.
- A data gap (`not_enough_candles`, `vwap_unavailable`, …) does not end a run: not knowing
  whether the signal fired is not evidence that it stopped.
- The last entry's signal candle and side come from the cycle rows
  (`scalper_day_totals.last_entry_candle_start` / `last_entry_side`), so the rule survives a
  restart. Candles are in-memory, so after a restart the run is held until the rebuilt history
  shows an off-candle — the conservative reading.
- Each past candle is replayed with its **own** VWAP (`Candle.vwap`, recorded at the bar's
  close), so the verdict is what the signal read at that minute.
- An aborted entry (limit never filled) consumes the run too: re-bidding the same run on the
  next minute is the chase §3.6 rules out.
- Held passes are logged as `signal_not_fresh`. The backtest applies the same rule
  (`skipped_same_signal`), or it would describe a different strategy.

The user chose this over a fixed post-exit timer (a 300-second cooldown was proposed by an
external review): a timer guesses how long a run lasts, while this rule reads when it ends.

### 3.5 Exits — the three-level trailing ladder

Evaluated on every coalesced tick of the held contract, priced off **bid** (what the position
can actually be sold at), never LTP:

| Level | Trigger | Action |
|---|---|---|
| Initial stop | −`stop_loss_pts` | Exit |
| Time invalidation | held `time_invalidation_seconds` without reaching +`time_invalidation_min_move_pts` | Exit |
| Level 1 | +`level_1_trigger_pts` | Move stop to cost + `level_1_lock_pts` |
| Level 2 | +`level_2_trigger_pts` | Move stop to +`level_2_lock_pts` |
| Level 3 | above +`target_pts` | Trail `level_3_runner_step_pts` behind the running peak |
| Hard square-off | `hard_square_off_ist` | Exit regardless |

The ladder never moves a stop backwards. Level 3 is why `target_pts` is a *trailing trigger*
rather than a take-profit: reaching it starts the runner instead of closing the trade, which
is the "ride the successful trades" behaviour the user asked for.

Every exit is a **marketable limit**, mirroring `squareoff_dispatcher._leg_limit_price` — a
sell priced a configured band below LTP, escalating on retry, never a raw market order.

*Known degeneracy, inherited from `docs/bots-mvp-plan.md` §4:* on a very cheap option the
band falls below the ₹0.05 tick and the limit stops meaning anything. Accept and document; do
not silently widen it.

### 3.6 Entry execution

Buy side, bounded limit at the ask plus a tolerance — the mirror of
`placement.sell_limit_price`, and the reason `placement.py` needs a `place_long_legs`
sibling rather than an `action` parameter bolted onto the existing function (the two have
different pricing directions and different failure semantics).

If the limit does not fill within `entry_fill_timeout_seconds`, cancel and re-price up to
`entry_retries` times, then **abort the cycle**. Chasing an unfilled entry is how a scalper
buys the top of the move it was trying to catch. An aborted entry is logged
(`entry_unfilled`) and is not a loss for consecutive-loss purposes — nothing was traded.

---

## 4. Bot 4 — ATM Iron Fly Scalper

**`bot_type: "iron_fly_scalper"`.** Opens a delta-neutral ATM iron fly, books it when the
collected credit has decayed by a configured share, waits for a re-entry condition, and
repeats.

### 4.1 Structure

```
Leg 1  SELL  ATM CE
Leg 2  SELL  ATM PE
Leg 3  BUY   CE at ATM + wing_width_points
Leg 4  BUY   PE at ATM - wing_width_points
```

`wing_width_points` defaults to **150**, the source conversation's stated sweet spot for
NIFTY: narrower collapses the net credit, wider adds max loss without buying further margin
relief. An optional VIX rule (`wing_width_vix_rule`, **off by default**) widens to a second
value above a VIX threshold.

Strikes snap to the nearest available strike from the chain; the wings snap **outward**, so
the realised width is never narrower than configured. Narrower wings mean both less credit
and more margin, so an inward snap would degrade the trade on both axes at once.

### 4.2 Sizing — largest fly under a rupee margin ceiling

The user set a **rupee margin ceiling**, not a lot count and not a percentage of free margin.
The bot takes the largest whole-lot fly fitting under it.

The requirement is **verified through `margin_calculator` in a single call carrying all four
legs**, never estimated per leg and summed. This is the same reasoning as
`docs/bots-mvp-plan.md` §9.2: pricing legs separately discards the exchange's netting, which
on a hedged four-leg structure is most of the margin benefit — it would understate the number
of lots that fit by a wide margin, or overstate the cost of the shape the netting exists to
reward.

Sizing walks down from the largest candidate lot count and takes the first that verifies
under the ceiling. If even one lot exceeds it, the cycle is skipped
(`margin_cap_too_small`). **Never partial-fund**, unchanged from `docs/bots-mvp-plan.md` §4.

A rupee ceiling was chosen over a percentage of free margin because position size then stays
stable across the day rather than drifting with unrealised P&L, and over a fixed lot count
because it adapts to VIX-driven margin changes without the user re-tuning it.

### 4.3 Entry sequencing — hedge first, and it is serialized

```
1. BUY  Leg 3 (OTM CE wing)
2. BUY  Leg 4 (OTM PE wing)
3. confirm both filled (order-notification WS, timeout entry_fill_timeout_seconds)
4. SELL Leg 1 (ATM CE)
5. SELL Leg 2 (ATM PE)
```

Selling first would present the broker with two naked short legs and draw a margin rejection
before the hedges exist. Non-negotiable.

**These four orders go out one at a time.** Design-decision #24 forbids concurrent broker
dispatch, and the reasoning applies with full force here: serialization is what makes a
throttle an unambiguous *refusal*, which is the only thing that makes a retry safe on an
order path. The cost is real and must not be hidden — **spot moves between leg 1 and leg 4**,
so the fly is not struck at a single instant.

Mitigations, in order:
* Wings first means the exposed interval carries the *cheap* legs.
* If a wing fails to fill inside its retry budget, **unwind whatever filled and abort**
  (`entry_unfilled`). A half-built fly is not a position this bot knows how to manage.
* If a short leg fails after the wings are on, the position is a long strangle — bounded
  risk, wrong trade. Close it immediately and log `entry_partial_unwound`.

### 4.4 Exits

Whichever fires first:

| Condition | Parameter |
|---|---|
| Net credit decayed by target share | `target_decay_pct` (default 15) |
| Position loss reaches the hard stop | `hard_stop_loss_inr` |
| Spot drifts from the entry ATM | `max_spot_drift_pct` (default 0.35) |
| Session window ends | `sessions[].end` |
| Hard square-off | `hard_square_off_ist` |
| Bot's cumulative daily stop | §6.1 |

Credit decay is computed from **live bid/ask on all four legs** — buy-back cost measured at
the ask for shorts and the bid for longs, i.e. what unwinding would actually cost, not a
mid-price fiction that flatters the target.

The drift stop is the one that matters. The source conversation's reasoning is adopted whole:
by the time a short ATM leg has moved 0.35% against you, gamma is expanding on the tested
side and waiting for the rupee stop is waiting for a number that arrives faster than the
exit can be placed.

**Exit sequencing is the reverse of entry**: buy back the two shorts first, then sell the
wings. Selling the hedges while shorts are still open inverts the margin mid-unwind — the
same rejection risk as entry, in the other direction.

### 4.5 Re-entry gate

After booking, **both** conditions must hold before the next cycle:

```
minutes_since_exit >= reentry_cooldown_minutes            (default 15)
AND spot range over the last reentry_range_window_minutes (default 10)
      <= reentry_max_range_pct                            (default 0.15%)
```

The cooldown alone would re-center into an ongoing move and get stopped again — the
characteristic way a short-premium strategy bleeds. The range test alone can re-fire
immediately in a chop that keeps clearing the band. Requiring both means the bot re-enters
only when the market has been quiet *and* some time has passed since the last thing that went
wrong.

Spot range is measured off `index_spot_feed`'s existing tick stream. No extra subscription.

### 4.6 What the build changed (2026-09-06)

**Two loss stops, both live, the tighter binding.** The design gave the fly a percentage
profit target and a *flat rupee* loss stop. That asymmetry misbehaves as lot count changes:
at three lots a flat ₹1,500 is ₹500 a lot, roughly seven points of combined premium, which
ordinary intraday movement clears. Both forms are now configurable and both are evaluated —
`hard_stop_loss_inr` and `stop_loss_credit_pct` (default 20%) — with the tighter one binding
and either settable to None to switch it off. The percentage form is the idiom Bot 2's
`loss_limit_premium_multiple` already uses.

> **Note on the shipped defaults:** at ₹1,500 flat and 20% of credit, the flat stop still
> dominates from about two lots upwards (20% of a two-lot credit is roughly ₹3,000). Raising
> or disabling `hard_stop_loss_inr` is what lets the percentage stop actually govern. Left as
> shipped rather than changed unilaterally.

**Superseded 2026-09-13, from the 10–11 Sep paper days.** The ₹1,00,000 default margin
ceiling bought **~13 lots** (every paper-P&L step was ₹42.25 = one ₹0.05 tick × 845 units).
Each fly opened about **1–1.3 points down** on the four legs' bid-ask spread alone, so the
flat ₹1,500 stop — under two points at that size — fired on noise: flies were stopped 14 and
37 seconds after entry, and the seven flies lost ₹13,750 between them without the strategy
ever being tested. The one fly that survived its opening dip was at +₹1,817 when its window
closed.

- `margin_ceiling_inr` now defaults to **₹25,000** (about three lots) and `hard_stop_loss_inr`
  to **None**, so the 20%-of-credit stop and the 0.35% drift stop govern. Proposed after the
  paper-log review and accepted by the user.
- Stored configs are full dumps, so `bots_migrate._correct_superseded_bot_defaults` rewrites
  those two fields **only where they still hold the exact old default**, as the charges
  migration does. A value the user chose is left alone.
- **Holding horizon:** a fly earns its entry spread back only after tens of minutes of calm
  (~3 points an hour on the 10 Sep survivor), so it is meant to hold to the end of its window.
  Nothing in the exit path closes it early except the target, the stops and the drift stop.

**Bot 2's margin helper could not be reused.** `expiry_index_writer.margin_for_legs` hardcodes
`action: SELL` on every leg, because Bot 2's shapes are short-only. An iron fly has two BUY
legs whose entire purpose is to *reduce* margin; sending them as sells prices the structure as
a short strangle plus two more shorts and returns a number several times too high, which would
then refuse every lot count against the ceiling. `scalping/margin.py` is the action-aware
sibling, and a test asserts the payload carries two BUYs and two SELLs.

Also settled while building:

* **An unquoted wing skips the cycle**, logged. A fly whose protective leg cannot be priced
  is a short straddle in disguise.
* **Sizing costs at most three `margin_calculator` calls.** Margin for N lots of one structure
  is very nearly N × the one-lot figure, so one call sizes and one verifies; the walk steps
  down rather than trusting the estimate outright.
* **The re-entry range test reads the futures candles the signal already builds**, rather than
  a second spot buffer. Over a ten-minute window the futures basis is stable, so the range is
  the same shape and costs nothing extra.
* **VIX is only fetched when the widening rule is switched on.** It ships off, and a bot not
  using the rule must not pay for a quote every pass.

---

## 5. Shared runtime

### 5.1 The scalper loop

A **daemon thread** in the API process, alongside the existing bot scheduler, portal
heartbeat and reference-data scheduler — it needs the broker session cache, the WS feed and
the P&L snapshot, all of which live there.

*Corrected 2026-09-06.* This section, and `docs/bots-mvp-plan.md` §5, both said "asyncio
task". The code disagrees and is right: `bots/scheduler.py` and `reference_data/scheduler.py`
are both daemon threads, and the tick source is a socket-thread callback with no async I/O
anywhere on this path.

**Cadence is the user's existing PB/SL recompute interval** (Settings → Advanced, 1–30s,
default 2s), read fresh every pass so it stays live-adjustable — the same value
`portfolio_pnl_engine` uses. Reusing it rather than inventing a second timer means there is
one latency knob for everything that watches an open position: a user who tightens it for
their stop-losses tightens it for the bots by the same act, and cannot end up with a P&L
engine and a bot disagreeing about how fast "now" is.

Structure, deliberately mirroring the existing bots' split so the same testing approach
applies: a **pure decision layer** (`decide()`-style, no I/O, fully unit-testable — given
candles, quotes, position state and config, return an action) and a thin **driver** that
subscribes to ticks, calls the decision layer, and dispatches orders through the serialized
path. Every judgement lives in the pure half.

Both bots run in the same task. They are independent in policy (§5.2) but share one tick
subscription and one API budget view.

### 5.2 The two bots are independent

Confirmed with the user: **fully independent windows, separate cumulative stops, separate
budgets.** Not the source conversation's mutually-exclusive regime schedule.

Each bot has its own `sessions` list, and they may overlap and hold positions simultaneously.
The shipped defaults reproduce the regime schedule anyway — Bot 3 morning and afternoon, Bot 4
mid-day — so the out-of-the-box behaviour matches the consultation while the mechanism does
not hard-code it.

Both lists are editable from the bot's **Schedule** tab: one to four windows each, plus
`hard_square_off_ist`, which is the hard backstop rather than a window bound. Four rules are
enforced in `validate_session_windows` (the pydantic model, so a direct PATCH is refused too)
and mirrored in `frontend/src/lib/scalper-sessions.ts` so the drawer can disable Save and say
why:

* **No window may start before 09:35.** Indicators are built from live ticks with no
  historical backfill, so an earlier window can only log `not_warm`. The floor is fixed
  rather than derived from the signal periods, which are themselves editable: deriving it
  would let an edit on the Signal tab invalidate a window saved on the Schedule tab, and an
  extreme-but-legal signal (200 bars of 300s) would make every window unsaveable. The UI
  warns in that case instead of blocking. Because the feed runs from the 09:15 open
  regardless of bot state (§5.6), a window at the 09:35 floor is genuinely warm on its
  first minute — the floor is a real guarantee now, not merely the point past which
  warm-up *might* have finished had the bot been armed at open.
* **No window may end after `hard_square_off_ist`**, or the bot opens a position and flattens
  it on the next pass, paying a round trip of friction -- the binding constraint -- for a
  trade it never had a chance to hold.
* **No window may run past the 15:30 close.**
* **No two of one bot's own windows may overlap.** `in_window` returns the first match, so a
  second overlapping window is unreachable. Overlaps *between* the two bots remain fine and
  are the point of this section.

Windows that merely abut (`11:30-13:00` after `09:35-11:30`) are legal and not an overlap:
`in_window` is `start <= now < end`.

> **Stated plainly, because it is the cost of this choice:** with separate stops, total daily
> downside is the **sum** of the two caps, not the number set on either. Two ₹10,000 stops is
> a ₹20,000 day. This is surfaced on the settings drawer as a combined figure rather than left
> for the user to multiply.

Both bots participate in the §9.4 cross-bot priority sweep and declare committed margin, so
Bots 1 and 2 size against what is actually left.

### 5.3 Expiry day

`trade_on_expiry_day` on both configs, **both shipped off**. Turning it on for either bot
brings the §2.2 Strategy Group guard into play as the real protection rather than a
theoretical one.

### 5.4 Session, license, market state

* **No broker session at window start** → the bot logs `no_broker_session` and keeps
  checking until the window ends. It does **not** open a second nag path: the existing
  `squareoff_watch` → portal → Telegram route already covers this, including a powered-off
  deployment (`docs/bots-mvp-plan.md` §4).
* **Read-only license mode** disarms both bots and says so on the card. Not a failure state.
  Checked explicitly, not via the HTTP dependency — there is no request on this path
  (`docs/bots-mvp-plan.md` §10.4).
* **Market closed / holiday** → `market_calendar`, skip.
* **Hard square-off at `hard_square_off_ist`** (default 15:15) closes everything regardless
  of P&L, before the exchange's own cutoff leaves a position to be closed at a price nobody
  chose.

---

## 5.5 The gate stack's ordering rule (established while building)

Exits are evaluated before entries, and **a gate that blocks entering never blocks leaving**.

Read-only mode, an expiry day, a closed session window, an active cooldown and an exhausted
API budget are all reasons not to *open* a position. None of them is a reason to abandon one
that is already open with a stop running against it — a stack that checked them uniformly
would strand a live position, unmanaged, the moment a licence lapsed or the clock left a
window. There is a test that walks every entry gate against an open position and asserts none
of them strands it.

Within the exit path the order is **obligations before opinions**: hard square-off, then the
daily stop, then a sustained stale feed, then the window (Bot 4 only), and only then the
position layer's own verdict. So a ladder that has not fired yet cannot keep a position past
its square-off.

Two consequences worth recording:

* **Ladder state is persisted when the stop moves, not when the peak does.** The peak updates
  on every new high; only the stop changes what the position will do. A handful of local
  SQLite writes per cycle buys an exact resume across the container recreation that every
  portal upgrade performs.
* **A stale feed freezes entries at ~10s but only exits after 60s.** Flattening on a blip
  costs a round trip of friction — the binding constraint of §6.4 — while a sustained
  blackout means the stop is not being evaluated against anything at all.

---

## 5.6 The candle feed runs the whole session, not only when a bot is armed (2026-09-07)

**Decision.** The NIFTY futures candle feed is serviced on every loop pass during market
hours on a trading day, whenever the deployment has a broker session — **regardless of
whether either scalper is `Off`, `Paper` or `Live`**. Candles start building at the 09:15
open and never stop for the session.

**Why this changed.** As first built, `runtime.tick()` only calls `_ensure_feed()` when it
finds at least one *enabled* scalper in `list_enabled_bots` — the feed is a side effect of
iterating armed bots. That is wrong for the case that matters most: a user who decides at
11:00 to turn Bot 3 on. With the old behaviour the feed subscribes at 11:00, the EMA and
volume MA then need ~20 live minutes, and the bot is blind until ~11:20 — through exactly
the move that prompted the user to arm it. The 09:35 window floor (§5.2) silently assumed
the bot was armed at open; armed mid-day, it bought nothing.

Running the feed from 09:15 unconditionally closes that gap: any window at or after the
09:35 floor is warm on its first minute, and a bot armed at any later point is immediately
ready rather than blind for its first 20 minutes.

**Scope, stated so it is not over-read:**

* **Only the futures candle feed is always-on.** Option-chain quote subscriptions, the
  margin call, the VIX read (§5 `_current_vix`) and everything else stay lazy — paid for
  only when a bot is actually evaluating or holding. The always-on cost is *one* WS
  subscription to *one* contract, which is the same order of cost as `index_spot_feed`,
  already always-on.
* **It runs even in read-only licence mode.** Building candles is reading data, not
  trading. Keeping the indicators warm means a licence restored at 13:00 leaves the bot
  ready to trade its afternoon window immediately, instead of standing down on `not_warm`
  until 13:20. The read-only gate still blocks every entry (§5.5) — nothing is placed —
  it just no longer also throws away the warm-up.
* **It still needs a broker session.** No ICICI login → nothing to subscribe with; the
  feed retries on its existing `FEED_RETRY_SECONDS` cadence and logs `no_broker_session`,
  exactly as today. The change is *when we start trying* (09:15, always), not the
  subscription mechanism.
* **The 09:35 window floor stays.** This decision makes that floor a genuine guarantee; it
  does not lower it. Windows still may not start before 09:35 (20 live 1-minute bars from
  the 09:15 open), and the UI still warns rather than blocks for an extreme-but-legal
  signal period (§5.2).

**Implementation note.** The feed servicing needs a `user_id` to subscribe with, and in
the all-`Off` case `list_enabled_bots` yields none. Resolve it from the persisted broker
session instead — the deployment's trading identity, the same one the portal login-notify
and `squareoff_watch` already assume is singular (`docs/bots-mvp-plan.md` §5 — one trader
per instance); a small `broker_session` helper to name the user(s) with a live token is
all that is missing. `tick()` resolves that identity and services the feed before the
enabled-bot loop; the decision loop itself still only touches enabled bots. A session run
is **not** opened for a bot that is merely `Off` — `warmup_status` has no run to write into
until a bot is armed, and nothing reads it before then.

---

## 6. Risk and circuit breakers

### 6.1 Cumulative daily stop — per bot

Tracked continuously as **realized + unrealized + estimated charges**, not realized alone. A
realized-only cap would let the bot sit deep underwater and keep opening cycles.

On breach: close open positions via the marketable-limit path, cancel working orders, mark
the bot `terminated_for_day`, and refuse further order calls until the next trading day.
Terminal — a cooldown does not release it.

### 6.2 Consecutive-loss cooldown

`consecutive_loss_limit` losing cycles in a row (default 3) pauses **that bot** for
`cooldown_minutes` (default 30). Targets the choppy regime where a scalper bleeds by
repetition rather than by any single bad trade. An aborted entry (§3.6) is not a loss.

### 6.3 API budget guard

The user chose this **instead of** a static max-cycles-per-day cap, and it is the better
mechanism: it responds to what the account is actually spending rather than to a number
guessed in advance.

Before opening any cycle, the bot reads its consumption against
`icici_api_pacing`'s rolling window and stands down when the remaining budget falls under
`api_budget_reserve_calls`. Scalping can then never starve the dashboard, Bot 1, Bot 2, or —
the one that matters — a manual square-off.

The arithmetic, so the reserve is set from something:

| | Calls per cycle | 20 cycles/day |
|---|---|---|
| Bot 3 | 2 orders + up to 4 retry calls ≈ **4–6** | ~120 |
| Bot 4 | 8 orders + 1 margin_calculator + retries ≈ **12–20** | ~320 |

Against ~90/min and ~5,000/day this is comfortable, which is why a static cycle cap is not
needed for API reasons. The per-minute ceiling is the tighter of the two and is never
approached, because a four-leg entry with retries spends ~15 calls over several seconds and
they are serialized anyway.

### 6.4 The constraint that actually binds is friction, not API budget

Worth stating precisely because it is counter-intuitive and because it is what the API guard
does *not* protect against.

Bot 3 with a 90-second time stop, one position at a time, could attempt **~80 cycles** in a
two-hour window. That is fine for the API budget (~480 calls) and catastrophic for the P&L:
at ~₹100 of round-trip friction per cycle, **80 cycles burn ~₹8,000 of friction against a
₹10,000 daily stop**. The bot would hit its stop having been roughly flat on the trades.

So: **cumulative friction is a first-class, displayed number** — in the run log, on the bot
card, and in paper mode's session summary. If paper mode shows friction consuming the day,
`max_cycles_per_day` is a one-field addition and the place it plugs in is already the same
guard as §6.3. It is deliberately not shipped in v1 because the user declined it and paper
mode will produce the evidence either way.

### 6.5 What the build changed (2026-09-06)

**A PB/SL rule armed mid-position is disarmed, and the user is told.** Decided by the user.
When a scalper holds a position and a Strategy Group rule appears on the same
`(stock_code, expiry)`, the bot clears the rule and sends a Telegram alert asking the user to
stop the bot first if they want PB/SL on that expiry. The alternative — leaving it armed —
means the rule squares off the bot's legs at a moment and price of its own choosing, after
which the bot manages a position that no longer exists.

> **The hazard this accepts, stated because it is not obvious:** a group rule covers the whole
> `(stock_code, expiry)` group, not just the bot's legs. If the user holds *other* positions on
> that expiry — a Strategy Builder trade, say — disarming the rule removes their stop too. The
> alert therefore counts and names those legs (`other_legs`) and says in as many words that
> they are now unprotected. A test asserts that wording.

Note the asymmetry, which is deliberate: with **no** position open a conflicting rule merely
*blocks entry* and is left alone. It is the user's rule and it is doing no harm. Only a rule
that would sweep a live bot position is disarmed.

**Breaching the daily stop now disables the bot.** `enabled` is set to false and a Telegram
alert says so; it will not trade again until re-enabled by hand. Disarming happens strictly
*after* the executor has run, so the position is closed first — switching a bot off while it
still holds something would leave that position unmanaged, and there is a test for exactly
that ordering. The cost is real: one bad day stops the bot every subsequent day until noticed,
which is why the run log entry and the alert both exist.

**The daily stop now includes unrealized P&L**, signed per structure — a long option's mark
above entry is profit, while an iron fly's `entry_value` is credit *received* and its mark is
the cost to buy back. Realized alone would let a bot sit deep underwater on an open position
and keep opening more. An unpriceable position contributes zero rather than raising, so the
durable realized half keeps working when a quote is missing.

#### Two bugs this step exposed

1. **An ordinary trading day was being logged as a crash.** `reap_stale_runs(older_than_minutes=30)`
   runs on every bots-scheduler tick, so when a scalper session's heartbeat stopped at end of
   day the row was marked `failed` / "Interrupted before it finished" about half an hour
   later. Sessions are now finalised — status `completed`, with a summary of cycles, net P&L
   and friction — once the last window has closed, the square-off has passed and nothing is
   open.
2. **Finalising then created a new session row on every subsequent pass.** `open_session_run`
   matched only `running` rows, so once a day was finalised each later tick minted a fresh
   one, which was then never finalised and was itself reaped as interrupted. It now matches
   `running` *or* `completed` for today. `failed` deliberately still does not match: that is a
   run the reaper closed because the process died, the interruption is a record worth keeping,
   and what follows it is a genuinely new session.

---

## 7. Paper mode

`mode: "paper" | "live"` per bot, **shipped as `paper`**. Arming a bot that fires unattended
orders on a momentum signal should be a deliberate act, matching the reasoning that made
`approval_mode` default to `telegram` for Bot 2.

In paper mode the bot runs the complete signal, sizing, sequencing and exit logic against
**live WS prices on the production instance**, and places nothing. Simulated fills are
recorded at the price a real order would have been marketable at — buys at the ask, sells at
the bid, plus the full modelled friction stack (brokerage, STT, exchange fees, GST, stamp) —
never at mid.

This is the only way to see a full session's decisions before real money is on, given that
live broker calls work only from the production static IP and mock mode cannot produce
realistic fills. Paper cycles write to the same run log, flagged, so the UI is one surface.

Two things paper mode still cannot tell you, stated so they are not assumed away: whether a
limit would actually have filled at the touch, and the market impact of the order itself.

### 7.1 What was built (2026-09-06)

Fills are at the touch — a buy at the ask, a sell at the bid — plus
`slippage_spread_fraction` of the bid-ask spread as **adverse slippage on each leg**, then
the charges stack. Filling at the touch is the optimistic half; the slippage term is the
honest correction for the part that *is* knowable, that a real order rarely gets the screen
price, without pretending to model a book we cannot see.

Slippage lives **inside the fill prices**, so it shows up in `gross_pnl`. It is deliberately
not added to `friction` as well — doing so would count it twice and make every cycle read
worse than the prices actually recorded. `friction` is charges only.

A sell is floored at one tick rather than allowed to reach zero: a near-worthless option
still sells for something, and a negative fill price would invent profit on the exit of a
losing trade.

### 7.2 The charges model, and a warning about its numbers

`trading_charges` is a **singleton settings row used by every bot** and by the backtest — no
two may disagree about what a trade costs, and the backtest must price a trade identically to
paper mode or the two describe different strategies. Brokerage is flat ₹20 per order
(confirmed against the note); STT falls on the sell leg only, stamp duty on the buy leg only,
and GST applies to brokerage and exchange fees but **not** to STT or stamp duty, which are
taxes rather than services.

Edited in exactly one place: **Settings › Trading Costs**. Renamed from `scalping_charges` and
moved out of `services/bots/scalping/` on 2026-09-07, when it stopped being scalper-specific.
The migration **renames** the existing table rather than creating a new one — a
create-and-abandon would leave a deployment's corrected rates in an orphaned table while the
app read defaults from an empty one, silently reverting a number the user had fixed. The
scalper drawers lost their Costs tab and carry a pointer instead: two editors for one record
is how they drift.

**The writer bots now quote net.** Bots 1 and 2 reported gross premium and had no cost model
at all. `services/bots/net_premium.py` annotates every proposal leg with `estimated_charges`
and `net_premium_total`, applied inside `repo.create_proposal` — the one choke point every
proposal passes through, so the app scan, the Telegram message and a reprice can never
disagree. Two stated limits: it prices the **entry leg only** (whether a written option is
bought back or left to expire is not knowable at proposal time, and charging a round trip
would overstate a leg that expires worthless), and freeze-quantity chunking multiplies the
per-order brokerage only when the caller knows the order count. On a thin Bot 2 leg the
charges exceed **5% of the premium**, which is why this was a real gap rather than a cosmetic
one.

> **Calibrated against a real contract note, 2026-09-06.** The user supplied an ICICI F&O
> order book — 140 fills across NSE and BSE, 2026-08-03 to 2026-09-03 — and the rates were
> fitted to it rather than taken from published summaries. **Three of the shipped values were
> wrong.**

| | shipped from summaries | contract note says |
|---|---|---|
| STT (sell side) | 0.10% | **0.15%** |
| Exchange transaction | 0.0495% | **NSE 0.03545%, BSE 0.0325%** — and it is per-exchange |
| IPFT | 0.0005%, billed separately | **0** — already inside the exchange charge, so this was a double-count |

Confirmed correct: flat ₹20 brokerage (a single value across all 140 fills), STT on the sell
leg only, stamp duty on the buy leg only, SEBI at ₹10 per crore, and **GST = 18% × (brokerage
+ exchange + SEBI)** — matched to within ₹0.043 a fill, with the STT-inclusive hypothesis
rejected at ₹3.36.

`test_scalping_charges.py` now replays verbatim rows from that note through the model and
asserts both the total and each component. A rate change fails the test with the row that
disagrees. (The component-level assertion earned its keep immediately: it caught a row I had
transcribed as NSE when the contract was `OPT-SENSEX` on BSE.)

**The errors largely cancelled.** A one-lot round trip goes from ₹66.65 to **₹68.18** — higher
STT offset by much lower transaction charges and the removed IPFT. Section 6.4's arithmetic
holds; it was right for the wrong reasons.

**Existing rows are corrected once, and only where untouched.** A `CREATE TABLE` default
cannot reach a row that already exists, so a deployment that booted before the calibration
would have kept rates that were never right. The migration rewrites each superseded value
*only where the stored figure still matches it exactly* — anything else is a number somebody
chose, and is left alone. Idempotent by construction.

They remain editable, because they are still set by regulation and still change without
notice.

---

## 8. Backtest harness

### 8.1 Why the first harness could not replay real option prices (superseded by 8.7)

`ws_quote_snapshot` looks like a tick archive and is not. `record_cells` writes with
`hset(key, mapping=...)` keyed per contract per trading date, so each contract retains only
its **last** value for the day, at `WS_QUOTE_SNAPSHOT_RETENTION_DAYS` (default 5). It is a
warm-start cache. There is no historical option price series in this deployment.

That is true of this deployment's own storage, and it is not true of ICICI:
`get_historical_data_v2` accepts `product_type="options"` with a strike, right and expiry,
and returns traded bars at 1-second to daily intervals. Section 8.7 replays those.

### 8.2 What it does instead

Confirmed with the user: **model option prices from underlying history.**

```
get_historical_data_v2(NIFTY futures, 1-minute)   -> real candles, real history
       |
iv_compute.bs_price_call / bs_price_put + an IV assumption
       |
modelled bid-ask spread + the full friction stack
       |
replay the 3.2 signal and the 3.5 ladder
```

This tests the **signal honestly** over months of real history, and the **fills only
approximately**. That division is the whole point of the harness and must be stated wherever
its output is presented: a backtest showing edge has demonstrated that the *signal* has edge,
not that the *strategy* survives execution. Paper mode (§7) is what tests the second half.

IV comes from the ATM implied vol at the candle's timestamp where obtainable, and a
configured constant otherwise, with the choice recorded in the result so a run priced off a
constant is never mistaken for one priced off the surface.

### 8.3 Bot 4 in the backtest

The same machinery prices all four legs, so credit decay and the drift stop are replayable.
Margin is **not** — `margin_calculator` is a live broker call and has no historical
counterpart. Bot 4 backtests therefore report P&L and cycle counts but must not claim a
return-on-margin figure. Sizing in a backtest run is fixed at a stated lot count rather than
derived from the §4.2 ceiling.

Offline, not a service. Run from `backend/` like the test suite.

### 8.4 What was built (2026-09-06)

`scripts/scalping_backtest.py` with three subcommands, split because fetching needs a broker
and replaying does not — re-tuning a parameter must never re-spend API budget on candles
already pulled:

| | runs where | needs broker |
|---|---|---|
| `fetch` | the EC2 instance (static IP) | yes |
| `coverage` | anywhere | no |
| `replay` | the EC2, over SSH | no |

Both run on the box; there is no transfer step and nothing to keep in sync. The cache is a
standalone `backtest.sqlite3`, deliberately **not** `users.sqlite3` — bulk, regenerable data
must not be able to bloat or corrupt the file holding accounts, credentials and the run log.
Its primary key makes a re-fetch of an overlapping range idempotent, so a fetch that dies
halfway is resumed by running it again.

**IV comes from each day's real India VIX**, fetched alongside the candles through the call
`dashboard_vix` already makes. Far better than one constant across months: a high-volatility
week prices options high, which is exactly when the signal fires most and when a constant
would most mislead. A constant remains the fallback, and the result line always names which
was used.

**Expiry dates use a date-ranged weekday map**, holiday-shifted *backwards* (the exchange
brings an expiry forward to the previous trading day, it does not defer it). A single weekday
would misprice every option on one side of the change SEBI made.

**Spread is calibrated from live observation.** Paper mode now records observed bid-ask
spreads (throttled to one sample per contract per minute, stored *with the premium they were
seen at* — a rupee median across a ₹30 option and a ₹180 one describes neither). Until 200
samples exist the backtest uses a percentage-of-premium default and says so on every result
line, so an uncalibrated run is never mistaken for a calibrated one.

**A bug worth recording:** the first draft applied slippage on entry but not on exit, which
made every backtested cycle look better than the identical trade in paper mode. The two paths
have to agree or they cannot be believed together; there is now a test asserting slippage
moves entry and exit prices in opposite, adverse directions.

### 8.5 The affordability constraint the backtest immediately exposed

*(Written when NIFTY's lot was 75, and the tables below are priced at 75. The lot is 65 across
the whole history the replays accept (section 8.6), which makes every figure here about 13%
cheaper; the conclusions stand.)*

The first replay produced **zero cycles and 13 `outlay_below_one_lot` skips**. That is not a
bug — it is the strategy meeting NIFTY's 75 lot size.

Cost of **one ATM lot** at spot 24,000:

| DTE | VIX 11 | VIX 13 | VIX 16 | VIX 20 |
|---|---|---|---|---|
| 1 | ₹4,309 | ₹5,060 | ₹6,188 | ₹7,691 |
| 2 | ₹6,198 | ₹7,259 | ₹8,853 | ₹10,978 |
| 3 | ₹7,689 | ₹8,989 | ₹10,940 | ₹13,540 |
| 4 | ₹8,974 | ₹10,474 | ₹12,726 | ₹15,729 |
| 6 | ₹11,190 | ₹13,025 | ₹15,781 | ₹19,456 |

So a **₹10,000 outlay buys one lot only within about 3–4 days of expiry at normal
volatility, and only 1–2 days when VIX is elevated.** For the rest of the week the bot
correctly refuses and logs `outlay_below_one_lot` — a quiet, explicable, entirely inactive
bot.

The source conversation's ₹10,000 figure came with a stated ATM premium of ₹80–140 and a lot
size of **65**, i.e. ₹5,200–9,100 per lot. Both inputs have moved: the lot is 75, and ₹80–140
describes an option 1–2 days from expiry, not a Monday one. The number was generalised from
expiry week to every day.

**Settled 2026-09-06: the default outlay is now ₹25,000**, keeping the existing
`lots = floor(outlay / cost)` model.

The choice was put to the user with its consequence stated, because the outlay turns out to
govern *risk per trade* rather than affordability. Since an ATM option cheapens towards
expiry, a fixed outlay buys more lots the closer expiry gets:

| DTE | 1 lot cost | ₹10k outlay | ₹25k outlay | ₹50k outlay |
|---|---|---|---|---|
| 1 | ₹5,060 | 1 lot → ₹450 | 4 lots → ₹1,800 | 9 lots → ₹4,050 |
| 3 | ₹8,989 | 1 lot → ₹450 | 2 lots → ₹900 | 5 lots → ₹2,250 |
| 6 | ₹13,025 | — | 1 lot → ₹450 | 3 lots → ₹1,350 |

*(risk = lots × the 6-point initial stop × 75 units)*

**Accepted trade-offs, chosen deliberately:**

1. **The largest positions sit closest to expiry**, where gamma is highest and moves are most
   violent. Risk per stop-out swings from ₹450 at 6 DTE to ₹1,800 at 1 DTE.
2. **The consecutive-loss cooldown costs more near expiry.** Three losses at 1 DTE is ₹5,400
   — 54% of the ₹10,000 daily cap in a single cooldown cycle. (At a ₹50,000 outlay it would
   have been ₹12,150, blowing the daily cap *before* the cooldown could fire and making that
   breaker unreachable; that is why the figure is ₹25,000 and not higher.)

Mitigation, since the swing is accepted rather than removed: every cycle records
`risk_per_stop_inr`, so a four-lot expiry-day position is visible in the run log rather than
something the reader has to derive. Two tests pin the behaviour — that ₹25,000 is what makes
a full week tradeable, and that lot count rises towards expiry — so neither is rediscovered
later as a surprise.

**What is *not* a problem:** an ATM option's delta is ≈0.5 at any DTE, so 6 option points is
≈12 spot points whether the option is one day or six days out. The absolute-point ladder is
DTE-invariant and needed no change. Only the lot count varies.

Rejected: risk-based sizing (fix rupee risk per trade, outlay as a ceiling), which would have
held risk constant across the week; and buying OTM when ATM will not fit, which changes the
delta the ladder is calibrated against.

### 8.6 What the 2026-09-13 review found in the first harness

Reviewing it against the history API turned up six defects. Each would have made a run
silently wrong rather than visibly broken.

1. **`fetch` stored nothing.** The SDK refuses an NFO futures request with no `expiry_date`
   *before* it reaches ICICI and returns an error dict. The script read that as "0 bars" and
   carried on. Now every chunk passes its near-month contract and rolls at each monthly
   expiry, and a non-200 response is printed as an error, never stored as empty.
2. **Chunks were probably truncated.** Five days of 1-minute bars is 1,875 per call, against
   a cap believed to be about 1,000. Chunks are now `cap // 375` sessions. `probe` measures
   the cap, a response at the cap is flagged, `coverage` lists short days, and a re-run of
   `fetch` asks only for incomplete days.
3. **Lot size was hard-coded at 75.** It is 65 today. Contract facts now live in
   `backtest_regime.py`, dated.
4. **The fetcher was invisible to the rate limiter.** `icici_api_pacing` is per process, so
   the script's calls stacked on top of the API server's and could push the account past
   ICICI's ~100 a minute, throttling a live bot's exits. **Decided 2026-09-13:** every broker
   command refuses to run between 09:00 and 15:45 IST on a trading day.
5. **VIX came from a single call**, which ICICI truncates to about a month. It is now fetched
   in 28-day chunks.
6. **The script was not in the production image**, so none of this could run on the instance.
   The Dockerfile now copies `backend/scripts/`.

Two fidelity gaps were closed along the way. First, the replays now call the live gate stack
(`decide()`), so session windows, the hard square-off, the expiry-day rule, the daily loss cap
and the consecutive-loss cooldown apply; the first harness ignored all five. Second, the ATM
strike is read off the cash index, as the live bot reads it, not off the monthly future,
whose basis puts it a strike or two away.

**The history the replays accept starts at `HISTORY_START` (2026-01-01).** Decided
2026-09-13: backtest only the lot-size era the bots trade in today. The date is **unverified**.
As far as we know NIFTY's lot moved from 75 to 65 with the January 2026 contracts; confirm it
against the NSE circular and move the constant if it is wrong.

### 8.7 Real option prices

`backtest_options.py` has two pricers behind one interface. **Real** reads ICICI's traded
bars for the exact contract. **Model** is the Black-Scholes harness above, run with `--model`.
**They are never mixed in one run** (decided 2026-09-13). Traded prices are not bid/ask, so
the spread is still modelled from paper mode's observed samples.

**Fetching follows demand.** Pulling a blanket strike band would cost about 40 calls per
session, which is two days of ICICI's daily budget for a year of history. Instead:

1. `backfill` fetches the underlying: NIFTY futures (the Bot 3 signal), the cash index (spot
   and strikes) and VIX.
2. It replays on real prices. A contract the cache has never fetched **stops that day**, and
   every trade the day had made so far is discarded. Everything after an entry depends on how
   that entry played out, and a half day in the totals would be counted twice once it is
   replayed in full. The missing window is queued in `option_needs`.
3. It fetches the queued windows, logging each in `option_fetches`, empty answers included.
   Then it replays again, until nothing is missing or the call budget (`--max-calls`, default
   1,500, one call a second) is spent. Re-running it resumes.

Missing data means one of three things, and each is handled differently:

| state | meaning | what the replay does |
|---|---|---|
| `missing` | never fetched | stops the day; queues a need |
| `no_data` | fetched; ICICI returned nothing for the contract | skips the trade (`skipped_no_data`) |
| `no_trade` | the contract has data, but nothing traded that minute | no fill (`skipped_no_fill`) |

**Fills on real prices, Bot 3.**
- Inside each trade the replay uses **1-second bars** for 15 minutes: 900 bars, under the cap
  and far past the 90-second time stop. The entry is the first trade after a 2-second
  reaction delay, within the order's own fill timeout × attempts.
- If ICICI has no 1-second bars, it falls back to the next minute's open. Each cycle records
  its resolution (`1s`/`1m`).
- A 1-minute bar is walked open → low → high → close, adverse extreme first, so a stop and a
  target inside one bar resolve as the stop.

### 8.7a What ICICI's history was verified to serve (2026-09-15)

Twelve manual `get_historical_data_v2` calls settled every question `probe` was built to measure.
The responses are kept in `logs/Call *.json`.

| question | answer |
|---|---|
| Expired weekly options, NIFTY (NFO) and SENSEX (BFO) | served, at 1-minute and 1-second |
| Expired monthly futures | served |
| How far back | options reach 2026-01-05 at both intervals, so `HISTORY_START` is reachable |
| Rows per call | 1,000. A longer window is cut from the **start**: the latest rows come back |
| Request clock | IST. A 10:00–10:14 window read as IST returned those seconds; read as UTC it returned nothing |
| Bar timestamp | the minute's **start**. 118 of 122 live-built candles of 2026-09-15 matched on close, with a volume ratio of exactly 1.000 |
| Volume | quantity, not lots. Always 0 for an index |
| A minute with no trade | a flat bar with volume 0, not a missing bar. The replay's `NO_TRADE` test (`volume <= 0`) is right |
| 1-second bars | sparse: a row only where something printed. Some zero-volume rows move the price; the replay fills only on rows with volume |

Three findings changed the replay or the reading of it:

- **Futures carry bars the live feed never sees.** There are pre-open bars from 09:00 to 09:08
  (one with volume −650) and post-close bars to 15:39. The replays now split each day with
  `split_session`: only 09:15–15:29 becomes candles, as live. The pre-open bars feed VWAP only.
- **Session VWAP is rebuilt at each bar's OHLC average, pre-open included.** Against the live
  `avgPrice` VWAP over 122 candles it reads 0.98 pts off on average; the previous formula,
  typical price from 09:15, read 1.80. Neither ever put a close on the other side of VWAP from
  where the live bot saw it.
- **The closing auction is in the index history.** The index bars freeze at the 15:15 level
  through 15:19, then move with the indicative value from 15:20 to 15:29. SENSEX on 2026-08-27
  went from 77,214 down to 74,964 and back to 76,934. From 15:30 the bars freeze at the auction
  close. Options keep trading to 15:39. The data for replaying CAS Bingo's in-auction trigger
  therefore exists at 1-minute resolution.

Not in the history at all:
- order-book depth and best bid/ask, so the W-OBI signal and the flow challengers have only the
  forward shadow log;
- option bid/ask, so the spread stays modelled;
- margin.

BFO options carry no open interest (always 0). The start of the 65-lot era is still a question for
the NSE circular (8.6), not for ICICI.

### 8.8 Bot 4 and Bot 2

**Bot 4** (`backtest_fly.py`) replays the live entry and exit rules:
- the re-entry gate;
- `evaluate_exit`: drift first, then credit decay, then the loss stop;
- the window-end square-off.

How it runs:
- Fixed lots (`--lots`, default 3, about what the ₹25,000 ceiling buys), and no return on
  margin, per 8.3.
- Entry fills at the next minute's open, wings first.
- The position is marked on each minute's close. A leg that did not trade in a minute keeps
  its last price, as an LTP would.
- Slippage applies on every leg at entry and exit.

**Paper mode's fly close does not apply slippage** (`iron_fly_bot._close` prices the unwind at
the touch), so paper fly exits read slightly better than Bot 3's. The backtest does not copy
that. Flagged, not changed.

**Bot 2** (`bots/backtest_expiry.py`, docs/bots-mvp-plan.md section 4a) sells each shortlisted
strategy **side by side** at the bot's entry time and safety distance, at a fixed lot count.
Decided 2026-09-13: margin has no history, so there is no margin-yield ranking and no sizing
to a share of free margin.

Exits follow the armed SG rule:
- a loss stop at N × the premium collected;
- a price target every short leg must reach;
- otherwise held to expiry and settled at intrinsic value against the index close.

How minutes are judged:
- A single leg is checked at open, high, low and close, the adverse high first.
- A strangle is checked at open and close only, because its two legs peak at opposite moments.

SENSEX legs are charged at BSE rates and sized in 20-unit lots. ITM settlement is charged as
a buy at intrinsic value; the cost model has no exercise-STT line.

### 8.9 Validating against paper

`compare --bot momentum|fly --date D` reads that day's paper cycles from `bot_cycles` and
replays the same day on real prices, using the bot's *saved* settings. The fly is replayed at
the paper session's lot count.

It pairs trades on the same contract whose entries fall within two minutes of each other. The
median entry-price difference across those pairs measures the backtest's fill error directly.
Day totals are shown too, but they diverge for reasons that are not fill error: once the two
paths exit one trade at different moments, every later trade can differ.

If the saved settings' fingerprint differs from the one the paper session ran under, the
output warns. The 10–11 Sep sessions predate the 2026-09-13 default changes, so comparing
those days will warn.

### 8.10 Running it

The backtests need **a release that contains these modules and the script**. Then, on the
instance, outside market hours:

```
docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py probe
docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py backfill --bot momentum
docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py backfill --bot fly
docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py backfill --bot expiry --index NIFTY
docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py replay
docker exec -it breeze-core-engine python /app/backend/scripts/scalping_backtest.py compare --bot momentum --date 2026-09-11
```

Run `probe` first. If it reports that expired contracts are not served, only `--model` runs
are possible, and `backfill` should stop after the underlying.

The cache is `backend/data/backtest.sqlite3`, on the data volume, and git-ignored.

### 8.11 Bots → Backtest, the page (2026-09-13)

> **Superseded 2026-09-17** by design-decisions #35 and #36: the page is retired. A backtest now starts from the clock icon on each bot card, asks only for a period, replays on real ICICI prices only (no Black-Scholes path in the app), and is recorded as an Activity row with its trades and a run-scoped audit trail. The section below is kept as the history of how the engine was first exposed.

`/bots/backtest` runs the same engine from the app. It is linked from the Bots header. The
shared code is `services/bots/backtest_service.py`; the jobs are in `backtest_jobs.py`; the
routes are `route_bots_backtest.py` (static paths under `/bots/backtest/*`, enumerated in
`next.config.js` and both nginx confs). The command line still works and calls the same
service.

Decisions made with the user:
- **Placement:** its own page under Bots. The results are wide tables and a chart that do not
  fit a 22rem card.
- **Settings:** the bot's **saved** settings only. There is no per-run override; to try a
  variation, edit the bot and run again.
- **Every run is kept** in `backtest_runs` (backtest.sqlite3), carrying the config it ran on,
  its summary and its trades. Runs can be reopened, downloaded as CSV, or deleted. A run left
  `running` by a restart is marked failed on the next page load.
- **Fetching is still refused 09:00–15:45 IST** on trading days, and the check runs before
  *every* call, so a backfill started at 08:40 stops itself at 09:00. It now runs inside the
  API process through the app's own session, so the per-user limiter counts it and it is
  marked `advisory`. Serialized is not free, though: each call in flight delays a live order
  by one call's latency.
- **Fly and Bot 2 size from today's margin.** At run start, `price_lots` makes live
  `margin_calculator` calls for one lot at today's strikes:
  - the fly: one call on the 4-leg structure;
  - Bot 2: a free-margin read plus one call per shortlisted strategy.

  Lots follow from the saved ceiling (the fly) or from the saved share of free margin (Bot 2),
  and apply to every replayed day. The basis is recorded on the run. Those two bots therefore
  need the live broker; Bot 3 does not.
- **Bot 2 runs its saved shortlist** for each index enabled in its settings, strategies side
  by side. If no index is enabled, it refuses.

How it runs:
- One job at a time: probe, fetch or replay. The page polls every 3 s while a job runs. Fetch
  and probe can be stopped.
- A replay is pure CPU in a background thread of the API process: seconds to a minute on the
  instance.
- The page also compares a simulation day against the backtest (8.9); the fly is replayed at
  the simulation's lot count.

---

## 9. Persistence and the run log

Two new `BOT_TYPES` discriminators, the existing `bots` / `bot_runs` tables, and one new
child table. No `bot_proposals` involvement — neither bot proposes.

**One `bot_runs` row per session, N cycles beneath it.** The alternative — a run row per
cycle — would put 80 rows a day into a log designed to make a single no-trade day
explicable, and the existing `/bots` run history would become unreadable.

```
bot_cycles
  id, run_id, user_id, bot_type, cycle_no
  opened_at, closed_at
  side / structure, strikes, expiry, lots
  entry_price(s), exit_price(s)
  gross_pnl, friction, net_pnl
  exit_reason_code, exit_reason_text
  paper INTEGER NOT NULL DEFAULT 1
```

Every cycle carries its own reason code, on the same terms as `ReasonCode` in
`app/domain/bots.py`: persisted, asserted on in tests, added to freely, **never repurposed**.
New codes needed here include `sg_rule_conflict`, `entry_unfilled`, `entry_partial_unwound`,
`outlay_below_one_lot`, `signal_no_trade`, `cooldown_active`, `api_budget_low`,
`friction_cap`, `drift_stop`, `time_invalidation`, `trailing_stop`, `terminated_for_day`.

`has_terminal_run_today` and `has_committed_run_today` are **untouched**. Neither applies to
a bot that legitimately runs 80 times a day; the scalpers use their own session-level state.

### 9.1 The live verdict on the open session row

A scalper holds one `bot_runs` row open all day, so unlike every other bot it cannot wait
until the end to write down why. `runtime._publish_verdict` writes `reason_code`,
`reason_text` and a `detail` payload onto the running row whenever the verdict changes, and
re-states an unchanged one every `PUBLISH_INTERVAL_SECONDS` (60s) -- the write is guarded on
`status = 'running'`, so it can never overwrite the reason a finished session settled on.

`detail` carries the futures feed's state (`subscribed`, `ticks_seen`, `candles`,
`contract`, `last_error`) alongside the gate values. That exists because the reason code
alone is ambiguous in the worst way: `not_warm` reads as "give it twenty minutes" whether
the feed is filling normally or was never subscribed at all, and only `subscribed` and
`ticks_seen` tell those apart. Both the run log and the bot card render it through
`frontend/src/lib/scalper-audit.ts`, and the same payload is appended to the decision log
line, so the downloaded log bundle (Settings -> Application Logs) answers the question
without an SSH session.

The cadence is the point of the re-statement: publishing every pass would be a log line and
a SQLite write per bot every two seconds, while publishing only on change leaves a bot that
stood down all day as a single line at 09:57 -- indistinguishable from one that stopped
being asked.

### 9.1 The stale-run reaper had to learn what "alive" means (found while building)

`reap_stale_runs` flips **any** `running` row to `failed`/`interrupted` — with no age bound at
startup, and at 30 minutes from the scheduler. A scalper's session row is `running` for the
whole trading day *by design*, so the aged sweep would have marked a healthy, actively-trading
bot as failed while it carried on trading.

Fixed with a nullable `heartbeat_at` on `bot_runs`, and `COALESCE(heartbeat_at, started_at)`
in the reaper. Bots 1 and 2 never beat, so for them the expression is `started_at` and their
semantics are byte-identical — verified against a copy of the real `users.sqlite3`, where all
13 existing runs migrate with a NULL heartbeat. Only a scalper session beats, which is what
lets the reaper still catch one that has genuinely hung on a blocked broker call. Excluding
the scalpers by bot type would have been smaller and would have lost exactly that.

### 9.3 What live adds over paper (2026-09-07)

Paper fills at the touch and abandons an unfilled entry with nothing to clean up. Live has
none of those luxuries, and each difference is a decision rather than a detail:

* **An unfilled limit is a real order resting at the exchange.** It is cancelled, re-priced
  once against a *fresh* touch, and if that does not fill, cancelled and abandoned — at most
  two orders and two cancels per attempt. An entry deliberately does **not** chase further on
  retry; an exit does widen progressively, because an exit has to complete while a position
  sits behind it with no stop.
* **A cancel that fails stops everything.** No second order is placed, and the bot is
  disarmed. An order you believe is dead but is not will fill later, into a position nothing
  is managing — placing again while unsure is how one intended position becomes two.
* **The cycle row is written BEFORE the order goes out.** A crash between `place_order`
  returning and the row being updated would otherwise leave a live position nothing knows
  about. `guards.reconcile_pending_cycles` resolves those on startup with two honest answers
  and no third: the broker shows a fill → adopt it so the exit loop takes over; the broker
  shows nothing → abandon it, having traded nothing. Anything the broker cannot answer is
  **escalated by Telegram and left open**, and blocks new cycles until a human resolves it.
  Guessing "no fill" there would strand a live position with no stop behind it.
* **An exit that will not fill alerts and stops trying.** Firing more orders into a market
  that keeps refusing them spends friction and achieves nothing; the position is handed back
  explicitly rather than churned.
* **Fill confirmation is the WS order feed with a REST backstop**, not the feed alone — the
  same lesson `strategy_group_lifecycle` learned when a completion path that only listened got
  stuck the one time the event did not arrive.

Two bugs the tests caught before any of this could run:

1. **The REST backstop was silently useless.** `watch()` pre-populated a zero-filled
   placeholder, and `state = tracker.state(...) or state` then clobbered the REST answer on
   every pass — so a fill the feed missed was still reported as unfilled. The tracker now
   stores an empty dict until a notification actually arrives, making "nothing heard" and
   "heard: nothing filled" distinguishable.
2. **A partial fill read as success.** `ok` was `error is None and filled > 0`, so 25 of 75
   units passed — and the entry path would then have abandoned the cycle while 25 units were
   genuinely held. `ok` now requires the full requested quantity, and a partial is *adopted*
   at its real size with the cycle's leg rewritten, so the exit sells what is held rather than
   what was asked for.

---

### 9.2 Day totals are recomputed, never accumulated

`scalper_day_totals` rebuilds cycles, realized net P&L, friction and the consecutive-loss
streak from the rows on every call. An in-memory counter plus a restart is precisely how a
cumulative stop silently resets mid-session and lets a bot that has already lost its limit
carry on trading.

Two consequences worth stating, both tested:

* **`net_pnl` is derived inside `close_cycle`, never passed in.** Net is what the stop and the
  loss counter read; letting a caller supply it alongside its own components is how the two
  drift apart. Friction is subtracted whether or not the caller tracked it, so a cycle that
  forgot it cannot read as break-even — which is the whole of §6.4.
* **An aborted entry is not a loss.** `is_loss` requires a *closed* cycle with negative net,
  so a cycle that never opened a position neither counts toward the cooldown nor clears it.

---

## 10. Config

Both blobs live in the existing JSON `config` column, typed by new pydantic models in
`app/domain/bots.py`, defaults encoding the agreed policy exactly as the existing configs do.

```yaml
momentum_long_scalper:
  index: NIFTY
  expiry_preference: nearest_weekly
  trade_on_expiry_day: false
  mode: paper                        # paper | live
  sessions:
    - {start: "09:35", end: "11:30"}
    - {start: "13:30", end: "15:10"}
  hard_square_off_ist: "15:15"
  signal:
    candle_seconds: 60
    ema_period: 9
    volume_ma_period: 20
    volume_multiplier: 1.5
    require_vwap: true
  premium_outlay_inr: 25000            # see section 8.5
  exits:
    target_pts: 10.0                 # trailing trigger, not a take-profit
    stop_loss_pts: 6.0
    time_invalidation_seconds: 90
    time_invalidation_min_move_pts: 3.0
    level_1_trigger_pts: 5.0
    level_1_lock_pts: 1.2
    level_2_trigger_pts: 8.0
    level_2_lock_pts: 5.0
    level_3_runner_step_pts: 3.0
  execution:
    entry_limit_tolerance_pct: 1.0
    entry_fill_timeout_seconds: 3
    entry_retries: 2
    exit_limit_band_pct: 1.0
  risk:
    cumulative_stop_inr: 10000
    consecutive_loss_limit: 3
    cooldown_minutes: 30
    api_budget_reserve_calls: 25

iron_fly_scalper:
  index: NIFTY
  expiry_preference: nearest_weekly
  trade_on_expiry_day: false
  mode: paper
  sessions:
    - {start: "11:30", end: "13:30"}
  hard_square_off_ist: "15:15"
  structure:
    wing_width_points: 150
    wing_width_vix_rule: {enabled: false, vix_threshold: 18.0, widened_points: 200}
  sizing:
    margin_ceiling_inr: 25000        # was 100000 (~13 lots); corrected 2026-09-13, §4.6
    min_lots: 1
  exits:
    target_decay_pct: 15.0
    hard_stop_loss_inr: null         # was 1500; off by default since 2026-09-13, §4.6
    stop_loss_credit_pct: 20.0
    max_spot_drift_pct: 0.35
  reentry:
    cooldown_minutes: 15
    range_window_minutes: 10
    max_range_pct: 0.15
  execution:
    entry_limit_tolerance_pct: 1.0
    entry_fill_timeout_seconds: 3
    entry_retries: 2
    exit_limit_band_pct: 1.0
  risk:
    cumulative_stop_inr: 10000
    consecutive_loss_limit: 3
    cooldown_minutes: 30
    api_budget_reserve_calls: 25
```

---

## 11. Frontend

Reuses the §9.7 furniture wholesale — the square cards in a two-up grid, the tabbed drawer
extending `Modal`. Two new cards, two new drawer configurations. Deltas only:

* The card shows **today's cycles, net P&L, and cumulative friction**, not a next-run time.
* A **PAPER** pill wherever a paper-mode bot appears. Unmissable, since the whole point is
  that a paper bot looks otherwise identical to a live one.
* The drawer's risk tab shows the **combined** daily downside across both scalpers (§5.2), so
  the sum is read rather than computed by the user.
* No manual-run sheet. `POST /bots/plan` has no meaning for a bot whose decision is a signal
  on a one-minute candle; the honest manual control is start/stop the session.
* Cycles are a paginated table under the session run, not top-level run-log rows.
* **An explicit confirmation dialog on the switch to `Live`** — see §11.2.

New backend paths need matching `rewrites()` in `frontend/next.config.js` **and** both nginx
confs (`nginx.conf`, `deploy/nginx.all-in-one.conf`).

### 11.1 What was built (2026-09-06)

**The scalpers could not reuse the writers' mode control.** `BotCard` collapses `enabled` +
`approval_mode` into Manual / Semi-auto / Auto. A scalper has no `approval_mode` at all, so an
armed one would have fallen through to "Semi-auto" and the card would have claimed it *asks on
Telegram before trading* — precisely false. Their axis is narrower and sharper: **Off / Paper /
Live**. `ScalperCard` is a separate component and `BotCard` delegates to it in one line, which
also kept an in-flight rework of that file undisturbed.

**Live is rendered but not selectable**, with a tooltip saying why, until step 9 ships real
dispatch. A switch that looks armed and is not would be the worst state for this control.

**Friction has its own column and its own card row**, never netted silently into P&L. A bot
that ran twenty cycles to stand still should say so on its face.

**The Costs tab saves separately from the bot.** The charge model is deployment-wide — shared
by both scalpers and the backtest, which must price the same trade identically — so it is not
part of any bot's config and the drawer's Save button does not own it. The tab carries the
§7.2 warning that the shipped rates are defaults to be checked against a contract note.

**Cycles are fetched only when a run is expanded.** A scalper produces dozens a day; loading
every session's cycles to render a collapsed list would pull the whole month for nothing.

### 11.2 The confirmation dialog on `Live` (2026-09-07)

Switching a scalper to `Live` is the one control on the whole bots surface that commits the
user to **real, unattended orders on a signal that turns over in seconds**, with no
per-trade approval by construction (§1). Every other mode change is recoverable by the next
click; this one can have placed a position before the user's hand leaves the mouse. It gets
a modal.

**Trigger.** Only the transition **`Off` → `Live`** or **`Paper` → `Live`**. Not
`Paper` arming (paper places nothing), not `→ Off`, not `Live → Paper` (that is the user
stepping *back*, and putting a dialog in front of it would train them to click through
dialogs on this control). The `Live` segment stays disabled entirely until
`LIVE_AVAILABLE` flips (§14 step 9) — the dialog is what guards it *after* that.

**Content.** Names the bot. States plainly: it will place real orders on the exchange, on
its own, whenever its signal fires inside a session window, until you set it back to Paper
or Off. Shows the numbers the user is actually authorising — this bot's **daily loss cap**
and, because the caps sum (§5.2), the **combined** downside across both scalpers if the
other is also live. Two buttons; the confirming one is not the default focus and is
labelled with the action (`Enable live trading`), not `OK`.

**It is frontend-only.** The backend already refuses live order calls whenever the licence
is not clean (`require_trading_not_revoked`, §5.4) and until `LIVE_DISPATCH_ENABLED` is on
(§14 step 9). The dialog does not gate anything the server does not already gate — it makes
the commitment legible at the moment it is made. A user who dismisses it stays on their
previous mode; nothing is PATCHed until they confirm.

**Not a checkbox, not a typed phrase.** The consequence is real but it is also routine for
someone running these bots daily — a "type LIVE to continue" step would be theatre by the
third time. One clear modal, once per transition into live.

### 11.3 The paper-evidence gate — step 9's precondition, made real (2026-09-07)

This section **supersedes** §14 step 9's `LIVE_DISPATCH_ENABLED` flag, which has been
removed.

**The problem with the old gate.** Step 9 said its precondition was "a full session of paper
evidence on the production instance, which no code can assert", and enforced it with a
hardcoded `False` that a developer flipped by hand. That is a precondition only in the sense
that someone remembered it: nothing tied the flip to the evidence, nothing re-checked it when
the settings changed underneath, and the deployment that most needed the discipline was the
one furthest from the person applying it.

**The gate.** A scalper may be set `live` only when one paper session run has **completed a
trading day** carrying the exact settings it would trade with. Enforced server-side in the
PATCH path (`route_bots._guard_scalper_live_transition`), so a direct API call is refused too
— the same reasoning that put `validate_session_windows` in the pydantic model.

**Why a trading day and not a cycle count.** A number would substitute an invented threshold
for the user's judgement, and the two bots produce wildly different cycle counts anyway. The
gate asserts only that a full day happened on these settings; the §11.2 dialog then shows
what that day *did* — cycles, closed cycles, wins/losses, net P&L, friction — and the user
decides whether it justifies real money. Objective gate, human judgement, real numbers. A
quiet day with no cycles still counts: it is evidence about these settings, and saying so is
more honest than pretending a threshold makes it good evidence.

**Why a config fingerprint rather than a flag.** Evidence is evidence for the settings that
produced it. `evidence.material_config_hash` hashes the material config and stamps it on the
session run, so editing any P&L-bearing parameter invalidates the evidence *automatically* —
there is no counter anyone can forget to reset, because the count is a join on a value that
just changed. Materiality is an **allowlist of exclusions**: everything is material except
`mode` (the field being gated) and `risk.api_budget_reserve_calls` (operational — it can stop
an entry but changes neither signal, size nor exit). A field added later is therefore gated
by default.

Three details the build settled, each pinned by a test:

* **Defaults hash identically whether spelled out or omitted**, because the config is
  normalised through its pydantic model before hashing. Otherwise a user who opened the
  Signal tab and saved without changing anything would silently void their own evidence.
* **A session whose settings changed mid-day counts for neither.** `stamp_session_config`
  clears the hash and marks the run `mixed` on any disagreement, and never restores it.
  Crediting such a day to the settings it started with would let a user paper-prove one
  configuration and arm a different one on its record.
* **A live bot's material settings are frozen.** Editing them is refused with a 409 telling
  the user to step the bot back to Paper first, which makes the demotion their deliberate act
  rather than a side effect of pressing Save. Auto-demoting would have been worse: with a
  position open it would have re-routed a real exit into paper simulation.

### 11.4 A real position outlives the switch that opened it (2026-09-07)

Two holes found while wiring the gate, both of which would have stranded real money:

* **`tick()` walks only *enabled* bots.** Setting a bot to Off with a live position open
  stopped the only thing evaluating its stop. The loop now also sweeps bots holding an open
  **live** cycle and ticks them with `entries_suspended` — §5.5's "a gate that blocks
  entering never blocks leaving", extended past the arming switch, which was the one gate
  that escaped it. Paper cycles are excluded: an abandoned simulation has no exchange side.
  The card shows this as **Closing · Live position** rather than Off, because "Off" over a
  position that is still at risk is a lie.
* **The executors routed on `config.mode`.** Moving a holding bot from Live back to Paper
  would have sent its exit to `close_paper_cycle` — marking the cycle closed at a *simulated*
  price while the actual position sat at the exchange. **An open position is now managed the
  way it was opened**: `cycle.paper` decides, and only an entry (where no position exists to
  contradict) consults the config.

### 11.5 Bot 4's live path (2026-09-07)

Written, completing step 9. It is not Bot 3's path with more legs — a four-leg structure
fails differently, and three decisions follow from that:

* **A partial fill on any leg is a failure, not a smaller position.** Bot 3 adopts a partial
  and manages it; the ladder works the same on 25 units as on 75. A fly with 75 on a wing and
  50 on a short is not a fly — the wing no longer covers the short it was bought to cover,
  and no exit rule in `iron_fly_bot` describes the result. Partials are unwound with
  everything else.
* **The unwind buys shorts back before it sells wings**, which is not the entry's reverse by
  accident: selling a hedge while its short is still open leaves a naked short for the life of
  one order, arriving at exactly what wings-first entry exists to prevent.
* **A cancel failure unwinds nothing.** An order believed dead but still live will fill later;
  unwinding around it would build a position out of a guess. Everything freezes, the bot is
  disarmed, and a human is told. Likewise a *failed* unwind leaves the cycle **open** — the
  exit loop keeps trying on later passes — rather than being tidied away as flat.

`guards.reconcile_pending_cycles` also had to learn the difference. It adopted a pending cycle
whenever the summed fills were non-zero, which for a fly could mean one wing. A multi-leg
cycle is now adopted only when every leg is accounted for; anything less is escalated to the
user, the same fail-closed answer an unanswerable order already got.

`live.buyback_price_ladder` was added for this: closing a short needs a buy limit that pays
progressively more, and `entry_price_ladder` deliberately ignores the attempt number because
an entry that does not fill is a trade not taken, whereas an exit that does not fill is a
position nobody is managing.

---

## 12. Accepted trade-offs

Each of these was raised, chosen deliberately, and is recorded so it is not rediscovered as a
bug:

1. **Absolute-point ladder across varying premium** (§3.3). Percentage was offered; absolute
   chosen. Paper mode reports both.
2. **Serialized four-leg entry means the fly is not struck at one instant** (§4.3). Follows
   from design-decision #24, which is not negotiable.
3. **Separate stops sum to double the stated daily downside** (§5.2).
4. **No max-cycles cap in v1** (§6.4). Friction, not API budget, is the binding constraint,
   and it is displayed rather than capped.
5. **The backtest validates the signal, not the fills** (§8.2).
6. **Marketable-limit bands degenerate below the ₹0.05 tick** on very cheap options —
   inherited, unchanged.
7. **Bot 3 aborts rather than chases an unfilled entry** (§3.6), trading missed entries for
   never buying the top of a move.
8. **Futures are unreachable through local reference data** (found 2026-09-06). `scrip_master`
   holds only CE/PE rows and `ws_token_index.populate_ws_token_index_from_raw` filters on
   `Series = "OPTION"` with a parseable strike, so there is no futures token to look up. The
   feed resolves it from the SDK's own SecurityMaster via `get_stock_token_value`, exactly as
   `index_spot_feed` does for the cash index — no ingest change, nothing extra to keep in
   sync. Two consequences: the SDK **returns** its exceptions rather than raising them
   (`except Exception as e: return e`), so the resolver checks the result shape explicitly;
   and the expiry format inside the SDK's key is the raw CSV column, unobservable off the
   production static IP, so the resolver tries an ordered set of candidate formats and logs
   which one worked.
9. **`subscribe_feeds(interval=...)` must never be called on the shared SDK** (found
   2026-09-06). It would give broker-computed OHLCV bars directly, and it sets `self.interval`
   on the shared instance; `get_stock_token_value` then resolves BFO as `2.` instead of `8.`,
   while this app hard-codes `BFO: "8.1"` in `ws_token_index.EXCHANGE_TO_WS_PREFIX`. One such
   call would break every SENSEX subscription and tick route in the process, silently. This is
   why candles are built from plain quote ticks rather than taken from the OHLCV stream.
10. **The futures feed subscribes all session even when both bots are `Off`** (§5.6, added
    2026-09-07). One extra WS contract subscription and a candle flush per loop pass, held
    for the whole session on a deployment where the scalpers may never be armed that day.
    Chosen over the alternative — warm-up only after arming — because that alternative made a
    bot armed mid-day blind through the first 20 minutes of the move that prompted it.

---

## 13. Out of scope

* SENSEX / BANKNIFTY (additive via `INDEX_EXCHANGE`).
* Telegram approval of individual scalps (§1).
* Order-book-imbalance and Bollinger-squeeze entries from the source conversation — the data
  for the first is already available (§3.2), so it is a v2 filter, not a rebuild.
* Delta-neutral gamma scalping with futures re-hedging.
* Skewed / asymmetric iron flies.
* Rolling or repairing a tested fly rather than closing it.
* Multi-position concurrency for Bot 3.

---

## 14. Build order

1. ~~**Tick-derived candle builder + futures volume field verification** (§3.2).~~
   **Done 2026-09-06.** `services/bots/scalping/candles.py` (pure, 25 tests),
   `services/bots/scalping/futures_feed.py` (contract resolution + subscription, 9 tests),
   and a `MockBreezeSdk` futures tick stream so the loop runs under `./dev.sh`. The volume
   question was settled from `breeze_connect`'s parser plus the real captures in
   `tests/fixtures/icici_ticks/` — see §3.2, which now records what was verified rather than
   what was assumed.
2. ~~**Migration, config models, run/cycle log.**~~ **Done 2026-09-06.** Two new
   `BOT_TYPES`, the `bot_cycles` table, `heartbeat_at` on `bot_runs` (§9.1), nested config
   models with a ladder-monotonicity validator, and session/cycle/day-total repository
   support — 32 tests. The scalpers are **not** in `list_bots` yet, so `/bots` is unchanged
   until step 8; `get_or_create_bot` makes them lazily, so wiring them in later needs no
   backfill.
3. ~~**The shared scalper loop skeleton** — pure decision layer plus driver, no orders.~~
   **Done 2026-09-06.** `signal.py` (momentum evaluation), `ladder.py` (the trailing ladder,
   with persisted state), `decide.py` (the gate stack) and `runtime.py` (the driver, wired
   into the app lifespan) — 67 tests. It decides, logs and heartbeats; it places nothing.
4. ~~**Bot 3 in paper mode**, end to end.~~ **Done 2026-09-06.** `charges.py` (the cost
   model, backed by an editable settings row), `paper.py` (fills at the touch plus
   spread-based slippage), `momentum_bot.py` (expiry/ATM selection, sizing, the paper round
   trip and orchestration), wired into the driver — 32 tests. Cycles are written, priced and
   closed net of friction. A bot set `mode: live` logs a warning and places nothing.
5. ~~**Backtest harness** against Bot 3's signal, to tune §10 parameters before any live run.~~
   **Done 2026-09-06.** `backtest.py` (pure replay reusing the production signal and ladder),
   `backtest_store.py` (the standalone cache), `spreads.py` (live calibration, wired into
   paper mode) and `scripts/scalping_backtest.py` — 24 tests. See §8.4, and §8.5 for the
   affordability constraint it exposed on its first run.
6. ~~**Bot 4 in paper mode**, including the §4.3 sequencing and its unwind paths.~~
   **Done 2026-09-06.** `iron_fly_bot.py` (structure, sizing, sequencing, exits, re-entry
   gate) and `margin.py` (an action-aware margin call), wired into the driver — 28 tests.
   See §4.6 for two things the build changed.
7. ~~**The §2.2 SG guard and the §6 breakers**, with tests, before either bot may be set
   `live`.~~ **Done 2026-09-06.** `guards.py` plus a `group_rule_for` accessor on the P&L
   engine — 22 tests. See §6.5 for what the build changed, including two bugs it exposed.
8. ~~**Frontend cards, drawer, cycle table.**~~ **Done 2026-09-06.** `ScalperCard.tsx` and
   `ScalperSettings.tsx` (new, so `BotCard`'s in-flight rework was not disturbed — it
   delegates in one line), an expandable cycle table in `BotRunLog`, `/bots/cycles` and
   `/bots/charges` with proxy rules in `next.config.js` and both nginx confs. All four bots
   are now listed. See §11.1.
9. ~~**Live dispatch.**~~ **Done 2026-09-07.** `live.py`, Bot 3's live entry/exit path,
   intent rows and startup reconciliation (18 tests), then completed the same day with
   **Bot 4's live path** — the serialized four-leg entry, both unwind paths, reverse-order
   exits and multi-leg reconciliation (14 tests, §11.5).

   `LIVE_DISPATCH_ENABLED` and the UI's `LIVE_AVAILABLE` constant are **gone**. What replaced
   them is a real precondition rather than a remembered one: the **paper-evidence gate**
   (§11.3) plus the **confirmation dialog** (§11.2). A scalper reaches `live` by completing a
   paper trading day on the settings it will trade with, and then by the user confirming a
   dialog that shows what that day actually did. See §9.3 and §11.3–11.5.

10. ~~**Always-on candle feed (§5.6).**~~ **Done 2026-09-07.** `_ensure_feed` moved out of
    the enabled-bot loop; the feed runs every pass during market hours on a trading day
    whenever a broker session exists, `Off` bots included, resolved via
    `broker_session.list_users_with_session`. Read-only mode keeps building candles.

11. ~~**A real position outlives its switch (§11.4).**~~ **Done 2026-09-07.** Exit-only
    ticking for a bot switched off while holding a live position, and the routing fix that
    stopped a real position being closed at simulated prices.

Step 7 gated step 9. A bot that can place orders before its circuit breakers are tested is
the one failure mode in this document that cannot be undone by editing config — which is
also why the gate that replaced the flag is enforced on the server, not in the card.

**What is deliberately still not automatic:** nothing here decides whether a paper day was a
*good* day. The gate opens the control; the user reads the evidence in the dialog and makes
that call. No threshold in this document should ever be read as the app endorsing a strategy.
