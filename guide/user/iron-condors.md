# Iron Condors

A **Dynamic Iron Condor campaign** manages one NIFTY iron condor for you over weeks: it checks the position twice a day, suggests when to add a tranche, roll a side or exit, and keeps a ledger of every rupee in and out across all of it. Each campaign's card sits on its group in [Portfolio](portfolio.md#iron-condor-campaigns). A campaign the bot runs is also one row in the Bots page's [Activity](bots.md#activity) for as long as it is open, and a Simulation campaign, which holds nothing at the broker, is found only there. Its settings, backtest and a quick way to start one by hand are on the **Dynamic Iron Condor** card on the [Bots](bots.md#dynamic-iron-condor) page.

Campaigns are **NIFTY only** for now. You act on a suggestion from the campaign's card with **Execute this suggestion…**, or make your own change with **Adjust…** (see [Portfolio](portfolio.md#adjusting-a-campaign)). Nothing is traded without you pressing Execute, unless you hand a campaign to the [Dynamic Iron Condor bot](bots.md#dynamic-iron-condor).

## How a campaign works

An iron condor sells an out-of-the-money call and put and buys further-out wings on both, so the loss on either side is capped. A *dynamic* condor is managed rather than left alone: when the market moves towards one side, the other side is rolled closer to collect more premium and bring the position back towards neutral.

- **Shorts by delta, wings by distance.** Shorts go in at the **Short Δ** (0.20 by default), so one rule works for a weekly or a monthly. Each wing then goes the same distance beyond its short on both sides: the **Wing width**, a percentage of spot (4.5% by default, about 1,000 NIFTY points at 22,400), snapped outward to a listed strike. Equal widths keep the most a side can lose, and the margin, the same on both sides. If the listed strikes end before the wing's target, the furthest one is used and the suggestion says how narrow that wing is.
- **Entries come in tranches.** The campaign's size is spread over several entries into the **same expiry**, evenly between the entry DTE and the cut-off. All tranches are managed together as one position.
- **Only the side that is not under attack moves.** When the market rallies the puts are rolled up; when it falls the calls are rolled down. The threatened side is never moved by an adjustment, unless you switch on **Re-centre**.
- **The roll stops at the straddle.** A roll moves the untested short to the threatened side's delta, but never past the threatened short's strike. At that point the position is an iron fly, and it never inverts.
- **Re-centre, if you switch it on.** Rolled again and again, the untested short ends at the threatened strike, which a sharp reversal hurts most. With **Re-centre both sides** on, a roll that comes due while the threatened short is past the trigger delta (0.30 by default) moves **both** sides instead: the threatened spread is bought back and sold again further out, the other spread moves closer, and both shorts land at the re-centre delta (0.20 by default) around today's spot, each wing the **Wing width** beyond its short. It books the threatened side's loss, but that loss is already in the campaign's P&L, so what is new is only the spread and charges on up to eight orders. It goes ahead even at a net debit. If a contract in it has no price, nothing moves; it never falls back to a one-sided roll.
- **The ledger is the campaign.** Every fill's proceeds minus its cost, net of charges, add up to the **total net credit**. Break-evens and the campaign's P&L include every past roll.

## The checks

The rules run at two fixed times on each trading day, both set in the campaign's settings:

| Check | Default | Why then |
|---|---|---|
| **Start-of-day** | 10:30 IST | Prices are ignored until the morning has settled. This check also catches an overnight gap. |
| **End-of-day** | 15:31 IST | After the closing auction has settled the index, while options still trade until 15:40. |

A few minutes before each check the app loads the expiry's live prices. A check missed because the app was down is skipped, never run late. A scheduled check decides only on **live** prices: if NIFTY's spot or any held leg is on a stand-in price, it says so and decides nothing.

At each check the rules are taken in this order, and the first that applies wins:

| # | When | Suggests |
|---|---|---|
| 1 | A price the rules need is missing, spot is not live, or a feed is down | Nothing: **Could not decide**, with the reason |
| 2 | Campaign P&L is at or past the **max loss** | **Close everything**: shorts bought back first, wings sold last |
| 3 | Days to expiry reach the **exit DTE** | **Exit or time-roll**: close, or close and open the next cycle |
| 4 | At the straddle and NIFTY is beyond a break-even, at the end-of-day check | **Exit or time-roll** |
| 5 | The untested side is below its delta floor or has decayed past its threshold, **or** net delta per lot is outside its band | **Roll the untested side**, if the roll adds at least the minimum credit after charges and is not within the **No rolls within N days of exit** window. With **Re-centre** on and the threatened short past its trigger: **Re-centre both sides** instead, at any credit or debit, unless within that window |
| 6 | A tranche is due at the entry check | **Enter a tranche** |

The max loss is checked **only at the two checks**, not continuously. On a fast day the position can run past it before the next check; the wings cap the worst case, and the card shows that figure as **Worst loss at wings**.

When a check suggests an action, or cannot decide, you get a [Telegram alert](settings-automation.md) if alerts are on. The check only suggests: you act on it from the card.

## Starting a campaign

- **From the bot card:** the play icon on the **Dynamic Iron Condor** card opens [Basket Order](basket-order.md#managing-it-as-a-dynamic-iron-condor-campaign) with the first tranche filled in: NIFTY, the expiry a new cycle would use, the four strikes at the settings' deltas, and the **Lots per tranche** (blank sizes it from today's margin). **Manage as a Dynamic Iron Condor campaign** is already ticked. Check or change anything, then **Execute**.
- **From Basket Order:** build any NIFTY basket yourself and tick **Manage as a Dynamic Iron Condor campaign** before you execute.
- **From Portfolio:** expand a NIFTY group you already hold and choose **Adopt as a campaign…**. The group's open legs become the campaign's opening fills at the broker's average prices, with charges estimated.

A campaign started from Basket Order uses the bot card's campaign settings, and its basket goes out through the campaign's own executor rather than as an ordinary basket: one order at a time, wings first, each at a limit near the live bid or ask (not at the prices in the basket), with every fill booked to the campaign's ledger. If a step does not fill, the rest are not sent, and the legs already filled are still hedged. The remaining tranches are then suggested on its card as they fall due. It is your campaign: the bot never acts on it unless you [hand it over](#handing-a-campaign-to-the-bot).

The campaign is refused, before any order is sent, if:

- another campaign already manages that NIFTY expiry, yours or the bot's;
- you already hold legs on that expiry (adopt the group from Portfolio instead);
- a Profit Booking / Stop Loss rule is armed on that expiry;
- the basket would leave a short leg with no wing to cap it.

Once a campaign runs, a Profit Booking / Stop Loss rule cannot be armed on its group: every roll would reset it, and the campaign has its own max loss.

## Handing a campaign to the bot

A campaign you started yourself can be handed to the [Dynamic Iron Condor bot](bots.md#dynamic-iron-condor), for example once its first tranche is in. On its card in Portfolio choose **Hand to the bot…**. Nothing is traded: the bot manages it from its next check, exactly as if it had opened it, and the card is marked **Managed by the bot**.

The dialog shows what will change before you confirm:

- **The bot's settings.** The bot only ever runs on its own settings, because its Simulation cycles and approvals were earned on them. Any setting that differs is listed with both values, and the campaign takes the bot's.
- **The remaining tranches**, and how the bot will size them (its **Lots per tranche**, or that day's margin). Your own tranches may have been a different size.
- **What the rules say now on the bot's settings.** This can differ from the card's suggestion: with a narrower band, for example, a roll may be due at once.

It is refused, with every reason listed, until:

- the bot is switched on in **Semi-auto** or **Auto** and is not paused (in Simulation it places nothing, so it cannot run real positions);
- the bot is not already running a campaign (it runs one at a time);
- the broker's position matches the campaign's ledger (assign or leave out every difference first);
- no ticket is executing on the campaign.

Afterwards the usual rules apply. A ticket you execute on it pauses the bot until you resume it; switching the bot off hands the campaign back to you with its legs open; and saving different settings on the campaign's card hands it back too. To change what the bot runs, use the gear on its card.

## Several campaigns

You can run as many campaigns as you like, each on its **own NIFTY expiry**: for example one on this month's expiry and one on next month's. An expiry can have only one campaign, because the broker holds one net position per contract and could not tell two campaigns' legs apart.

- **Margin is per campaign.** Each campaign sizes and checks against its own margin ceiling and max loss. Nothing adds them up, so leave room in your account for all of them together.
- **A time roll never lands on an expiry another campaign manages.** If the next cycle's expiry is taken, the time roll is refused before any order is sent and you get a Telegram alert. The position stays open past its exit DTE until you act: close it from its card (**Adjust… → Close all**), or close the other campaign first. The suggestion is repeated at every check until then.
- **The bot waits for a free expiry.** If the expiry its next campaign would use is already another campaign's (or has a Profit Booking / Stop Loss rule armed), the bot opens nothing. Its card says **Waiting:** and why, and you get one Telegram alert. In **Simulation** it never waits, because a Simulation campaign holds nothing at the broker.

## Settings

The settings are on the gear of the **Dynamic Iron Condor** bot card, in the tabs **Cycle**, **Strikes**, **Rolls**, **Risk**, **Premium** and **Bot**. A campaign started from Basket Order takes a copy of them when it starts; a campaign's own card on Portfolio can change its copy later.

| Setting | Default | What it does |
|---|---|---|
| **Expiries** | Monthly only | **Monthly only** uses each month's last expiry; **Any** allows weeklies. |
| **Entry DTE** | 45 | The first tranche goes in at or below this many days to expiry. |
| **Tranche cut-off DTE** | 30 | No new tranche below this. |
| **Exit DTE** | 21 | Exit or time-roll at or below this. |
| **Tranches** | 3 | Entries spread evenly from the entry DTE to just before the cut-off (45, 40 and 35 by default). |
| **Enter tranches at** | Start-of-day check | Which check enters a due tranche. |
| **Short Δ** | 0.20 | The delta the shorts are chosen at. |
| **Wing width** | 4.5% of spot | How far beyond its short each wing goes, the same on both sides. |
| **Untested side below Δ** | 0.10 | Roll when the untested short falls below this delta… |
| **…or decayed** | 80% | …or has lost this share of its premium. |
| **Net Δ band per lot** | 0.15 | Roll when the position's net delta per lot is outside ± this. |
| **Minimum roll credit** | 20 points | A roll adding less than this per unit, after charges, is skipped and the card says so. |
| **No rolls within … days of exit** | 0 (off) | A roll due within this many days of the exit DTE is reported but not done: the new short would be held only a day or two, so the roll mostly pays the spread. It holds back re-centres too. Every backtest compares 0 and 3 for you (see [Comparing settings](#comparing-settings)). |
| **Re-centre both sides when the tested side is deep** | off | On: a roll due while the threatened short is past the trigger moves both sides, as described [above](#how-a-campaign-works). Every backtest compares off and on for you. |
| **Re-centre when the tested short is above / Re-centre both shorts to** | 0.30Δ / 0.20Δ | The trigger, and where both new shorts land. The landing must be below the trigger, or the next due roll would re-centre again. |
| **Start-of-day check / End-of-day check** | 10:30 / 15:31 | The two check times, IST. |
| **Margin ceiling** | — | The campaign's margin. Keep the rest of your capital free as a buffer. |
| **Max loss / …or of the ceiling** | off / 5% | Close everything past this. If both are set, the tighter one applies. One of them must be set. |
| **Only sell when premium is rich / Sell at or above** | off (on for a new bot) / 1.00 | The [premium gate](bots.md#the-premium-gate) for **tranche entries only**. A due tranche waits, check by check, until the cycle's options price at least this multiple of the move NIFTY's recent history forecasts to expiry, or until the cut-off DTE passes. Rolls, exits and the max-loss stop never wait for it. At a time roll it decides only whether the next cycle opens: if not, the old cycle still closes and the campaign ends there, as with **Close**, and the next cycle's tranches go in when premium is rich. |

The **Bot** tab adds the bot's own settings (see [Dynamic Iron Condor](bots.md#dynamic-iron-condor)): what happens at the exit DTE, the lots per tranche and the Semi-auto approval window.

The defaults are starting points, not findings. Backtest them before trusting them.

## Backtest the rules

The history icon on the **Dynamic Iron Condor** bot card backtests the rules on the card's **saved** settings, exit action and lots per tranche, like every bot's backtest (see [Backtesting a bot](bots.md#backtesting-a-bot)): choose a period, run it, and read the result in **Activity**. Save a change before you backtest it. The replay runs the same rules a campaign runs, at the same two checks, on ICICI's traded **5-minute** option prices from January 2026; a period reaching further back starts there, and the result says so.

### Years of history: NSE daily closes

ICICI's prices cover only about eight monthly cycles, none of them a crash. For a longer view, choose **Prices: NSE daily closes** in the backtest dialog. It replays the same rules on the closing prices NSE publishes for every session **from 1 January 2020**, so it includes the March 2020 crash, the 2022 sell-off and the June 2024 election day. **Since 2020** fills in the whole range. It differs from an ICICI replay in four ways, and its notes repeat them:

- **One decision a session, at the close.** Daily files have only each contract's close, so a 10:30 check would read prices before they happened. Entries, rolls, exits and the max-loss stop are all decided after the close, and a tranche set to enter at the start-of-day check enters at the close instead. The stop is therefore checked once a day, not twice.
- **Every cycle is sized to today's money.** NIFTY was about 12,000 in 2020. Each cycle's lot is today's lot × today's NIFTY ÷ NIFTY when the cycle opened, so a 2% move costs the same in 2020 as it does now and every year is judged on equal terms. Rupee figures read "as if traded at today's size". Settings in index points (**Minimum roll credit**) stay in points, so they are relatively larger at older, lower index levels.
- **A contract that did not trade is priced from the ones that did.** NSE's file gives a contract that did not trade that day a stale price, so that price is ignored. Instead, the price is worked out from the implied volatility of the same expiry's contracts that did trade that day, which is how NSE prices untraded contracts too. This matters most for far wings, which often go a day without a trade. A traded price always wins, and each run's notes say how many prices were worked out this way.
- **Spreads are today's.** The bid-ask spread is modelled on today's quotes, which understates how wide older far strikes were.
- **The premium gate reads daily prices.** Its forecast of NIFTY's movement uses each session's open-to-close and overnight moves, where a live check uses one-minute prices. Both measure the same thing; the daily version is noisier.

The prices are downloaded from NSE's public archive, outside market hours: about 1.3 GB the first time from 2020 (about an hour), and only new sessions after that. Only NIFTY options near the money that traded are kept, about 100 MB, listed as **NSE daily option prices** on [Storage](settings-diagnostics.md#storage), where a date range can be deleted. In market hours the backtest replays what is already stored.

### Comparing settings

Every backtest also replays the saved settings' neighbours, so one run shows how the rules compare with the settings around yours. It replays every combination of:

| Setting | Values compared |
|---|---|
| **Net-Δ band per lot** | yours, 0.05 below and 0.05 above |
| **Minimum roll credit** | yours, 10 points below and 10 points above |
| **Max loss** | your % of the margin ceiling, half of it and one and a half times it. A rupee limit you also set stays as it is; with only a rupee limit, that limit is compared instead. |
| **No rolls within … days of exit** | 0 and 3, and yours if it is neither |
| **Exit action** | time roll and close |
| **Re-centre** | off and on. "On" uses your trigger and landing deltas when your re-centre is on, else 0.30 and 0.20. |

That is 216 combinations with the defaults, which needs about twice the prices a run without re-centre would, so a long period may take more days of fetch budget. A value outside the setting's allowed range is left out rather than moved, so the run may compare fewer. Every other setting (strikes, the cycle clock, the leg rule, lots per tranche) stays as you saved it.

The Activity row opens on a table with one line per combination: the settings it varied, **Premium gate**, **Re-centre**, **Campaigns**, **Rolls** (a re-centre counts as one), **Win rate**, **Net P&L**, **Max drawdown** (the largest fall at a check) and **Worst at a check**. **your settings** marks your saved combination, **best** marks the highest net P&L, and **partial** marks a combination that stopped for missing prices (hover it to see where). The campaigns, chart and figures below the table are your saved combination's.

On ICICI prices, history covers only about eight monthly cycles, so the best of 216 combinations is a lead to look into, not a finding. NSE daily closes from 2020 cover about 80, which is better but still not proof: pick settings from the middle of a range that does well, not the single best row. Each combination is also kept as a backtest of its own settings: save one, and the card's **Backtest of these settings** shows its result straight away.

Lots per tranche left blank are sized from today's margin, which needs ICICI's margin calculator; a mock instance has none, so set the lots in the settings first.

The replay only opens legs at strikes ICICI lists as tradeable: the range ICICI lists today for NIFTY (for example 8.6% below to 16.4% above spot), applied to every day of the period, because the lists of past days are not kept. Its notes say which range was used. Without it, a backtest could trade far strikes that live trading cannot.

Like every backtest, missing prices are fetched outside market hours within today's call budget (see [Backtests](backtests.md)). Only the contracts the rules actually need are fetched, plus a fixed set of strikes every 500 points to read volatility from. All the combinations are fetched for together, so the budget is spread across them, and each window is fetched once whichever combinations need it. Comparing settings therefore needs more history than one replay: a long period may take several days' budgets. A combination that cannot get what it needs stops at that check, and its row and the run's notes say where and why. It is not a completed backtest, so **Backtest of these settings** does not count it. Run the backtest again to carry on from what is already fetched.

Its Activity row lists one line per **campaign**: when it started and ended, its expiries, tranches and rolls, the worst P&L it showed at a check, how it ended, charges and net P&L. A campaign still open when the period ends is marked at its last check (what closing then would have left), and reads **Open at period end (marked)**. **Download backtest results (.zip)** adds every action your saved combination's replay took, the comparison table (`summary.csv`), and every other combination's campaigns (`combinations/`). Only your saved combination keeps its full action log.

What it cannot model: bid and ask (spreads are modelled from your own observed samples), order-book depth, intraday moves between the two checks, and margin, which is sized once at today's levels. History starts in January 2026, which is only about eight monthly cycles: enough to see how the rules behave and how large the losing months are, not enough to prove an edge. A monthly campaign takes weeks, so a short period may finish none.
