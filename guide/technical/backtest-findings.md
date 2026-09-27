# Backtest findings: signals and bots

*Internal, repo-only. Current as of 25 September 2026. Not published in the app.*

This is the record of what the signal and bot backtests have found. How signals, bots and backtests **work** is explained in the user guide, which is published inside the app at `/guide`:

- [Signals](../user/signals.md) — the twelve readings, how each mechanism decides, the 30-day gate, how a signal is scored.
- [Bots](../user/bots.md) — every bot, its modes, settings, entries and exits.
- [Backtests](../user/backtests.md) — how to run signal and bot backtests and read the results.

The user guide deliberately carries no findings: they change with every run, and customers should judge from their own results. Section references below ("Part 1.9", "Part 2.4", and so on) point to the matching sections of those user guide pages.

---

## Executive summary
### What the backtests have found so far

We have been candid with ourselves here, and we are being candid with you.

1. **No signal predicts direction well enough to follow.** Across 117 trading sessions (April–September 2026) and about 17,200 signal calls, no reading was right more often than the market's natural up/down split.
2. **One signal is reliably wrong-way, which is itself useful.** After a volume-expansion call, the index tends to move slightly *against* the call over the next 15 minutes. Trading *against* the 1-minute expansion signal and holding for 15 minutes is the only pattern that held up when tested on data it had not been tuned on. The edge is small, strongest on SENSEX, and has not yet been confirmed on real option prices.
3. **The Long Scalper (Bot 3) lost money on every setting.** Replayed on real ICICI option prices from January to September 2026, all twelve signal settings lost money after costs. The losses ranged from about ₹43,000 to ₹2.26 lakh at a ₹25,000-per-trade budget. The bot won 40–55% of its trades, but its average loss was larger than its average win, and trading costs of about ₹95 a trade added up.
4. **Early practice sessions exposed design flaws, and those flaws have been fixed.** The Long Scalper kept re-buying the same signal after being stopped out. The Iron Fly was sized so large that its stop-loss sat inside the normal bid-ask noise and fired within seconds. Both were corrected.
5. **Several things have not been tested yet.** The improved signals (updated 21 September), Bot 2, Bot 4 and CAS Bingo have not been backtested. Until a fresh 30-day signal backtest runs, every signal-reading bot stands down by design.

### Bottom line

The app is built to be **honest about what works**. Every signal can be replayed against history, every bot can be backtested on real traded prices, and nothing is allowed to trade on a signal that has not been tested. So far the evidence says the direction signals are **not a reliable source of profit on their own**, and the Long Scalper should stay in practice mode. The premium-selling bots (1, 2, 4 and 5) are yet to be judged against history.

### State of the 30-day gate

Both mechanisms were improved on 21 September 2026 (see 3.5 below). **A fresh 30-day signal backtest has not yet been run**, so signal-reading bots are currently standing down by design.

---

## Part 3 — Backtest results
### 3.2 Signal backtest: 1 April – 18 September 2026

**Scope:**
- 117 trading sessions and all twelve readings.
- **About 17,200 calls** in total.
- This run was made *before* the 21 September improvements described in 3.5, and scored with the older method.

**Headline: no reading could be profitably followed.**

"Right" means the index moved the called way by at least the cost of a trade within the call's duration. Smaller moves count as neither right nor wrong. For comparison, the market's natural up/down split over any stretch was about **50%**.

| Reading | NIFTY calls | NIFTY right | SENSEX calls | SENSEX right |
|---|---|---|---|---|
| Expansion 1m | 1,326 | 42% | 3,186 | 43% |
| Expansion 5m | 630 | 45% | 1,122 | 40% |
| Expansion 15m | 351 | 46% | 502 | 43% |
| Momentum 1m | 4,036 | 45% | 4,040 | 52% |
| Momentum 5m | 778 | 48% | 803 | 47% |
| Momentum 15m | 211 | 49% | 215 | 50% |

**Five of the twelve were reliably *worse* than chance.** Those were NIFTY expansion 1m, SENSEX expansion 1m, 5m and 15m, and NIFTY momentum 1m. The original report summed this up as "no series showed an edge". That was misleading: a reading that is reliably wrong is a candidate for fading, not a failure. This finding led directly to scoring both directions (3.5).

**What deeper analysis found**

