# Bots

Bots are automated strategies that scan, size and place trades on your behalf, within limits you set. There are five. Each can be run by hand, can ask your approval, or can trade on its own, and every decision it makes, including every decision not to trade, is logged.

![The Bots page](images/dark/bots.png)

| Bot | In one line |
|---|---|
| **Holdings Option Writer** | Writes covered calls against shares you own, and optionally cash-backed puts, once a month. |
| **Expiry-Day Index Writer** | On NIFTY/SENSEX expiry mornings, sells an out-of-the-money option or strangle sized to your margin, with a stop armed automatically. |
| **Long Scalper** | Buys one at-the-money NIFTY option when a signal fires, and manages it with a tight stop and a trailing ladder. |
| **Intraday Iron Fly** | Sells a hedged at-the-money NIFTY iron fly during a quiet part of the day, and books it as premium decays. |
| **CAS Bingo** | On expiry days, trades spreads or a strangle around the exchange's closing auction. |

The Long Scalper, CAS Bingo's spreads and (optionally) the Iron Fly read a [signal](signals.md). The two writers never look at direction.

> [!IMPORTANT]
> Bots place real orders in your account when set to trade on their own. Start every bot in its manual, approval or simulation mode, check its Activity log and its backtest, and only then let it trade unattended. Nothing here is investment advice.

## The bot card

![A bot card](images/dark/bot-card.png)

