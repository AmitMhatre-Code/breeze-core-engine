# Signals, Bots and Backtests: How Breeze Modern Reads the Market and Trades

*A guide for options traders. Current as of 25 September 2026.*

This guide covers three things:

- how the app forms a view on where NIFTY and SENSEX are heading (**signals**);
- how its five automated strategies behave (**bots**);
- what we have learned by replaying all of it against months of real market data (**backtests**).

The executive summary is written for anyone. The later sections go into detail for traders who want to know exactly how each piece works.

---

## Executive summary

### What the app does

Breeze Modern has two layers of automation on top of your ICICI Direct account.

**Signals** are short-term readings of market direction for NIFTY and SENSEX. Every minute the app looks at the index futures and asks a simple question: *is something unusual happening, and which way?* The answer is **Bullish**, **Bearish**, **Quiet** or **No reading**. There are two ways of asking the question:

- **Volume expansion** looks for a price move that is unusually large *and* backed by unusually heavy trading. On NIFTY it also checks that the move is building new positions rather than closing old ones.
- **Momentum** looks for price trading above (or below) both its recent trend line and the day's average traded price, on heavy volume.

Each is read over 1, 5 and 15 minutes, for both indices, which makes twelve readings in all.

**Bots** are automated strategies. Some place trades on their own, and some propose a trade and wait for your approval. There are five:

| Bot | In one line |
|---|---|
| **Bot 1 · Holdings Option Writer** | Writes covered calls against shares you own, and optionally cash-backed puts, once a month. |
| **Bot 2 · Expiry-Day Index Writer** | On NIFTY/SENSEX expiry mornings, sells an out-of-the-money option or strangle sized to your margin, with a stop-loss armed automatically. |
| **Bot 3 · Long Scalper** | Buys one at-the-money NIFTY option when a signal fires, and manages it with a tight stop and a trailing profit ladder. |
| **Bot 4 · Intraday Iron Fly** | Sells a hedged at-the-money NIFTY iron fly during a quiet part of the day, and books it as premium decays. |
| **Bot 5 · CAS Bingo** | On expiry days, trades spreads or a strangle around the exchange's closing auction (the last 15–30 minutes). |

Only Bots 3, 4 and 5 use signals. Bots 1 and 2 are rule-based premium sellers that never look at direction.

### Built-in safety rails

- **A signal must prove itself before any bot can use it.** A bot cannot trade on a signal until that signal has been backtested over at least 30 calendar days of history.
- **Bots that trade on their own start in practice mode.** The scalpers and CAS Bingo ship in paper/simulation mode. A scalper cannot go live until it has completed at least one full practice day on the exact settings it will trade with.
- **Every trade has an exit plan from the moment it is placed.** That means stop-losses, profit targets, daily loss limits and a hard square-off time.
- **The app never fires orders in parallel.** Orders go to ICICI one at a time, so a rejected order can be retried safely and can never double-fill.

### What the backtests have found so far

We have been candid with ourselves here, and we are being candid with you.

1. **No signal predicts direction well enough to follow.** Across 117 trading sessions (April–September 2026) and about 17,200 signal calls, no reading was right more often than the market's natural up/down split.
2. **One signal is reliably wrong-way, which is itself useful.** After a volume-expansion call, the index tends to move slightly *against* the call over the next 15 minutes. Trading *against* the 1-minute expansion signal and holding for 15 minutes is the only pattern that held up when tested on data it had not been tuned on. The edge is small, strongest on SENSEX, and has not yet been confirmed on real option prices.
3. **The Long Scalper (Bot 3) lost money on every setting.** Replayed on real ICICI option prices from January to September 2026, all twelve signal settings lost money after costs. The losses ranged from about ₹43,000 to ₹2.26 lakh at a ₹25,000-per-trade budget. The bot won 40–55% of its trades, but its average loss was larger than its average win, and trading costs of about ₹95 a trade added up.
4. **Early practice sessions exposed design flaws, and those flaws have been fixed.** The Long Scalper kept re-buying the same signal after being stopped out. The Iron Fly was sized so large that its stop-loss sat inside the normal bid-ask noise and fired within seconds. Both were corrected.
5. **Several things have not been tested yet.** The improved signals (updated 21 September), Bot 2, Bot 4 and CAS Bingo have not been backtested. Until a fresh 30-day signal backtest runs, every signal-reading bot stands down by design.

### Bottom line

