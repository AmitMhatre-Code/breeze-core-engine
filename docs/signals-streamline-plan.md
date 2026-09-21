# Signals & Bots streamline — plan

Status: **built** (2026-09-19), uncommitted. The rationale is recorded as design-decisions #39. Section 11 lists what the build found or added beyond this plan.

Supersedes, once built: design-decisions #30 (W-OBI as the published signal), #33 (shadow
evidence), the `:flow` addendum, #35's shadow-log scoring paragraphs, and #38 (user-created
variants, windows owned by Settings). #34 (expansion mechanism) and #36 (bot backtest periods,
fetch budget, real prices only) stay and are extended.

---

## 1. Decisions you made (2026-09-19)

| # | Question | Answer |
|---|---|---|
| 1 | Which mechanisms remain | **Volume expansion** and **Momentum** (EMA/VWAP/volume, promoted from Bot 3's private signal). VIX stays a Bot 4 filter, not a mechanism. W-OBI and both `:flow` challengers are decommissioned. |
| 2 | Live vs replay parity | Live computes only from 1-minute OHLCV+OI bars with **exactly the replay's formulas**, on bars built from ticks. No per-minute history polling. |
| 3 | SENSEX source | **BSESEN near-month futures, no OI**, labelled "thin data" everywhere it is shown. |
| 4 | Live-session audit trail | **Nothing is recorded live.** A session's audit trail is a backtest of that day, run on demand after 15:45. Bots' own run logs still keep the reading they acted on. |
| 5 | Meaning of a duration | The **window the mechanism reads and how long a call stands**. A bot trading a call exits when the call's duration ends. Its stop and ladder still apply. Durations are **1m, 5m and 15m**, and 1m was added on the second pass so that every bot can choose it. |
| 6 | Variants and fade | **Fixed grid**: mechanism × {1m, 5m, 15m} × {NIFTY, SENSEX}. No user-created variants. Fade becomes a **per-bot "trade against the signal" switch**. |
| 7 | Availability gate | A combination is available to bots once **any completed signal backtest's range spans ≥ 30 calendar days**. It is judged on coverage only, gaps allowed, and applies to Live and Simulation alike. |
| 8 | Scope of a signal backtest run | **Everything in one run**: both mechanisms × all three durations × both indices, one fetch, **one zip**. |
| 9 | Backtests in scope | Bots 2, 3 and 4 as today, plus a **new CAS Bingo backtest**. Bot 1 comes later. |
| 10 | Bot backtest vs signals | Replay **every mechanism × every duration** (× follow/fade where the bot has the switch), whatever the gate says, with each result reported separately. |
| 11 | Navbar | The radio picks the **mechanism**. The navbar always shows its **15m** reading for NIFTY and SENSEX. It is not gated. |
| 12 | Page location | A new top-level **Signals** page beside Bots. Settings → Index Signal is removed. |
| 13 | Momentum's volume rule | **Percentile rank, top 20%** of the trailing candles, the same rule expansion uses. It replaces `> 1.5 × 20-candle mean`. |
| 14 | Old evidence | **Drop** `index_signal_log` and the variants table in the migration. |
| 15 | Warm-up | **Levels reset daily; size rankings carry over.** At feed start, fetch the last 2 sessions' futures bars if they are missing, about 2–4 calls a day, so live warms from exactly the bars the replay uses. Today's 1-minute bars stay in Redis until midnight for restart recovery. |
| 16 | Bot 3 exit | The 90-second / 3-point time-invalidation is **dropped**. Every bot's signal trade exits when the call ends, and the stop and ladder still apply. |

---

## 2. The signal model

A **reading** is identified by `(mechanism, duration, index)`, giving 2 × 3 × 2 = 12 series. Every
reading is a **pure function of 1-minute bars**. A bar holds `ts` (the minute's start, IST), OHLC,
volume, and OI, where OI is None when absent. Those are the only fields ICICI's
`get_historical_data_v2` serves. Anything a mechanism would need beyond them (depth, bid/ask,
tick `avgPrice`, `ttv`) is forbidden. That rule is what makes "reproducible from history" true
by construction.

| index | instrument | OI | notes |
|---|---|---|---|
| NIFTY | NIFTY near-month futures (NFO) | yes | Rollover days are excluded for readings that use OI, as today |
| SENSEX | BSESEN near-month futures (BFO) | never, since ICICI serves 0 | median 20 contracts/min, and ~47% of minutes have no trade. Labelled "thin data" |

Common rules, carried over from the current engines:
- Readings run from 09:15 to 15:15. The closing auction is not continuous trading, and `unavailable` covers everything outside that span.
- `unavailable` is never `neutral`, and no bot trades on it (#30).
- A window may not span a gap larger than 300 s. The baseline keeps the windows from earlier sessions (#34).
- Each mechanism carries a **version** constant. A change to its formula bumps the version, and the version is stamped on every backtest run (see the gate in §5).

### 2.1 Volume expansion — `expansion`
It is the existing `expansion.evaluate` (#34), unchanged:
- **1m**: 1-minute price/volume window, NIFTY OI over 15 minutes (the floor), call stands 1 minute. It is new. On SENSEX it is mostly silent, because about half of BSESEN minutes carry no trade.
- **5m**: 5-minute price/volume window, NIFTY OI over 15 minutes (the floor), call stands 5 minutes. This is today's "5m price/vol + 15m OI" variant.
- **15m**: 15/15, call stands 15 minutes. This is today's incumbent.
- SENSEX: `require_oi=False` at every duration.
- The "5m, no OI" NIFTY variant is dropped.

### 2.2 Momentum — `momentum`
This is Bot 3's signal, lifted out of the bot and re-expressed on bars:
- d-minute candles aggregated from 1-minute bars, aligned to 09:15.
- Bullish when the last completed candle closes above EMA(9) **and** above the session VWAP, on a volume spike. Bearish is the mirror.
- **Session VWAP uses the replay formula live too**: OHLC/4 × volume, pre-open bars included (`backtest_common.SessionVwap`). Live stops reading tick `avgPrice`. Measured gap between the two: 0.98 pts mean, and neither ever put a close on the other side of VWAP (plan §8.7a).
- Volume: the candle's volume must rank in the top 20% of the trailing candles (decision 13).
- **1m** is today's Bot 3 signal: 1-minute candles, with the new volume rule.
- A call stands for one candle (d minutes) and extends while consecutive candles keep firing.
- **Levels never cross the night.** EMA and VWAP are rebuilt from today's candles only, as `CandleBuilder._start_new_session` already does, so an overnight gap cannot make the open read as a breakout. Whether the volume *ranking* may use previous sessions is §9 Q2.
- Parameters become fixed constants of the mechanism. The per-bot `MomentumSignalConfig` knobs are removed.

### 2.3 Direction is the bot's
Follow/fade is no longer part of a signal. `variants.apply_direction` moves to the bot side as
the "trade against the signal" switch. `unavailable`/`neutral` pass through untouched, as today.

---

## 3. Live path

- **One bar source per index.** There is one tick → 1-minute OHLCV+OI bar builder, replacing the two that exist today (`scalping/candles.CandleBuilder` for Bot 3 and `index_signal/expansion_feed.BarAccumulator`). It is fed by the NIFTY futures ticks already subscribed and by a **new BSESEN futures subscription** on BFO. The token is resolved via the SDK SecurityMaster like `futures_feed`, and it never calls `subscribe_feeds(interval=...)` (scalping-plan trap 7).
- **12 live engines**, each the same pure function the replay calls, fed each completed bar.
- **Publish the current reading only**, to Redis under `signal:{mechanism}:{duration}:{index}`, with `valid_until` as today. This is the one thing the navbar and bots read, through `index_signal.reader`. No history of readings is stored anywhere, so `index_signal_log` and live shadow logging go.
- Settings shrink to a single global row: `navbar_mechanism` ∈ {`expansion`, `momentum`}.
- Warm-up and restart recovery need earlier bars. See §9 Q2.

### Decommissioned (backend)
- `index_signal/engine.py` (W-OBI), `depth_feed.py`, `weights.py` (NSE/BSE weight fetching), `flow.py`, `variants.py`, most of `shadow_log.py` (its scoring logic moves to the backtest scorer, §4), and `settings.py` (replaced by the single row).
- The depth-tick bypass in `ws_tick_pipeline`, the depth re-arm in `ws_price_feed_watchdog`, and `system_chain_health.ensure_depth_feed`.
- Routes: every `/api/settings/index-signal/*` route (preferences, weights, shadow-report, readiness, variants, flips, expansion/backtest, readings and calls downloads). The new routes are listed in §4.
- Tables: `index_signal_log`, `index_signal_variants`, the weights cache and the old settings row, all dropped by a migration. See §9 Q3.
- Tests: `test_index_signal_engine/flow/weights/settings/shadow_log`, `test_signal_variants*`, `test_unknown_depth_semantics` are deleted or rewritten.

---

## 4. Signal backtest

**Trigger.** One **Backtest** button on the Signals page opens the existing period picker (last
day / week / month / custom). The fetch rules are unchanged from #36: live broker only, never
09:00–15:45 on a trading day, the 800-call daily budget, and a stop is a note, never a failure.
The fetch covers NIFTY and BSESEN 1-minute futures bars for the range plus a warm-up of about a
week.

**One run** replays all 12 series. Each series is scored once by one scorer:
- the fixed forward horizons;
- edge over the base rate with the 95% range taken from readings a horizon apart;
- a minimum-move floor;
- the breakeven from Trading Costs (`breakeven.py`, kept);
- per-day correlation, never pooled (#33's four traps).

**Storage.**
- A `signal_backtest_runs` table holds id, triggered_at, period, from and to, status, notes, mechanism versions, headline summary JSON and zip path.
- The zip lives at `DATA_PATH/signals-backtest/<run_id>.zip`.
- A running job shows progress and can be stopped. A stopped or failed run keeps its row and has no zip.

**Zip contents (one per run)**
```
README.txt                    what every file and column means
run.json                      period, range, triggered at, app + mechanism versions,
                              every parameter, fetch notes, sessions replayed/skipped (+why)
summary.csv                   one row per index × mechanism × duration: sessions, readings,
                              calls (bull/bear), flips, right/wrong at +d / +2d / call end,
                              edge vs base rate + 95% range, mean called-way bps, breakeven bps,
                              share of calls withdrawn early, verdict, 30-day gate status
NIFTY/bars.csv                the exact input bars (so anything can be recomputed offline)
NIFTY/expansion-1m/readings.csv   one row per minute: bar, every component (price_bps, volume,
                              ranks, oi_delta, quadrant | close, ema, vwap, candle volume,
                              volume rank), state, reason, strength, call start/end,
                              forward move +1/+5/+15/+30 min and at call end, MFE/MAE in the call
NIFTY/expansion-1m/calls.csv      one row per call: fired at, side, level, strength, components
                              at fire, outcome at +d / +2d / call end, MFE/MAE, beat breakeven?,
                              withdrawn early?
NIFTY/expansion-1m/days.csv       per session: up/down day, calls, hit rate, correlation, gaps,
                              excluded (rollover) flag
… same for expansion-5m, expansion-15m, momentum-1m/5m/15m, and SENSEX/…
```

**Routes (new)**
- `POST /signals/backtest` starts a run.
- `GET /signals/backtest/job` returns the running job.
- `POST /signals/backtest/cancel` stops it.
- `GET /signals/backtest/runs` returns the activity log.
- `GET /signals/backtest/runs/{id}/zip` downloads a run's zip.
- `GET /signals` returns the current readings, last-run verdicts, gate status and navbar choice.
- `PUT /signals/navbar` sets the navbar mechanism.

Each needs matching `next.config.js` rewrites and nginx entries.

---

## 5. The 30-day gate

A `(mechanism, duration, index)` combination is **available to bots** when at least one
completed signal backtest run meets both conditions:
- it covers that combination on the mechanism's **current version**, and
- its range spans ≥ 30 calendar days (`to − from + 1 ≥ 30`), data gaps allowed.

Because one run covers all 12 series, in practice one 30-day run opens everything, and a change
to a mechanism's formula closes that mechanism until it is re-run.

Where the gate is enforced:
- **Saving** a bot config that names an unavailable combination is refused with a message saying which backtest to run.
- **Arming** a bot in Simulation or Live is refused the same way.
- **At runtime**, a bot whose combination became unavailable (a version bump) makes no entries. Its run log says why.
- **Backtests are never gated.**

The scalpers' existing paper-evidence gate (one completed paper session on the exact settings
before Live, `scalping/evidence.py`) is a gate on the *bot*, not the signal, and stays as is.

---

## 6. Signals page (frontend)

- A new nav item **Signals** at `/signals`, beside Bots. Settings → Index Signal and `lib/settings/index-signal.ts` are removed.
- **One section per mechanism** (2 sections), in plain language:
  - *What it watches*: two sentences. For example: "Fires when the price moves unusually far on unusually heavy trading, and on NIFTY only when new positions are being opened."
  - *Right now*: NIFTY and SENSEX at 1m, 5m and 15m as six small chips (Bullish / Bearish / Quiet / No reading, with a short reason). SENSEX carries a "thin data" tag.
  - *What the last backtest says*: per index and duration, one sentence. For example: "Over 21 sessions it made 70 calls and was right 39% of the time. After trading costs, following it lost money." Details are in the zip.
  - *Available to bots*: a badge, or "Needs a backtest covering 30 days".
  - A **Show in navbar** radio.
- **Backtest** button and **Activity log** table with these columns: triggered at, period, status, headline, and **Download (.zip)**. A running job shows progress and Stop.
- The navbar chip names the mechanism in its tooltip and shows the 15m reading.

---

## 7. Bots

**Config shape.** Each bot that uses a signal carries
`signal: {mechanism: "expansion"|"momentum", duration: 1|5|15, direction: "follow"|"fade"}`.
This replaces Bot 3's `entry_signal`, `signal: MomentumSignalConfig` and the variant id in Bot 4's
`entry_filter`.

| bot | uses the signal as | direction switch |
|---|---|---|
| Bot 1 holdings, Bot 2 expiry writer | nothing (unchanged) | — |
| Bot 3 scalper | its entry. The trade exits when the call's duration ends, and the stop and ladder still apply. The 90s time-invalidation is removed (`SIGNAL_WINDOW_ENDED` already does this for variants) | yes |
| Bot 4 iron fly | an entry filter: hold the fly while the chosen signal has a live call. The VIX filter stays separate and unchanged | no (a filter has no side) |
| Bot 5 CAS Bingo | flip source for both debit and credit, per index | debit only |

**CAS Bingo changes.**
- Today's flips are **recomputed from today's bars** instead of read from the shadow log. The restart case depends on §9 Q2.
- `strong_threshold` is removed. It is a W-OBI-scale number: every expansion call already has strength ≥ 0.80, and momentum has no strength. A "strong flip" becomes a call held for `sustain_minutes`.
- Autonomous's `readiness == ready` gate is replaced by the 30-day gate.

**Migration of stored configs.**
| stored today | becomes |
|---|---|
| Bot 3 on the incumbent variant | expansion / 15 / follow |
| Bot 3 on `15m fade` (the current default) | expansion / 15 / fade |
| Bot 3 on `5m + 15m OI` or `5m no-OI` | expansion / 5 / follow |
| Bot 3 on `momentum` | momentum / 1 / follow |
| Bot 4 `expansion_neutral` + variant | same mapping, direction ignored |
| CAS Bingo | expansion / 15 / follow |

**Consequence to know:** right after deploy nothing is available until a ≥30-day signal backtest
has run on the new versions. Every signal-using bot sits idle with a clear run-log reason until
then. Run one on the first evening.

---

## 8. Bot backtests

- **Per-combination replays.** One backtest run of Bot 3, 4 or 5 replays the bot once per combination:
  - Bot 3: 2 mechanisms × 3 durations × follow/fade = 12.
  - Bot 4: 6, plus a no-signal-filter baseline.
  - CAS Bingo: 6 for credit, 12 for debit, and the strangle (which uses no signal) once.

  Signal readings are built once per series over the range plus warm-up, exactly as the signal backtest builds them, so both backtests act on the same calls.
- **One Activity row per run**, as today (#35). The run detail shows a **comparison table** with one row per combination: trades, win rate, net P&L after costs, max drawdown, worst day and data gaps. The bot's saved combination is marked "your setting".
- **Zip download per run**, in the same shape as §4:
  - `README.txt` and `run.json`;
  - `summary.csv` (the comparison table);
  - per combination: `trades.csv`, `daily.csv` and `decisions.csv` (every minute's gate verdict and the reading it saw);
  - `audit.jsonl` (the existing trail).

  This replaces today's single JSONL download for backtests.
- **Option-price fetching grows.** Different combinations trade at different times and strikes. Everything still runs under the 800-call daily budget and the "reported gap, never a failure" rule. A month of Bot 3 across 12 combinations may need two evenings of fetch.
- **New: CAS Bingo backtest.**
  - Expiry days in the range only.
  - 1-minute index bars, which carry the CAS indicative index from 15:15 (verified).
  - 1-minute option bars for the strikes the rules pick, NFO and BFO (BFO OI is 0; prices are fine).
  - Its own exit loop (SL and target % of net premium, else settle).
  - Sized from saved budgets and today's margin, like Bot 2.
  - The liquidation step is not replayed, because it acts on positions that existed at the time. That is stated in the run notes.

---

## 9. Warm-up, in detail (decision 15)

Signals read futures bars only; option prices are never an input. What a mechanism may take from earlier sessions is split in two:

- **Levels never carry over.** A level is a price, an EMA or a VWAP.
  - Momentum rebuilds EMA and VWAP from today's candles.
  - Expansion never reads a window that spans the overnight break (`max_gap_seconds`).
  - So a gap at the open is never itself read as a move.
- **Size rankings carry over.** Expansion's price and volume ranks, and momentum's volume rank, compare against the trailing windows, including earlier sessions'. These are bps and contracts, not price levels.
  - On a news-driven open the first hour ranks high against a calm yesterday.
  - By about 11:15 the 120-window baseline is all today's.

**Parity.** The replay warms from the preceding days. Live therefore fetches the last 2 sessions' futures bars at feed start if the cache lacks them. That is about 2–4 history calls a day, and the only history calls made in market hours. Live then warms from the same bars.

**Restarts.** Today's 1-minute bars (inputs, not signals) are kept in Redis until midnight. A mid-session restart rebuilds every engine's day exactly, and CAS Bingo recomputes today's flips from them.

## 10. Build order

Each step lands green on the full backend suite and `npm test`.

1. **Bars and mechanisms (pure).**
   - The unified bar builder.
   - The `mechanisms` registry (expansion, momentum), with d-minute aggregation and versions.
   - One `replay_series(bars, key)` used by live and backtest alike.
   - Parity tests: live feed of the verified 2026-09-15 captures vs history bars.
2. **Live publisher rewrite.**
   - 8 engines and the BSESEN subscription.
   - Redis-only current readings, the navbar row, and warm-up per Q2.
   - Delete W-OBI, flow, depth, weights and the shadow log. Migration per Q3.
3. **Signal backtest.** The one-run job, the scorer (moved from `shadow_log`), the zip writer, runs table, routes and rewrites.
4. **Signals page and navbar**, and removal of Settings → Index Signal.
5. **Bot config + gate.**
   - The new `signal` block and config migration.
   - Enforcement on save, arm and runtime.
   - Bot 3, 4 and CAS Bingo read `(mechanism, duration)`, with CAS flips recomputed from bars.
6. **Bot backtests per combination**: the comparison table and the zip.
7. **CAS Bingo backtest.**
8. **Docs.** A new design-decision (#39) superseding #30/#33/#38. Update `architecture.md`, `functionality.md` and the changelog.

---

## 11. Found or added during the build

- **A call is timed at its bar's close.** #34's engine timed a call from the bar's start, which the live publisher could never see for a one-minute call. Both paths now take readings at the close.
- **A re-fire at the exact lapse moment extends the call.** Without that, every momentum candle would be a new call.
- **The volume of a flat minute.** The live bar builder writes a flat zero-volume bar for a quiet minute only within five minutes of the last print, matching ICICI's flat bars without hiding a feed outage.
- **The warm-up is two cached sessions**, not ten days. Every window a series keeps fits inside one session, so the engine state at the open is the same as a replay's, for a fraction of the work.
- **CAS Bingo's credit move uses the futures' open.** Comparing a futures level with the cash open would have added the basis to a 0.5% trigger.
- **The Bot 3 exit when the signal cannot be read.** The trade holds; an unreadable reading is never an exit. *Superseded in part by the 2026-09-21 round below* — it used to hold only until the call would have lapsed, then close.
- **CAS Bingo backtest approximations**, stated in every run's notes:
  - credit spreads are sized by maximum loss per lot;
  - liquidation of other positions is not replayed.
- **Mock mode** now streams BSESEN futures on BFO too. The mock's depth rooms are removed.
- **Not done, for the user to decide:**
  - no `changelog.ts` entry or version bump;
  - the backtest CLI (`scripts/scalping_backtest.py`) still replays a single setting.


## 12. The 2026-09-21 round (design-decisions #40)

Prompted by reading the first long run (2026-04-01 → 09-18: 12 series, 117 sessions, 17,200 calls),
which the page reported as *"No series showed an edge"* while five of the twelve were `worse` —
which is a fade candidate, not a failure. Six changes; the reasoning and every measurement are in
design-decisions #40.

**Reporting only — no bot behaviour changes**
1. **Both directions are scored.** Each series reports follow *and* fade. The page sentence and the
   activity-log headline name the candidates in either direction.
2. **Every horizon is scored**, not just the call's own duration: 1, 5, 15 and 30 minutes, with
   `best` naming the winner. Decision 5 had welded the horizon to the window, and the 1-minute
   series' information peaks at +15.
3. **Money, not hit rate.** Each horizon carries the average move net of the bar and `t`, its size
   against the day-to-day scatter, taken per session first. `stands_out` needs a positive net,
   t ≥ 2 and ≥20 sessions.
4. **The bar is size-aware and includes the spread.** `cost_lots` on the Signals page (default 1,
   so nothing moves until it is set); the spread comes from `scalping/spreads.spread_stats()`.
   `rough_pnl_one_lot_rupees` becomes `rough_pnl_rupees`, priced at that size.

**Mechanism changes — both close their mechanism until a fresh ≥30-day backtest runs**
5. **Expansion v3**: a window whose anchor bar never traded is `unavailable`
   (`anchor_not_traded`), because its move is measured from a carried-forward price. Gates the
   current reading only, never the baseline.
6. **Momentum v2**: the EMA is carried across the night shifted by the overnight gap, ending a
   blind spot that ran to 11:30 on the 15-minute series every day. VWAP still resets.

**Bot behaviour**
7. **A signal trade is no longer closed by its call running out.** The stop, the trailing stop, a
   call the other way (`signal.call_reversed`) and the square-off close it. Deliberate cost,
   accepted: a quiet trade can hold to the square-off and tie up margin far past its window.

**Decided against**: a cross-check between the two mechanisms (fade expansion only when momentum
does not confirm). It replicated, weakly, but breaks the rule that a series reads only its own
bars. If it comes back it belongs on the bot side as a filter.

**Still to do**
- **Run one ≥30-day signal backtest in an evening.** Both mechanisms changed version, so every
  signal-using bot stands down until then — expected, and the run log says so.
- Re-run the bot backtests afterwards: they price real options, and every bps figure in #40 assumes
  a 0.5 delta with no decay and no gamma.
- No `changelog.ts` entry or version bump yet.