"bps" is basis points: 1 bps = 0.01%. On NIFTY at about 26,000, 1 bps is about 2.6 index points.

1. **Expansion calls are followed by a move against them.** One minute after a 1-minute expansion call, NIFTY had moved on average **0.44 bps against** the call, and SENSEX **0.52 bps against**. This was highly consistent across days. The effect **peaks around 15 minutes later** (NIFTY −1.16 bps, SENSEX −1.53 bps), well beyond the call's own one-minute life. The old scoring only looked one minute out, so it could not see this.
2. **One pattern survived a proper test.** To guard against fitting to noise, the period was split: patterns were found in April–June and then checked on July–September, which they had not seen.
   - **Only one survived: trading *against* the 1-minute expansion call and holding for 15 minutes.**
   - On SENSEX it earned **+1.14 bps net** in the unseen period, consistently across days. On NIFTY it earned **+0.48 bps**, which was positive but not consistent enough to rely on.
   - Everything else failed on the unseen half: signal strength, the open-interest reading, time of day, distance from VWAP, and day-volatility regimes.
3. **A trap we nearly fell into.** A split by "how volatile the day was" looked strong, until the day's range was measured *as it stood at the moment of the signal* rather than over the whole day. Then it reversed. The whole-day version quietly used information from the future. It was discarded.
4. **SENSEX's thin data was hiding stale spikes.** Almost all of SENSEX's reversal effect came from bars where the market *had* actually traded the minute before. Spikes measured from a carried-forward price after a dead minute carried nothing. This led to the "ignore spikes after a dead minute" rule.
5. **The 15-minute momentum reading was blind all morning.** It gave its first reading at 11:30 **on every one of the 117 sessions**, made its first call around 13:00, and made **no call at all on 101 of them**. That is the reading the navbar shows. This led to carrying the trend line overnight.
6. **The cost bar was about 3.3 times too high.** It had been priced at one lot, but most of a round trip's cost (about ₹47 of ₹61) is flat brokerage and GST that does not grow with size. The breakeven move is **0.80 bps at 1 lot, 0.31 at 5 lots and 0.24 at 10 lots**. At the same time, the old bar left out the bid-ask spread entirely. Both errors are now fixed.

**Important caveat.** All the basis-point figures above are measured on **futures moves**, converted to an option at a notional 0.5 delta, with no time decay or gamma. They are good for ranking signals against each other. Whether any of it survives **real option prices** is what bot backtests answer.

### 3.3 Long Scalper (Bot 3) backtest: 1 January – 21 September 2026

**Setup.** The bot was replayed twelve ways (every signal setting, following and fading) on real ICICI NIFTY option prices over 177 trading sessions. The settings used were the user's saved ones:

| Setting | Value used |
|---|---|
| Premium per trade | ₹25,000 (about 4–5 lots on average) |
| Session window | 10:00–15:15 |
| Trade on expiry day | Yes |
| Stop / ladder | 6-pt stop; +5 → lock +3; +8 → lock +5; trail 3 pts beyond +10 |
| Daily loss limit | ₹10,000 |
| Losing-streak pause | 3 losses → 10 minutes |

These differ slightly from the shipped defaults in Part 2.4: a higher Level 1 lock, one long window instead of two, expiry days included, and a shorter pause.

The results below are from the most complete of three runs (24 September). Two earlier runs, on 22 and 23 September, were cut short by the daily data allowance and covered too few sessions to be meaningful.

**Result: every one of the twelve settings lost money after costs.**