| Part | What it shows or does |
|---|---|
| **Priority N** | The bot's place in the queue. Lower runs first and sizes against your margin before the others, so two bots never commit the same margin. Click it to pick a slot; taking a slot that another bot holds swaps the two. |
| Play icon | **Start a run** now, by hand (Holdings Writer, Expiry Writer, CAS Bingo). Opens the run sheet. |
| History icon | **Backtest** this bot. See [Backtesting a bot](#backtesting-a-bot). |
| Gear icon | Opens the bot's settings. |
| Status | **Idle** (not armed), **Armed**, or **Closing** (switched off with a real position still open, which it manages to its exit). A pill reads **Asks first** or **Simulation** when the bot is armed but cannot reach the exchange on its own. |
| What next | When it will act next, for example **Would fire 3 trading days before expiry** or **Trades 09:35–11:30 and 13:30–15:10, flat by 15:15**. |
| Figures | **Last run** or **Cycles today**, **Net P&L** today, and **Last backtest** (clearly marked as a backtest, never as money made). |
| Mode switch | How the bot runs (below). The line under it says in plain words what the mode does. |

### Modes

| Bots | Modes | What they mean |
|---|---|---|
| Holdings Writer, Expiry Writer | **Manual** · **Semi-auto** · **Auto** | **Manual**: runs only when you start a run. **Semi-auto**: sizes the trade on its schedule and asks you on Telegram; nothing is placed until you approve. **Auto**: sizes and places the trade on its schedule without asking. Semi-auto needs a linked Telegram chat ([Telegram Alerts](settings-automation.md#telegram-alerts)). |
| Long Scalper, Iron Fly | **Off** · **Simulation** · **Live** | **Off**: nothing is evaluated or recorded. **Simulation**: runs on live prices but places no orders; fills are simulated at the quoted price, with slippage and charges. **Live**: places real orders, unattended. |
| CAS Bingo | **Off** · **Simulation** · **Autonomous** | **Off**: never fires on its own; you start runs yourself. **Simulation**: runs the full logic on expiry days, places no orders. **Autonomous**: places real orders on expiry days when its trigger fires. |

Switching a scalper to **Live** opens **Enable live trading?**, which shows its daily loss cap (and the combined cap if the other scalper is live too), its simulation record on these exact settings, and its last backtest. A scalper can only go live after at least **one completed Simulation day on the exact settings** it will trade with; change a setting that affects money and that evidence no longer counts. **Enable Autonomous?** does the same job for CAS Bingo.

In semi-auto, Telegram approval works like this: the bot sends the priced trade with **Approve** and **Reject** buttons. **Silence never trades.** An unanswered proposal is re-priced and re-sent until the day's cut-off.

## Safety rails every bot shares

- **Your ICICI login must be live.** ICICI sessions end every night. When a bot needs a session and there is none, it sends Telegram reminders and waits, up to a cut-off. It never trades on stale credentials.
- **Read-only mode stops new trades.** If your license is not active, bots do not open positions.
- **One order at a time.** Orders go to ICICI strictly one after another, never in parallel, so a refused order can be retried without any risk of a double fill.
- **Never partly funded.** If even one lot does not fit the budget or margin, the bot skips with a logged reason rather than trading a smaller version of the idea.
- **Limit orders, not market orders.** Entries and exits use limit prices a small band beyond the quote.
- **Protection is armed as soon as the trade exists.** If a stop cannot be armed, the run is marked **Partial**, not failed, and the card tells you to set one by hand (see [Profit Booking / Stop Loss](portfolio.md#profit-booking--stop-loss)).
- **Costs are real.** Every rupee figure a bot shows is after brokerage, taxes, exchange fees and an allowance for the bid-ask spread, from [Trading Costs](settings-trading.md#trading-costs).
- **Live bots always ask ICICI for margin**, whatever the SPAN-file settings say.
- **Signals must prove themselves first.** A bot cannot use a signal until that signal has passed the [30-day gate](signals.md#the-30-day-gate).

## Settings

The gear icon opens a settings panel with tabs. Changes apply to the next run. The footer shows **Unsaved changes** or **All changes saved**, with **Discard** and **Save**.

### Holdings Option Writer

Earns premium on stock you already hold, without ever selling naked calls. It writes the current or next month's stock options on your demat holdings that have NSE options.

| Tab | Settings |
|---|---|
| **Scrips** | One row per holding: **CE lots** and **PE lots** to write (blank means every covered lot for calls, none for puts), **CE %** and **PE %** distance from spot (blank means the defaults), and **Priority** (who is funded first when margin or cash runs short; lower goes first). The panel shows each holding's **Free**, **Pledged** and **Blocked** lots, and calls already written against it. |
| **Schedule** | **Days before expiry** (trading days; default 3), **Expiry** (**Current month** or **Next month**), **Start (IST)** and **Until (IST)** for the day's window, and **Remind every (min)** if you are not logged in. |
| **Limits** | **Default call distance %** above spot and **Default put distance %** below spot (default 5% each), **Delivery-cash budget (₹)** (the most every written put would cost if all were assigned), and **Proposal validity (minutes)**. |

How it decides:

- **Calls are always covered.** Call lots can never exceed the shares you can deliver, minus calls already open on that stock. **Pledged** shares count but are flagged (you would need to unpledge them to deliver); **blocked** shares do not count.
- **Puts are opt-in** and limited by the delivery-cash budget.
- Strikes are rounded **further** from spot, never closer. Premium is quoted at the **bid**, and margin is netted against your existing positions.
- Stocks are funded in your priority order; one that does not fit is skipped and the rest still get written.
- A proposal is re-priced at approval and not placed if the bid has moved materially.
- It arms **no automatic exit**: these are monthly positions you manage yourself.

### Expiry-Day Index Writer

Collects the rapid time decay of NIFTY and SENSEX options on their expiry day. It runs only on expiry days, read from the exchange's own expiry list.

| Tab | Settings |
|---|---|
| **Indices** | For each of NIFTY and SENSEX: whether to trade it, the **Strategies** shortlist (**Naked PE**, **Naked CE**, **Short strangle**), **CE distance %** and **PE distance %** from spot (default 2%), **Margin cap %** (share of free margin; default 30%) and **Priority** if both expire on the same day. |
| **Schedule** | **Entry (IST)** (default 09:30), **Remind from (IST)**, **Until (IST)** (default 12:00; no session by then and it skips the day) and **Remind every (min)**. |
| **Exits** | **Book at % of premium** (default 50%: buy back when the option has halved; 100% lets it expire with only the stop live) and **Stop at N × premium** (default 1: exit when the loss equals the premium collected). |

With more than one strategy shortlisted, it picks the one that pays the most **premium per rupee of margin**, pricing a strangle's margin as one position. Size is confirmed with ICICI's margin calculator before any order goes out. The stop and target are armed the moment the fills are confirmed. On a strangle, profit is booked only when **both** legs have decayed.

### Long Scalper

Catches short bursts of NIFTY movement by buying one at-the-money option on the nearest weekly expiry: a call on a bullish signal, a put on a bearish one (or the reverse when fading).

| Tab | Settings |
|---|---|
| **Schedule** | Trading windows (**Add window** / **Remove**; defaults 09:35–11:30 and 13:30–15:10), **Flat by (square-off)** (default 15:15) and **Trade on expiry day** (off by default). |
| **Signal** | **Signal** (Volume expansion or Momentum), **Duration** (1, 5 or 15 min) and **Direction** (follow, or trade against the signal). It says if the chosen signal is not yet available to bots. **Premium outlay** (₹): capital per trade; lots = outlay ÷ option price. |
| **Exits** | The trailing ladder, in option points: **Initial stop**, **Level 1 trigger** / **Level 1 locks**, **Level 2 trigger** / **Level 2 locks**, **Runner trigger** (starts the trailing runner rather than taking profit) and **Runner trails by**. |
| **Risk** | **Daily loss cap** (₹; hitting it closes the position and disables the bot until you re-enable it), **Consecutive losses** before a pause, **Cooldown** (min), and **Broker calls held back** (reserved so scalping can never starve your manual trading). |

How it trades:

- **One position at a time**, and **one trade per signal**: after trading a signal it waits for that signal to switch off and fire afresh.
- It buys with a limit slightly above the ask and **abandons** the entry rather than chasing if it does not fill quickly.
- Every exit level is priced off the **bid**. The stop moves up the ladder as the option rises and never moves back:

| Stage | Default | Action |
|---|---|---|
| Initial stop | Option falls 6 pts | Exit |
| Level 1 | Option rises 5 pts | Stop moves to entry + 1.2 pts |
| Level 2 | Option rises 8 pts | Stop moves to entry + 5 pts |
| Runner | Option rises past 10 pts | Stop trails 3 pts behind the highest price |
| Opposite call | The signal calls the other way | Exit |
| Square-off | 15:15 | Exit |

- **A call simply ending does not close the trade.** Only the stops, an opposite call, or the square-off do.
- A round trip on one NIFTY lot costs roughly ₹100, so costs matter a great deal to a scalper; every report shows them.

### Intraday Iron Fly

Earns premium from a calm midday NIFTY market with a strictly limited worst case: it sells the at-the-money call and put and buys protective wings.

| Tab | Settings |
|---|---|
| **Schedule** | Trading windows (default 11:30–13:30), square-off time, and **Trade on expiry day**. |
| **Structure** | **Wing width** (pts; default 150, always rounded outward), **Margin ceiling** (₹; default ₹25,000: the largest whole-lot fly that fits, checked with ICICI on all four legs), and **Widen above VIX** / **Widened wing width** (0 switches the rule off). |
| **Exits** | **Book at credit decay of** % (default 15%), **Stop at credit loss of** % (default 20%), **Spot drift stop** % (default 0.35%; checked first, because once spot moves away from the short strikes losses accelerate), and **Hard stop** (₹; 0 switches it off). |
| **Re-entry** | **Cooldown** (min; default 15) and **Range window** / **Spot must stay within** (default 10 minutes within 0.15%). Both must clear before another fly. |
| **Entry filter** | **Filter**: **None — the re-entry gate only**, **India VIX not rising** (with **Look back** and **Skip if VIX rose more than**), or **Only while a signal is quiet** (the fly opens only while the chosen signal has no live call either way). Both filters fail closed: if VIX or the signal cannot be read, the fly waits. |
| **Risk** | Daily loss cap, consecutive losses, cooldown and broker calls held back, as for the Long Scalper. |

It places the **wings first**, then the short legs; if a wing will not fill, anything filled is unwound. Profit and loss are measured at what it would actually cost to close (shorts at the ask, wings at the bid). On exit it buys back the shorts first, then sells the wings.

### CAS Bingo

Trades the exchange's **closing auction session (CAS)** on expiry days. Continuous trading stops at 15:15, auction orders are collected 15:20–15:30 and matched by 15:35, and **expiry settles on the auction price**, which can move sharply.

| Tab | Settings |
|---|---|
| **Schedule** | The indices it trades (NIFTY, SENSEX; expiry days only) and two entry windows: pre-CAS (default 14:30–15:15) and CAS (default 15:15–15:29). At most one entry per index per day. |
| **Strategy** | What Simulation and Autonomous deploy: **Credit spread**, **Debit spread** or **Long strangle**, plus the signal choice for the spreads. |
| **Credit** | **Margin to deploy** (lakh), **Move from open that arms it** %, **Inner (sold) leg from open** % and **Outer (bought) leg from open** %, **Gap beyond the indicative index** % and **Minimum credit** (% of width) for the in-auction version, **Profit target** and **Stop-loss** (% of credit). |
| **Debit** | **Net premium to pay** (₹), **Sustained for** (min: how long the signal call must hold), **Inner (bought) leg from spot** % and **Outer (sold) leg from spot** %, **Profit target** and **Stop-loss** (% of debit). |
| **Strangle** | **Premium to pay** (₹), **Entry time**, **CE distance from spot** % and **PE distance from spot** %, **Profit target** and **Stop-loss** (% of debit). |
| **Liquidation** | Whether it may buy back your own short options on the same index and expiry to free margin, only those with at least **Minimum premium captured** %, most captured first, with a **Safety buffer** %. |

The strategies:

- **Debit spread**: on a signal call that holds for the set time, buys a bull call spread (bullish) or bear put spread (bearish).
- **Credit spread, before the auction**: after the index moves from the open, and the signal then calls against that move, it sells the side that bets on a return towards the open.
- **Credit spread, inside the auction** (from 15:20): no signal; it sells beyond the indicative index while the spread still pays a minimum credit.
- **Long strangle**: at a set time, buys a call and a put out of the money.

It buys the long leg first and sells only once that has filled; if the sell leg fails, the buy leg is unwound. If neither the target nor the stop fires, the position settles at expiry. It will not enter an index and expiry where a Profit Booking / Stop Loss rule is already armed.

> [!WARNING]
> ICICI may square off your positions at an extreme loss if mark-to-market or margin requirements spike during the closing auction.

## Running a bot by hand

The play icon opens the **run sheet**, a priced proposal you can check and place yourself.

![A manual run sheet for the Holdings Option Writer](images/dark/bot-run-sheet.png)

- **Holdings Writer and Expiry Writer**: the header shows when the proposal was priced and when it expires, with **Re-price**. Each proposed leg has a checkbox (untick to leave it out), **Contract**, **Spot**, **Strike**, **Dist %** from spot and **Lots** (both editable), **Bid**, **Premium**, **Margin** and, for puts, **If assigned**. Notes under a contract flag pledged or blocked shares. Bids in amber are indicative: priced off the last trade because there is no live bid, so you can plan but not place until the market opens. **N produced nothing** lists holdings that could not be written, with the reason. The footer totals **Premium**, **Margin needed** and **Delivery headroom**, with **Cancel** and **Execute N orders**. After placing, a **Placement result** shows what went through and whether the exit was armed.
- **CAS Bingo**: all five structures priced side by side for each expiring index, with **Structure**, **Legs (in order)**, **Lots**, **Net premium** and **Margin / cost**, and an **Execute** button on each. It warns you if a Profit Booking / Stop Loss rule is armed on the same expiry.

## Activity

The **Activity** section at the bottom of the page lists every scan, order and skip across all bots, including the days nothing happened and why.

![The bot Activity log](images/dark/bot-activity.png)

| Control or column | What it does |
|---|---|
| **Today** / **Week** / **Month** / **Custom** | The period shown. |
| **Started**, bot, **Trigger**, **Outcome**, **Reason** | When the run started, which bot, what started it (schedule, you, Telegram), what happened, and why. |
| Bundles | Back-to-back runs with the same outcome are bundled; expand one to see each run. |
| Expanded run | Each cycle's **Contract**, **Lots**, **Gross** P&L, **Friction** (costs) and **Exit**. |
| Download | **Download full-day audit trail** for a run, or **Download backtest results (.zip)** for a backtest. |

Backtests appear in Activity too, marked **Backtest**. While one runs, **Show live progress** displays how long it has run, the last update, ICICI calls used and memory, with **Stop backtest**.

## Backtesting a bot

The history icon on a card opens **Backtest**, which replays the bot's **saved** settings on real ICICI prices, sized with today's lot sizes and margin.

![Starting a bot backtest](images/dark/bot-backtest.png)

1. Choose a **Period**.
2. Click **Run backtest**. **Close — keeps running** lets you carry on while it works.
3. Read the result in **Activity**.

For bots that read a signal, the backtest replays **every signal setting side by side** (for example the Long Scalper twelve ways: two mechanisms × three durations × follow or fade), in a table of **Signal setting**, **Trades**, **Win rate**, **Net P&L**, **Max drawdown** and **Worst day**, with your saved setting marked. Each run also has a trade list (**Download trades CSV**) and a cumulative P&L chart.

The Holdings Option Writer has no backtest yet. See [Backtests](backtests.md) for how history is fetched, what the results mean and what backtests cannot tell you.