The app is built to be **honest about what works**. Every signal can be replayed against history, every bot can be backtested on real traded prices, and nothing is allowed to trade on a signal that has not been tested. So far the evidence says the direction signals are **not a reliable source of profit on their own**, and the Long Scalper should stay in practice mode. The premium-selling bots (1, 2, 4 and 5) are yet to be judged against history.

---

## Part 1 — Signals

### 1.1 What a signal is, and what it is not

A signal is a **short-lived opinion about the next few minutes of index direction**. It is not a forecast for the day, and it is not a buy/sell recommendation on any particular option.

Each reading is in one of four states:

| State | Meaning |
|---|---|
| **Bullish** | The reading has seen something that, by its rules, points up. |
| **Bearish** | The mirror: it points down. |
| **Quiet** | The reading is working normally and sees nothing unusual. |
| **No reading** | The reading *cannot* form an opinion right now: outside market hours, a data gap, still warming up, or the data is not trustworthy. |

**"No reading" is never treated as "Quiet".** A bot that needs a quiet market will not assume the market is quiet just because the signal cannot see. A bot that needs a direction will never trade on "No reading". When in doubt, bots stand aside.

### 1.2 The ground rules every signal follows

**It reads futures, not the index or options.** The NIFTY and SENSEX indices themselves have no traded volume, and volume is central to both signals. So they read the **near-month index futures**: NIFTY futures on NSE, and SENSEX futures on BSE. Option prices are never an input.

**It uses only what history can replay.** Every signal is calculated purely from one-minute price bars (open, high, low, close), traded volume, and open interest. These are exactly what ICICI serves in its historical data. That one rule is what makes every signal testable: any day's readings can be recreated after the fact, exactly as they would have appeared live.

We deliberately gave up richer live-only data, such as order-book depth and bid/ask pressure. We built and tested signals on that data earlier. They could only be judged by collecting live sessions a couple of days at a time, and they showed no edge. A signal that can be tested over six months in an afternoon is worth more than one that needs six months of waiting.

**It works only during continuous trading, 09:15 to 15:15.** After 15:15 the cash market moves into the **closing auction**, where the index shows an *indicative* value rather than traded prices. That is a different market, and the signals do not pretend otherwise. Outside these hours every reading is "No reading".

**It never reads the overnight gap as a move.** A gap-up open is not a breakout. No measurement window may span the overnight break or any silence longer than five minutes. Trend lines are adjusted for the gap (see Momentum, below).

**Nothing about a live reading is stored.** Only the current reading is kept. If you want to audit what a signal did on a given day, run a backtest of that day after 15:45: it reproduces the day's readings exactly from ICICI's history.

**SENSEX is labelled "thin data".** SENSEX futures trade very lightly: a median of about 20 contracts a minute, with no trade at all in roughly a third to a half of all minutes. ICICI also provides **no open interest for BSE contracts**. SENSEX readings therefore work with less information, and the app says so wherever they are shown.

### 1.3 Volume expansion

**The idea:** a move that is both **unusually large** and **unusually heavily traded** is a sign that real money is behind it.

**How it works, step by step:**

1. **Measure the move.** Over the chosen window (1, 5 or 15 minutes), measure how far the futures price moved and how many contracts traded.
2. **Compare with what is normal.** Rank that move and that volume against the last 120 windows of the same length, including earlier sessions. This is a ranking, not a fixed threshold. A 15-basis-point move is ordinary in the first minutes after the open and extraordinary at 1 pm, and the ranking adjusts for that automatically.
3. **Both must be in the top fifth.** The move must be larger than 80% of recent windows, and the volume heavier than 80% of recent windows. A big move on thin volume, or heavy volume with no move, is not a call.
4. **NIFTY only: check open interest.** Over at least 15 minutes (open interest moves slowly, so shorter windows are just noise), the app asks whether positions are being **built** or **closed**:

| Price | Open interest | What it means | Call |
|---|---|---|---|
| Up | Rising | New long positions | **Bullish** |
| Down | Rising | New short positions | **Bearish** |
| Up | Falling | Short covering (an unwind) | **Quiet**, not a call either way |
| Down | Falling | Long liquidation (an unwind) | **Quiet**, not a call either way |

An unwind is deliberately *not* read as a reversal. "The rally is short covering" means "new money is not behind this", not "this will turn". The signal only claims what it can support.

5. **SENSEX skips step 4.** With no open interest available, SENSEX expansion reads price and volume only. It cannot tell a genuine breakout from a blow-off, which is part of why it carries the "thin data" label.