| Signal setting | Sessions replayed | Trades | Winners | Gross P&L | Costs | **Net P&L** | Worst drawdown |
|---|---|---|---|---|---|---|---|
| Momentum 15m · fade | 167 | 348 | 55% | −₹9,506 | ₹33,794 | **−₹43,301** | −₹64,712 |
| Expansion 5m · fade | 127 | 417 | 51% | −₹35,789 | ₹40,332 | **−₹76,121** | −₹1,09,326 |
| Expansion 15m · fade | 166 | 383 | 49% | −₹56,375 | ₹36,556 | **−₹92,931** | −₹96,377 |
| Expansion 15m · follow | 167 | 387 | 47% | −₹87,350 | ₹36,861 | **−₹1,24,211** | −₹1,39,533 |
| Expansion 1m · fade | 79 | 280 | 48% | −₹1,01,790 | ₹27,254 | **−₹1,29,044** | −₹1,30,070 |
| Expansion 1m · follow | 77 | 269 | 40% | −₹1,03,769 | ₹26,108 | **−₹1,29,877** | −₹1,35,623 |
| Momentum 15m · follow | 166 | 343 | 45% | −₹1,16,103 | ₹32,869 | **−₹1,48,972** | −₹1,64,492 |
| Momentum 5m · follow | 81 | 383 | 43% | −₹1,52,451 | ₹36,578 | **−₹1,89,029** | −₹2,02,476 |
| Momentum 5m · fade | 83 | 389 | 49% | −₹1,81,695 | ₹37,110 | **−₹2,18,805** | −₹2,19,909 |
| Expansion 5m · follow | 127 | 412 | 43% | −₹1,86,943 | ₹39,342 | **−₹2,26,285** | −₹2,34,312 |
| Momentum 1m · follow / fade | 5 | 24 / 20 | 13% / 10% | ~−₹49,000 | ~₹2,000 | **~−₹51,300** | — (too few sessions to judge) |

**What the numbers say**

1. **Winners are smaller than losers.**
   - Across settings (excluding momentum 1m) the average winning trade made **₹900–1,450**, while the average losing trade cost **₹1,600–1,960**.
   - At those sizes the bot needs to win **57–69%** of its trades just to break even. The best it managed was **55%**.
   - The ladder locks in small wins quickly, but a 6-point stop on 4–5 lots is a ₹1,500–2,000 loss, and exits sometimes slip past the stop.
2. **Costs turn small losses into large ones.**
   - Each round trip cost about **₹95–100**, which is ₹26,000–40,000 over the period.
   - For the best setting (momentum 15m, fade), costs were **3.5 times** the trading loss itself. Before costs it was close to break-even.
3. **Fading tended to beat following.** Fade did better in three of the five pairs with enough data (expansion 5m, expansion 15m, momentum 15m), about the same on expansion 1m, and worse on momentum 5m. That broadly agrees with the signal backtest's finding that calls are followed by moves against them, but no fade was strong enough to overcome costs.
4. **Trades were very short.**
   - The median trade lasted **2.5–3 minutes**; three-quarters were closed within 7 minutes.
   - Roughly half the exits were the initial stop and half the trailing stop. Very few were an opposite signal or the square-off.
   - The one pattern that survived the signal test was about a **15-minute** hold, so the bot's tight stop and ladder close trades long before that pattern has time to play out. That pattern was also strongest on SENSEX, which the Long Scalper does not trade.
5. **It was consistent in the wrong way.** Even the best setting made money in only **3 of 9 months**, and most settings in one month or none.
6. **Coverage varies.** The 15-minute settings were replayed on about 166 of 177 sessions. The 1- and 5-minute settings covered fewer (77–127) because they need option prices at more times and strikes, and the data allowance ran out. The momentum 1m result (5 sessions) should be ignored.

**Verdict:** as configured, the Long Scalper should **stay in paper mode**. Neither its signal choices nor its exits produced a profit over nine months of real prices.

### 3.4 Early practice sessions: 10–11 September 2026

Before the backtests existed, Bots 3 and 4 ran in paper mode on live prices. Those two days used an earlier version of the momentum signal, and they exposed design flaws that no amount of theory had.

**Long Scalper**
- **10 Sep (a choppy day): −₹4,074. 11 Sep (a trending day): +₹9,480**, carried by trades the trailing stop let run. That is net +₹5,406 over two days. Momentum strategies win on trend days and bleed in chop.
- **The flaw:** a signal is a *state* that stays true minute after minute, so after being stopped out the bot re-bought the **same** signal seconds later. It did this seven times across the two days. Only one of those re-entries won, and together they came to **−₹3,706**.
- **The fix:** the one-trade-per-signal rule (Part 2.4). The bot may re-enter only after the signal has switched off and fired afresh. A fixed "wait 5 minutes" timer was considered and rejected, because it guesses how long a run lasts where the rule reads when it has ended.

