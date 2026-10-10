# Backtests

A backtest replays history and asks: *what would this have done, exactly as it is set up today?* Breeze Modern has two kinds:

| Kind | Where | What it replays |
|---|---|---|
| **Signal backtest** | [Signals](signals.md#backtest-every-signal) page, **Run backtest** | Every direction reading (two mechanisms, Momentum in three versions, × three durations × two indices; Momentum v3 runs on NIFTY only), scoring every call. |
| **Bot backtest** | The history icon on a [bot card](bots.md#backtesting-a-bot) | One bot on its saved settings on real option prices: minute by minute, or for the Dynamic Iron Condor at its two daily checks on 5-minute prices (see [Backtest the rules](iron-condors.md#backtest-the-rules)). |

This section covers what they have in common. The Signals and Bots sections explain the controls on each page.

## Choosing a period

Both use the same period choices:

| Period | What it covers |
|---|---|
| **Last trading day** | The most recent session that has closed. During market hours that is yesterday; today counts only after the close. |
| **Last trading week** | The last five sessions ending there, holidays excluded. |
| **Last trading month** | From the same date a month back to the last closed session. |
| **Custom range** | Any **From** and **To** dates. A range reaching into today while the market is open stops at the last closed session. |

A signal needs a completed backtest covering **at least 30 calendar days** before a bot may use it (the [30-day gate](signals.md#the-30-day-gate)), so run a month or longer when that is your aim.

## Where the history comes from

Backtests use only data ICICI serves as history: one-minute bars of price, volume and open interest for futures and options. Anything not already on your server is **downloaded from ICICI when you run the backtest**, and kept for next time.

- **Downloads happen outside market hours only.** During market hours a backtest replays whatever is already stored.
- **They share your ICICI allowance.** ICICI allows about 5,000 calls a day for everything you do. Backtests may use only their own **Backtest call budget** (**Settings → API Usage**), and never the reserve kept for placing and cancelling orders. One bot over a month typically needs under a hundred calls; six months, a few hundred.
- **A long period can take more than one evening.** When the day's budget is spent, the backtest runs on what it has and reports the missing days. Run it again another evening and it picks up where it left off.
- **Nothing is invented.** A day or contract that cannot be fetched is **skipped and reported** in the results, never filled in with a model price.

## What a bot backtest assumes

- **Real traded prices only.** Fills use actual ICICI option prices, down to one-second bars where available, with the bid-ask spread modelled from observed quotes. No theoretical prices are ever used.
- **Today's set-up.** Every past day is replayed with your **current** settings, **today's** lot size and **today's** margin. The question is "how would this bot, as I have it now, have done?"
- **Every signal setting side by side** for bots that read a signal, so you can compare following and fading each reading. The Expiry-Day Index Writer, the Intraday Iron Fly and the Long Scalper also replay their [premium gate](bots.md#the-premium-gate) off and at 0.90, 1.00 and 1.20. Your saved setting is marked. The Dynamic Iron Condor compares combinations of its roll and risk settings instead (see [Comparing settings](iron-condors.md#comparing-settings)).
- **Full costs**, from [Trading Costs](settings-trading.md#trading-costs), including the **Simulation slippage** allowance.
- **Margin for lot sizing** comes from the SPAN file or ICICI, as set by the **Backtesting** switch in [Reference Data Loads](settings-automation.md#use-span-files-for-margin-calculation). You are warned if the SPAN file is out of date.

## Running, watching and stopping

- **One backtest at a time.** A signal backtest and a bot backtest never run together; the button tells you if another is running.
- **It keeps running if you close the dialog** or leave the page. For bots, open **Show live progress** on its Activity row to see how long it has run, ICICI calls used and memory. **Stop backtest** (bots) or **Stop** (signals) ends it early.
- **It stops itself before running out of memory** or disk. If storage passes your threshold, backtests that download data are refused until you free space (see [Storage](settings-diagnostics.md#storage)).

## Reading the results

**Signal backtests** appear on the Signals page: a sentence per reading in each mechanism's table, and a row in its Activity table. See [How a signal is judged](signals.md#how-a-signal-is-judged).

**Bot backtests** appear as a row marked **Backtest** in the Bots page's Activity. Expanded, a row shows:

| Part | What it shows |
|---|---|
| Comparison table | For each **Setting** (a signal setting, or a premium gate threshold): **Trades**, **Win rate**, **Net P&L**, **Max drawdown** (largest fall from a peak) and **Worst day**. For the Dynamic Iron Condor, one line per settings combination ([Comparing settings](iron-condors.md#comparing-settings)). |
| Totals | Net P&L and the main figures for the run. |
| Trades | Every trade with entry and exit times, contract, lots, prices and **Exit reason**. **Download trades CSV** saves them. |
| Chart | Cumulative net P&L over the period. |

**Download backtest results (.zip)** saves everything for the run. A signal backtest's zip holds every bar replayed, every minute's reading and every call with what the index did next, so any number can be checked independently.

The card's **Last backtest** figure (**Backtest of these settings** on the Dynamic Iron Condor) is always labelled as a backtest, so it can never be mistaken for money the bot made.

## What a backtest cannot tell you

- It uses today's lot size and margin on past days, which were different.
- Liquidity and your place in the order queue are only approximated.
- Markets change; a pattern that held last quarter may not hold next quarter.

A backtest is evidence, not a promise. Use it alongside a period in **Simulation** mode on live prices before letting any bot trade real money.