**A few details that matter:**

- **A spike after a dead minute is ignored.** If the bar at the start of the window saw no trade, its price was just carried forward from earlier, so the "move" really happened over an unknown stretch of silence. Such readings are marked "No reading". This mostly affects SENSEX, where the backtests showed these stale-anchored spikes carried no information.
- **NIFTY expansion stands down on rollover days**, when open interest shifts from one month's contract to the next and stops meaning what it normally means.
- **Worked example (NIFTY, 15-minute window).** At 11:40 the futures have risen 0.18% over the last 15 minutes. That move is larger than 92% of the last 120 fifteen-minute windows, and volume ranks in the top 10%. Open interest has risen over the same 15 minutes. That is new longs, so the reading is **Bullish**. If open interest had fallen instead, it would read **Quiet**.

### 1.4 Momentum

**The idea:** a price that trades above its short-term trend *and* above the day's average traded price, on heavy volume, is in an up-move that participants are paying up for.

**How it works:**

1. Build candles of the chosen length (1, 5 or 15 minutes), aligned to 09:15.
2. When a candle completes, check three things:
   - **Trend:** did it close above its **9-candle exponential moving average** (EMA)?
   - **Value:** did it close above the day's **VWAP**, the volume-weighted average price of everything traded so far today?
   - **Participation:** does the candle's volume rank in the **top fifth** of the last 20 candles?
3. All three up means **Bullish**, and all three down means **Bearish**. Anything mixed is **Quiet**.

**How the trend line handles the overnight gap.** A 9-candle average of 15-minute candles needs over two hours of data, so if it started fresh every morning the 15-minute reading would say nothing until 11:30. Our backtests found exactly that: on every one of 117 sessions it was silent all morning. So the trend line now **carries over from the previous day, shifted by the overnight gap**. On a flat gapped open the first candle sits exactly on the line, so the gap alone can never trigger a call. Within a couple of hours yesterday's influence has faded almost entirely. VWAP, by contrast, always starts fresh each day, because it is by definition an average of today's trading.

### 1.5 Durations: 1, 5 and 15 minutes

Each mechanism runs at three durations. The duration is **both** the window the signal looks at **and** how long a call stands:

- A **1-minute** reading looks at the last minute. A call stands for one minute unless the next minute fires the same way again, in which case it extends.
- A **5-minute** or **15-minute** reading works the same way at its own scale.

Shorter durations fire far more often and are noisier. Longer ones fire rarely and speak about a longer stretch. On SENSEX the 1-minute readings are mostly silent, because most minutes carry no trade.

### 1.6 The twelve readings

| | NIFTY | SENSEX (thin data) |
|---|---|---|
| **Volume expansion** | 1m · 5m · 15m (with open interest) | 1m · 5m · 15m (price and volume only) |
| **Momentum** | 1m · 5m · 15m | 1m · 5m · 15m |

This grid is fixed. There are no user-tunable versions of a signal, because a signal that can be tweaked until it looks good on past data proves nothing. What *is* your choice is which cell a bot uses, and whether the bot **follows** it or **fades** it (see 1.9).

### 1.7 Where you see signals

- **The navbar** shows the **15-minute** reading for NIFTY and SENSEX, for whichever mechanism you pick on the Signals page. It is informational only.
- **The Signals page** shows, for each mechanism:
  - a plain-language description of what it watches;
  - all six current readings;
  - one sentence per reading summarising the last backtest;
  - whether that mechanism is currently available to bots.
- **Bots** read the cell you choose in their settings.

### 1.8 The 30-day gate

A signal mechanism becomes **available to bots** only once a completed signal backtest covers **at least 30 calendar days** on the mechanism's **current version**. Gaps in the data are allowed; it is the span that counts.

- The gate is about **coverage, not merit**. It guarantees you have seen a month of evidence before you arm a bot on a signal. It does not decide for you whether that evidence is good enough. The verdict is shown beside it for you to judge.
- It applies to **practice (paper/simulation) and live alike**.
- Whenever a signal's formula is improved, its version changes and the gate **closes again** until a fresh 30-day backtest has run. Any bot using that signal stands down, and its activity log says why.
- Backtests themselves are never gated.

**Current state:** both mechanisms were improved on 21 September 2026 (see Part 3). **A fresh 30-day signal backtest has not yet been run**, so signal-reading bots are currently standing down by design.

### 1.9 How a signal is judged

A signal backtest replays every reading over a chosen period and asks one question: **after a call, did the index move the called way, by enough to pay for a trade?** The way that question is scored matters as much as the answer:

- **Both directions are scored.** A signal that is *reliably wrong* carries just as much information as one that is reliably right; you would simply trade against it. Every reading is scored as **follow** (trade with the call) and as **fade** (trade against it).
- **Every horizon is scored.** Each call is checked 1, 5, 15 and 30 minutes later, not just at the end of its own duration. The backtests found that a 1-minute signal's information peaks around 15 minutes later, which a narrower test would have missed.
- **Money, not hit rate.** The headline is the **average move in the traded direction, after costs**, not the percentage of calls that were right. A 60% hit rate with small wins and large losses still loses money.
- **The cost bar reflects your size.** Brokerage is mostly a flat fee per order, so its cost per lot falls as you trade more lots. On the Signals page you set how many lots you typically trade, and the bar is priced at that size. It also includes the **bid-ask spread**, measured from real quotes.
- **Consistency across days, not one lucky week.** Results are averaged day by day first, then checked for how consistently they hold. A reading only "stands out" if the net result is positive, clearly consistent across days, and based on at least 20 sessions.

**Follow and fade in bots.** Bots that trade a direction (the Long Scalper, and CAS Bingo's debit spread) have a **"trade against the signal"** switch. With it on, a Bullish call buys puts and a Bearish call buys calls.

---

## Part 2 — Bots

### 2.1 What all bots have in common

**Ways to run a bot**

| Mode | What happens |
|---|---|
| **Off / Manual** | The bot never fires by itself. You can open its run sheet at any time, see a fully priced proposal, and place it with a click. |
| **Telegram approval** (Bots 1 and 2) | The bot runs on its schedule and sizes the trade, then sends the priced proposal to your linked Telegram with **Approve / Reject** buttons. **Silence never trades.** An unanswered proposal is re-priced and re-sent until the day's cut-off. |
| **Automatic** (Bots 1 and 2) | The bot places the trade itself on its schedule. |
| **Paper** (Bots 3 and 4) / **Simulation** (Bot 5) | Full logic on live prices, with fills simulated at the quoted touch and realistic trading costs. No order reaches the exchange. This is the default. |
| **Live** (Bots 3 and 4) / **Autonomous** (Bot 5) | Real orders. The scalpers need at least **one completed paper day on the exact settings** first, and change any setting that affects money and that evidence no longer counts. |

**Safety rails every bot shares**

- **Your ICICI login must be live.** ICICI sessions end every night. Where a bot needs a session and there isn't one, it sends Telegram reminders and waits, up to a cut-off. It never trades on stale credentials.
- **Licence status.** If the deployment is in read-only mode, bots do not open trades.
- **One order at a time.** Orders go to ICICI strictly one after another, never in parallel. This keeps within ICICI's rate limits, and it means a refused order was genuinely refused, so it can be retried without any risk of a double fill.
- **Never partially funded.** If even one lot does not fit the budget or margin, the bot skips with a logged reason. It never trades a smaller version of the idea.
- **Limit orders, not market orders.** Entries and exits use limit prices a small band beyond the quote, stepping further only on retry.
- **Protection is armed as soon as the trade exists.** Bot 2's stop is armed the moment the exchange confirms the fills. If a stop cannot be armed, the run is marked **Partial**, not Failed, and you are told to set a stop by hand.
- **Trading costs are real.** Brokerage, STT, exchange and SEBI fees, stamp duty, GST and an allowance for the bid-ask spread all come from one shared cost model (Settings → Trading Costs). Every rupee figure the bots show is **after** those costs.
- **Everything is logged.** Every run, every decision not to trade and why, and every trade with its entry, exit and reason appears in the bot's Activity log and can be downloaded.
- **Bots don't collide.** Bots 1 and 2 run in the priority order you set, and each one's committed margin is subtracted before the next one sizes. Bot 5 will not enter an index and expiry where a stop-loss group rule (for example Bot 2's) is already armed, because the two would interfere.

### 2.2 Bot 1 · Holdings Option Writer

**What it is for:** earning option premium on stock you already hold, without ever selling naked calls.

**Universe:** your demat holdings that have NSE stock options. Stock options are monthly only, so the bot writes the current or next month's expiry.

**When it runs:**
- **Automatically or via Telegram approval** on a firing day a set number of **trading** days before the monthly expiry (default: 3), from 09:20.
- If you are not logged in, it reminds you every 15 minutes until 12:00, then gives up for the day.
- You can also run it manually at any time.

**Calls (on by default for each stock, with a per-stock opt-out):**
- **Covered, always.** The number of call lots can never exceed the shares you can actually deliver, minus any calls you already have open on that stock, across all expiries.
- **Pledged shares count as coverage, but are flagged.** You own them, but you would need to unpledge them before expiry to deliver. The proposal shows "N of M lots pledged" so you see that obligation up front.
- **Shares blocked for another purpose do not count.** Examples are a pending sale or a settlement hold.
- **Strike:** a safety distance above spot (default **5%**), rounded to the next listed strike *further* from spot, never closer.

**Puts (off by default, opt-in per stock):**
- These are limited by a **delivery-cash budget** you set: the total cash you would need if every put were assigned (strike × lot size × lots).
- **Strike:** a safety distance below spot (default **5%**), rounded further from spot.

**How it prices and chooses:**
- **Premium is quoted at the bid**, never the last traded price. Stock options have wide spreads, and the bid is what you would actually receive.
- **Margin is shown with netting** against your existing positions, as the exchange would compute it.
- If there is not enough margin or budget for everything, stocks are funded in **your priority order**. A stock that doesn't fit is skipped and the walk continues, so cheaper stocks further down still get written.

**Before placing:**
- A proposal is valid for 15 minutes. At approval it is **re-priced**, and if the bid has moved materially the order is not placed.
- Off-market indicative prices are refused outright.

**Exit:** Bot 1 does not arm an automatic exit. These are considered monthly positions that you manage, for example with the Portfolio page's profit-booking/stop-loss tools.

### 2.3 Bot 2 · Expiry-Day Index Writer

**What it is for:** collecting the rapid time decay of NIFTY and SENSEX options on their expiry day.

**When it runs:**
- **Only on expiry days**, read from the exchange's own expiry list rather than assumed from a weekday. The exchanges have moved expiry days before.
- Entry at **09:30** by default.
- If you are not logged in, you get Telegram reminders from 08:00 every 15 minutes. The bot enters as soon as a session appears, up to **12:00**, after which it skips the day. (An entry near noon has given up much of the day's decay while keeping all the risk. That trade-off is accepted in return for simplicity.)

**What it sells.** You shortlist one or more of:
- a **naked put** (the default);
- a **naked call**;
- a **short strangle** (both).

Each leg sits a safety distance from spot (default **2%** on each side, set separately for calls and puts).

**How it chooses among the shortlist.** It picks the one that pays the most **premium per rupee of margin**, not the most premium in total. A strangle always collects more premium than either leg alone, so ranking on total premium would pick the strangle every time. The strangle's margin is priced as one position so that the exchange's netting benefit is counted fairly.

**How much it sells:**
- Each index gets a **share of your free margin** (default **30%** each). If NIFTY and SENSEX ever expire on the same day, the one with higher priority sizes first.
- Size is estimated, then **confirmed with ICICI's own margin calculator** before any order goes out.

**Exit, armed automatically once the fills are confirmed:**
- **Stop-loss:** a multiple of the premium collected. The default of **1×** means exit if the loss equals the premium received.
- **Profit booking:** a share of the premium captured. The default of **50%** means buy back when the option has halved.
  - At **100%** no target is set and the position is left to expire worthless, with only the stop live.
  - On a strangle, profit is booked only when **both** legs have decayed, so the bot never buys back one side and leaves the other naked.

**Approval:** Telegram approval by default, or fully automatic.

### 2.4 Bot 3 · Long Scalper

**What it is for:** catching short bursts of NIFTY movement by buying options, with risk capped at the premium paid.

**What it trades:** one **at-the-money NIFTY option** on the nearest weekly expiry. It buys a call on a bullish signal and a put on a bearish one, or the reverse with **fade** switched on. Expiry-day trading is off by default.

**When it trades:**
- Inside session windows you set. The defaults are **09:35–11:30** and **13:30–15:10**.
- There is a hard square-off at **15:15**.

**Entry:**
- The signal is the one you choose from the grid. The shipped default is **Volume expansion, 15 minutes, fade**.
- **Size** is a fixed premium budget per trade (default **₹25,000**): as many whole lots of the ATM option as that buys. If it can't afford one lot, it skips.
- **One position at a time.** A signal that fires while a trade is open is ignored, not queued.
- **One trade per signal.** Once the bot has traded a signal, it will not trade again until that signal has **switched off and fired afresh**. Without this rule the bot re-bought the same stale signal seconds after being stopped out (see Part 3.4).
- **Buys with a limit** slightly above the ask. If not filled within a few seconds after two re-prices, it **abandons the trade rather than chase it**.

**Exits: the three-level trailing ladder.** Every level is priced off the **bid**, the price you could actually sell at. Defaults are in option points:

| Stage | Trigger | Action |
|---|---|---|
| Initial stop | Option falls **6 pts** | Exit |
| Level 1 | Option rises **5 pts** | Move the stop to entry **+1.2 pts** (roughly covers costs, so a reversal from here exits about flat) |
| Level 2 | Option rises **8 pts** | Move the stop to entry **+5 pts** |
| Level 3 | Option rises past **10 pts** | Stop trails **3 pts** behind the highest price reached, so winners can run |
| Opposite call | The signal calls the other way | Exit |
| Square-off | 15:15 | Exit |

The stop never moves backwards. Note that **a call simply lapsing does not close the trade**. Only the stop, the trailing stop, an opposite call or the square-off do. The accepted cost is that a quiet trade can sit open, holding its premium, until square-off.

**Circuit breakers:**
- A **daily loss limit** (default **₹10,000**, counting open losses and costs). Hitting it closes everything and stops the bot for the day.
- **Three losing trades in a row** pause the bot (default **30 minutes**).
- The bot also stands down before it would eat into the ICICI request allowance needed for your manual trades and square-offs.

**Why costs matter so much here.** A round trip on one NIFTY lot costs roughly **₹100**, about 1.5–2 option points. A scalper that trades often can lose its daily limit to costs alone while roughly breaking even on price. Every Long Scalper report therefore shows costs as a headline number.

### 2.5 Bot 4 · Intraday Iron Fly

**What it is for:** earning premium from a calm midday NIFTY market, with a strictly limited worst case.

**The structure:**
- **Sell** the at-the-money call and put.
- **Buy** protective wings **150 points** away on each side.
- Wings are rounded *outward*, so the fly is never narrower than asked. An optional rule widens the wings (for example to 200 points) when India VIX is above a level you set.

**When it trades:** inside session windows you set. The default is **11:30–13:30**, which is typically the calmest part of the day. There is a hard square-off at 15:15. Expiry-day trading is off by default.

**Size:**
- It takes the largest whole-lot fly whose margin fits under a **rupee ceiling** (default **₹25,000**, about 3 lots).
- Margin is confirmed with ICICI on all four legs together, so the hedge benefit counts.
- If one lot doesn't fit, it skips.

**Entry order:**
- **Wings first, then the short legs.** Selling first would leave naked shorts in place, which the broker would reject for margin.
- If a wing won't fill, whatever did fill is unwound and the attempt abandoned.
- If a short leg fails after the wings are on, the remaining position is closed immediately.

**Exits (whichever comes first):**

| Exit | Default |
|---|---|
| **Profit:** the credit has decayed by a set share | **15%** |
| **Stop:** loss reaches a share of the credit | **20%** of the credit |
| **Drift stop:** spot moves away from the entry strike | **0.35%** |
| Optional flat rupee stop | Off by default |
| Session window ends / 15:15 square-off / daily loss limit | — |

Profit and loss are measured at the prices it would actually cost to close: shorts at the ask, wings at the bid. The **drift stop** is the important one. Once spot has moved 0.35% away from the short strikes, losses on the tested side accelerate faster than a rupee stop can react. On exit, the shorts are bought back first and then the wings are sold.

**Re-entry.** After a fly closes, the next one waits until **both** of these hold:
- at least **15 minutes** have passed;
- NIFTY's range over the last **10 minutes** is within **0.15%**.

Waiting alone would re-enter into a move that is still running, and a calm-range test alone could re-fire again and again in chop.

**Optional entry filter** (off by default):
- **VIX not rising:** skip if India VIX has risen more than 2% over the last 15 minutes. Rising volatility marks up every short leg at once.
- **Signal quiet:** only open a fly while a chosen signal has **no** live call in either direction, because a call means the signal thinks a move is under way. This is the only way Bot 4 uses signals, and choosing it brings the 30-day gate into play.

Both filters **fail closed**: if VIX or the signal can't be read, the fly waits.

### 2.6 Bot 5 · CAS Bingo

**The market it trades.** Since August 2026, NSE and BSE close F&O stocks with a **closing auction session (CAS)**:
- Continuous trading stops at **15:15**.
- Auction orders are collected **15:20–15:30** and matched by **15:35**.
- Index derivatives keep trading until the close, but the index itself prints an **indicative** value built from the auction.
- **Expiry settles on the auction price.**

SENSEX has swung 2–3% inside the auction on some expiry days, and expiring options have moved hundreds of percent. CAS Bingo is built to trade that window. (SEBI is consulting on changes to expiry settlement, which could change this regime.)

**When it trades:**
- **Expiry days only**, per index (NIFTY and SENSEX).
- Two entry windows: **pre-CAS 14:30–15:15** and **CAS 15:15–15:29**.
- At most **one entry per index per day**. A stopped-out trade is not re-entered.

**Modes:** Off (manual run sheet), **Simulation** (the default) and Autonomous. The manual run sheet prices all five structures side by side for each expiring index, and you pick one.

**Strategies (you choose one):**

| Strategy | What triggers it | Structure (defaults) |
|---|---|---|
| **Debit spread** (default) | A signal call inside a window that **holds for 3 minutes**. Bullish buys a bull call spread, bearish a bear put spread (reversed with fade). | Buy ATM, sell 0.5% further out. Budget **₹10,000** of premium. |
| **Credit spread, before the auction** | The index has moved **0.5%** from the day's open, **then** the signal calls against that move. After a rally it sells calls; after a fall it sells puts. | Sell 0.5% from the open, buy 1.0% from the open as the hedge. Margin budget **₹2 lakh**. |
| **Credit spread, inside the auction** (from 15:20) | No signal (it can't be read in the auction). The side is where the indicative index sits versus the open. | Sell **1%** beyond the indicative index, hedge one width further out. Only if the spread still pays at least **10%** of its width. |
| **Long strangle** | The clock: **15:15** | Buy a call and a put, each 0.5% out of the money. Budget **₹10,000**. |

**About the credit spreads:**
- They are **reversal bets**: they wager the index returns towards the day's open.
- Because strikes are measured from the open, the sold leg can already be in the money when entered. That is the intended bet, and the proposal shows it plainly.
- The in-auction version sells options that, at the current indicative index, should expire worthless but still carry a price. It is fading the spike.

**Execution:**
- **Buy leg first, confirmed filled, then the sell leg.** If the sell leg fails, the buy leg is unwound.
- **Own exits**, measured against the net premium:
  - Credit spread: book at **80%** of the credit captured; stop when the loss equals **100%** of the credit.
  - Debit spread and strangle: book at **+100%** of the debit; stop at **−50%**.
  - If neither triggers, the position **settles at expiry**.

**Freeing margin if needed.** If free margin can't cover the chosen trade, CAS Bingo can buy back **your own short options on the same index and expiry** to release it:
- only those that have already captured at least **80%** of their premium, most-captured first;
- the buy-back price is capped, so every liquidation is itself a profitable exit;
- it adds a **10% safety buffer** and re-checks your actual margin after each round.

In Simulation it only reports what it would buy back.

**Signals and the gate:**
- The debit and pre-auction credit spreads read a signal, so the 30-day gate applies to them in both Simulation and Autonomous.
- The long strangle and the in-auction credit spread read no signal.

> ⚠️ **Warning shown in the app:** ICICI may square off your positions at an extreme loss if mark-to-market or margin requirements spike during CAS.

### 2.7 Bots at a glance

| | Bot 1 Holdings Writer | Bot 2 Expiry Writer | Bot 3 Long Scalper | Bot 4 Iron Fly | Bot 5 CAS Bingo |
|---|---|---|---|---|---|
| **Underlying** | Your F&O stocks | NIFTY, SENSEX | NIFTY | NIFTY | NIFTY, SENSEX |
| **When** | Monthly, N days before expiry | Expiry morning | Intraday windows | Midday window | Expiry, last 45 min |
| **Position** | Short covered calls / cash-backed puts | Short option or strangle | Long ATM option | Short ATM iron fly | Spreads or long strangle |
| **Uses signals** | No | No | Yes (entry) | Optional (filter) | Yes (spreads) |
| **Sizing** | Holdings and cash budget | % of free margin | Premium budget | Margin ceiling | Premium or margin budget |
| **Exit** | Manual | Auto stop and target | Trailing ladder | Decay / stop / drift | Target / stop / settle |
| **Default mode** | Telegram approval | Telegram approval | Paper | Paper | Simulation |
| **Backtested yet?** | Not yet available | Not yet run | **Yes** (Part 3.3) | Not yet run | Not yet run |

---

## Part 3 — Backtests

### 3.1 How a backtest works

A backtest replays history and asks: *what would this have done, exactly as it is configured today?*

**Signal backtests:**
- One run replays all twelve readings over a period you choose (last day, week, month or a custom range).
- It scores every call as described in Part 1.9.
- The result comes as a downloadable zip containing every bar, reading and call, so any number in it can be checked independently.

**Bot backtests replay a bot minute by minute on real traded option prices from ICICI.** Some rules apply:
- **Real prices only.** Fills use actual traded ICICI option prices, down to one-second bars where available, with the bid-ask spread modelled from observed quotes (a median of about 0.25% of premium on NIFTY ATM options). No theoretical or model prices are ever used. A day with no traded data is **skipped and reported**, never filled in with a model.
- **Today's configuration.** Every past day is replayed with your current settings, today's lot size and today's margin. The question is "how would this bot, as I have it now, have done?"
- **Every signal setting side by side.** For example, the Long Scalper is replayed twelve ways: two mechanisms × three durations × follow or fade. The results are shown as a comparison table, with your saved setting marked.
- **Full costs**, from the same Trading Costs model the live bots use.
- **Data is fetched on demand, outside market hours only.** ICICI allows a fixed number of requests per day, shared with your live trading. Backtest downloads never run during market hours and never eat into the reserve kept for placing and cancelling orders. A long period can take several evenings to fetch completely. Everything fetched is kept, so each evening builds on the last. Until then, days without data are listed as "awaiting data".

**What backtests cannot tell you.** They use today's lot size and margin on past days. Liquidity and queue position are only approximated. And the past does not repeat exactly. A backtest is evidence, not a promise.

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

## Glossary

| Term | Meaning |
|---|---|
| **ATM / OTM / ITM** | At, out of, or in the money: an option's strike relative to the current price. |
| **CE / PE** | Call option / put option. |
| **bps (basis point)** | 0.01%. On NIFTY at 26,000, 1 bps ≈ 2.6 points. |
| **Open interest (OI)** | The number of futures/option contracts outstanding. Rising OI means new positions are being opened; falling OI means positions are being closed. |
| **VWAP** | Volume-weighted average price: the average price of everything traded so far today, weighted by quantity. |
| **EMA** | Exponential moving average: a trend line that weights recent prices more heavily. |
| **Call (signal)** | A Bullish or Bearish reading. It stands for the reading's duration, and is extended if it re-fires. |
| **Follow / Fade** | Trade in the direction of a signal's call / trade against it. |
| **Hit rate** | Share of calls followed by a move the called way large enough to pay costs. |
| **Backtest** | A replay of a signal or bot against historical market data. |
| **Paper / Simulation** | Running a bot on live prices with simulated fills: no real orders. |
| **Friction / costs** | Brokerage, taxes, exchange fees and the bid-ask spread paid on each trade. |
| **Drawdown** | The largest peak-to-trough fall in running P&L over a period. |
| **SPAN / ELM** | The exchange's margin components: SPAN covers the risk of the position, and ELM (extreme loss margin) is an additional buffer. |
| **Iron fly** | Short ATM call and put, with long OTM call and put wings limiting the maximum loss. |
| **Strangle** | A call and a put at different OTM strikes, both short (Bot 2) or both long (CAS Bingo). |
| **Credit / debit spread** | Two options of the same type: a credit spread collects premium (sell nearer, buy further), a debit spread pays premium (buy nearer, sell further). |
| **CAS** | Closing Auction Session: the 15:15–15:35 auction that sets the closing and expiry settlement price. |
| **Indicative index** | The index value implied by auction prices during CAS, before the final auction price is set. |
| **PB/SL** | Profit booking / stop-loss rules you can arm on a group of positions from the Portfolio page. |
| **Rollover day** | Days around month-end when futures positions move from the expiring contract to the next one. |

---

## Important notes

- **Nothing in this guide is investment advice.** The signals, bots and backtests are tools. How and whether to use them is your decision.
- **Past results do not guarantee future results.** Backtests replay history with today's settings, lot sizes and margins. Real markets change, and the auction regime and expiry rules are themselves under regulatory review.
- **Options trading carries substantial risk,** including the loss of more than your premium when selling options. Credit spreads and iron flies limit this; naked short options do not.
- **Live trading depends on your broker session,** ICICI's request limits and exchange rules. Bots stand aside rather than guess when any of these are uncertain, which means they will sometimes not trade when you might expect them to.