**Intraday Iron Fly**
- The original default margin ceiling of ₹1,00,000 bought **about 13 lots**.
- Each fly started roughly **1–1.3 points down**, purely from the bid-ask spread across four legs. The flat ₹1,500 stop was **under 2 points** at that size, so it sat inside normal noise.
- Flies were stopped out **14 and 37 seconds** after entry. Seven flies lost **₹13,750** between them without the strategy ever really being tested. The one fly that survived its opening dip was **+₹1,817** when its window ended.
- **The fix:**
  - The ceiling is now **₹25,000** (about 3 lots) and the flat stop is off by default.
  - The **20%-of-credit** stop and the **0.35% drift** stop now govern.
  - The fly is meant to be held through its window: it earns back its entry spread only after tens of minutes of calm, at roughly 3 points an hour.

**A lesson both bots taught:** in these strategies the constraint that binds is **trading costs, not opportunity**. A scalper that could take 80 trades in two hours would spend around ₹8,000 on costs against a ₹10,000 daily limit, and could hit its limit while roughly flat on price.

### 3.5 What changed because of the evidence

| Date | Change | Prompted by |
|---|---|---|
| 13 Sep | Long Scalper: one trade per signal | Re-entries on stale signals (3.4) |
| 13 Sep | Iron Fly: ~3 lots, credit-% and drift stops, hold the window | Hair-trigger stops (3.4) |
| 19 Sep | Signals rebuilt as a fixed, fully replayable grid, with the 30-day gate. Order-book signals retired. | Order-book signals could not be backtested and showed no edge |
| 21 Sep | Signals scored in **both directions** and at **every horizon** | Five "worse" readings reported as "no edge" (3.2) |
| 21 Sep | Cost bar sized to **your lots** and including the **spread** | Bar was ~3.3× too high, and ignored the spread (3.2) |
| 21 Sep | Expansion ignores spikes after a dead minute | SENSEX stale-anchor finding (3.2) |
| 21 Sep | Momentum trend line carried overnight, gap-adjusted | 15-minute momentum blind until 11:30 every day (3.2) |
| 21 Sep | A signal trade no longer closes just because its call lapsed | 89% of 1-minute calls simply lapsed after one minute, so the trailing stop never got a chance |
| 23–24 Sep | Backtests bounded by memory and by the day's remaining ICICI allowance | Long runs being cut short (3.3) |

### 3.6 What has not been tested yet

- **A signal backtest on the improved signals.** Both mechanisms changed on 21 September, so their 30-day gate is closed. **Until a fresh signal backtest covering at least 30 days is run, the Long Scalper, CAS Bingo's spreads and the Iron Fly's "signal quiet" filter will not trade, in practice or live.** This is expected, and each bot's activity log says so.
- **Bot 2 (Expiry-Day Index Writer):** backtesting is available (each shortlisted strategy is replayed side by side on every expiry day) but has not been run yet.
- **Bot 4 (Intraday Iron Fly):** backtesting is available (seven ways: no filter, plus each signal's quiet test) but has not been run yet.
- **Bot 5 (CAS Bingo):** backtesting is available, with two stated approximations: credit spreads are sized by maximum loss because historical margin isn't available, and freeing margin by buying back other positions is not replayed. It has not been run yet.
- **Bot 1 (Holdings Option Writer):** backtesting is planned but not yet built.

### 3.7 What this means for you

- **Treat the direction signals as context, not as a trading system.** They are honest, fully testable descriptions of unusual activity. On current evidence, following them does not pay, and fading them pays only a sliver that costs can easily swallow.
- **Keep the Long Scalper in paper mode.** Nine months of real prices say it loses money on every signal setting as configured.
- **Judge the premium-selling bots on their own evidence when it arrives.** Bots 1, 2 and 4 and CAS Bingo work on different principles (time decay, auction dynamics) from direction-following, and the Long Scalper's result says nothing about them either way.
- **Use your own size when judging signals.** Set your typical lot count on the Signals page so the cost bar reflects what *you* would pay.
- **Every number is re-checkable.** Every signal and bot backtest can be downloaded with its raw data. If a result looks too good or too bad, the evidence is there to inspect.

---

## Important notes

- **Nothing here is investment advice.** Past results do not guarantee future results: backtests replay history with today's settings, lot sizes and margins, and the auction regime and expiry rules are themselves under regulatory review.
- **Every number is re-checkable.** Each signal and bot backtest downloads with its raw data from the app (Signals page activity log, Bots page Activity rows).
