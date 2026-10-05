# Portfolio

The Portfolio page lists your **open option positions on NFO and BFO** (NSE and BSE F&O). Legs of the same underlying and expiry are grouped together, so a strangle, spread or iron fly reads as one position with one P&L, one margin figure and one payoff chart. From here you can square off legs and arm automated exits.

![The Portfolio page with one group expanded](images/dark/portfolio-overview.png)

## Summary tiles

| Tile | What it shows |
|---|---|
| **Total MTM** | Mark-to-market P&L across all open positions, with the number of groups and legs. While any leg has no price yet it shows **—** and **Waiting on N unpriced legs**, because a sum of the priced legs alone would look like the total. |
| **Total carry** | What your positions would make **if held to expiry** and every option you sold expired worthless (and every option you bought were worth nothing). It is the premium still to be earned or lost from here. Like Total MTM, it waits until every leg is priced. |
| **Span + ELM margin** | Margin blocked by your positions, split into what ICICI has **blocked** (SPAN) plus the app's **ELM buffer**. The **i** icon explains why it can differ slightly from ICICI's screens. See [Margins](margins.md). |
| **Carry return** | Total carry as an **annualised** return on that margin. |

During market hours **Live quotes · WebSocket** appears in the page header, and the MTM and carry figures update with every tick.

## Open positions

The **Group legs** switch at the top right of the table chooses between two views:

- **On** (the default): one row per **Strategy Group**, meaning all legs of one underlying and expiry. Click a group to expand it.
- **Off**: one row per leg, with no payoff chart.

The app remembers your choice.

### Columns

| Column | What it shows |
|---|---|
| Checkbox | Select legs to square off. Ticking a group's box selects all its legs. |
| **Row** | Row number. |
| **Option** | Underlying, expiry and number of legs for a group; the full contract for a leg (for example `NIFTY.29-Sep-2026.24000`). |
| **Type** | Call or put. |
| **Position** | Buy (long) or sell (short). |
| **Qty** | Quantity in units. |
| **Avg** | Your average price. |
| **LTP** | Last traded price. **· prev close** or **· broker px** after the price means it is not a live tick: the previous session's closing price, or ICICI's own figure. |
| **Spot** | The underlying's current level. **· last tick 11:28**, **· close 25-Sep** or **· ICICI quote** after it means no live tick has arrived for over a minute: it is the last price seen today, a closing price, or ICICI's own quote. |
| **MTM** | Mark-to-market P&L. |
| **Carry** | P&L if held to expiry, with its annualised return on margin underneath (group rows). |
| **Span + ELM** | The group's margin: SPAN on top, the ELM buffer beneath. Shown for groups only, netted across the group's legs. |
| **Δ** | [Delta](glossary.md#delta) in units of the underlying. On a leg: the option's delta × quantity, negative when sold. On the group row: the sum over the group's open legs, so −40 means the group gains about ₹40 for each point the underlying falls. It updates with the live prices. Hover a value for its breakdown. |
| **PoP** | Probability of profit at expiry, from the option chain's implied volatility. For a group that collected premium it is the chance every option you sold expires out of the money, the same convention ICICI uses. See [PoP in the glossary](glossary.md#pop-probability-of-profit). |
| **See actions →** | A reminder that expanding the group shows its actions. |

A green dot next to a group means its prices are streaming live.

### Under each group's name

The second line under a group's name shows its automated-exit status:

- **+ Set Profit Booking / Stop Loss** when no rule is armed. Clicking it expands the group, selects every leg and opens the rule dialog.
- A small gauge when a rule is armed: the stop on the left, the target on the right, and a dot showing where the group's P&L sits between them.
- A short explanation when a rule has **reset** (see [When a rule resets](#when-a-rule-resets)).

A badge next to the name shows the rule's state: **Armed**, **Fired** (exit orders placed, waiting for fills), **Completed** (every exit order filled, or the options expired while it was armed) or **Reset**. A scalping bot that trades the same expiry switches the rule off, and it then shows **Reset** with that reason.

## Inside an expanded group

![An expanded group: payoff chart and actions](images/dark/portfolio-group-expanded.png)

### Group payoff

The chart shows the group's P&L across a range of underlying prices:

- **Expiry** (solid line): P&L on expiry day.
- **T+0** (dashed line): P&L today, if the underlying moved there right now.
- The shaded areas show where the group makes or loses money at expiry. The marker on the price axis shows the current spot.
- **+** and **−** zoom the price axis in and out.

Underneath, **Max profit**, **Max loss**, **Breakevens** and **PoP** summarise the payoff. An ∞ sign means unlimited.

**What-if: T+0 scenario** reshapes the dashed line:

| Control | What it does |
|---|---|
| **Days to expiry** | Moves the T+0 curve forward in time. At 0 it lies on the expiry line. |
| **IV shock** | Raises or lowers implied volatility by up to 10 percentage points, to see the effect of a volatility spike or crush. |
| **Live** (reset icon) | Puts both sliders back to today's actual days to expiry and the chain's current volatility. |

### Group actions

| Button | What it does |
|---|---|
| **Profit Booking / Stop Loss** | Opens the automated-exit dialog for the whole group (below). It is enabled only when **every** open leg of the group is ticked, so that what you have selected matches what the rule will close. The line under the label says so: **Select all N legs to apply**, or **Applies to all N legs**. It reads **Edit Profit Booking / Stop Loss** once a rule is armed. On a group an [Iron Condor campaign](#iron-condor-campaigns) manages it is disabled, and says why. |
| **Square Off Selected** | Closes the legs you have ticked. See [Squaring off](#squaring-off). |

### Iron Condor campaigns

Below the actions of every NIFTY group is its **Dynamic Iron Condor** section. On a group no campaign manages, **Adopt as a campaign…** opens the campaign settings and turns the group into one. A campaign can also be started from [Basket Order](basket-order.md#managing-it-as-a-dynamic-iron-condor-campaign) (see [Starting a campaign](iron-condors.md#starting-a-campaign)). The group's row then carries a **Condor** badge.

![A campaign's card on its Portfolio group](images/dark/portfolio-condor-card.png)

The campaign's card shows:

| Item | What it is |
|---|---|
| **Campaign P&L** | The ledger's cash plus what closing every leg now would fetch (longs at the bid, shorts bought back at the ask), net of charges. This is what the max loss is checked against. |
| **Max loss** | The campaign's stop, in rupees. |
| **Total net credit** | Every rupee in and out over the campaign's life, net of charges. |
| **Worst loss at wings** | The lowest the campaign can finish at expiry with the current legs. A dash means it could not be worked out, for example while a short has no wing. |
| **Break-evens** | Where expiry would leave the campaign flat, including every past roll. |
| **Short \|Δ\| PE · CE**, **Net Δ per lot**, **Untested decay** | What the roll rules read, beside the thresholds they are compared with. |
| **Evaluated now** | The rules run on today's prices, refreshed every 20 seconds. **Indicative** means some prices were stand-ins (outside market hours, closing prices are used): it is a reading, never a decision. When it suggests an action, **Execute this suggestion…** opens it as a ticket. |
| **Last scheduled check** | What the last 10:30 or 15:31 check suggested. |
| **Hand to the bot…** | On a campaign of your own: gives it to the Dynamic Iron Condor bot to manage. See [Handing a campaign to the bot](iron-condors.md#handing-a-campaign-to-the-bot). A campaign the bot manages is marked **Managed by the bot**. |
| **Ledger**, **Executions**, **Check history**, **Settings** | Every fill, every ticket and how it went (including any warnings you overrode), every check, and the campaign's settings. **Stop managing…** ends the campaign and keeps its ledger. Nothing is traded and the legs stay open. |

#### Adjusting a campaign

**Adjust…** opens a ticket for the campaign. Start from a template, or a blank ticket, then edit any row (action, strike, right, quantity, and the expiry for a time roll's second half):

![The Adjust ticket](images/dark/portfolio-condor-ticket.png)

| Template | Fills in |
|---|---|
| **Suggested action** | What the rules suggest now. A due tranche is sized at the **Lots** you enter. |
| **Roll selected legs** | The legs ticked in the ticket, moved to **Roll to strike** or **\|Δ\|**. With **Wing follows at the same width** ticked, a rolled short's wing moves with it. |
| **Convert to iron fly** | The untested short moved to the tested strike, its wing at the tested side's width. |
| **Add tranche** | A new condor at the entry deltas, at **Lots**. |
| **Close a side** / **Close selected** / **Close all** | Shorts bought back, then wings sold. |
| **Time roll** | Closes this cycle and opens the next expiry at **Lots**, in one ticket. |
| **Blank** | Rows you add yourself. |

**Preview** prices the ticket and shows the orders in the order they will go out, the cash it brings in or costs after charges, and the position after: net delta per lot, worst loss, break-evens, total credit and margin before and after. Any edit needs a fresh preview.

The campaign's rules come back as **warnings** you may override: moving the tested side, inverting the strangle, a roll below the minimum credit, wings at unequal widths, margin above the ceiling, and thin liquidity on any order. The button then reads **Execute anyway**, and the warnings are kept with the execution. The one thing a ticket cannot do is leave a short with no long to cap it: that is shown in red and the ticket is **Blocked**.

**Executing.** Orders go out one at a time: new wings first, then buy-backs, then new shorts, then old wings, so no short is ever without its wing. Each order waits for its fill before the next is sent, and each fill goes into the ledger as it lands. If an order does not fill, nothing after it is sent and the ticket stops, saying so. If an order's outcome cannot be confirmed, the ticket stops and alerts you to check the Order Book. A position is never opened on a stand-in price; a close may use ICICI's own quote. In [read-only mode](read-only-mode.md) a ticket that only closes still runs; one that opens anything is refused.

**Square Off Selected** on a campaign's group opens this ticket on **Close selected**, so the close is booked to the campaign.

**When the broker and the ledger differ.** If the group's legs change outside the campaign (an order from another page or from ICICI's app), the card lists each difference and the rules decide nothing until each is settled:

- **Assign to campaign** books the difference into the ledger at the price you enter. The broker's average price is filled in where there is one.
- **Leave out** keeps extra exposure outside the campaign. It stays visible as *outside the ledger*, and the rules ignore it. Legs the campaign held that are no longer at the broker cannot be left out; assign the price they were closed at.

## Profit Booking / Stop Loss

A Profit Booking / Stop Loss rule watches a group's total P&L and closes **every leg** of the group when the P&L reaches your profit target or falls to your loss limit.

![The Profit Booking / Stop Loss dialog](images/dark/portfolio-pbsl.png)

| Field | What it does |
|---|---|
| **Current group P&L** | The group's P&L right now, for reference. |
| **Profit target** | Close the group when its P&L rises to this many rupees. |
| **Loss limit** | Close the group when its loss reaches this many rupees. Enter it as a positive number. |
| **Profit-booking offset** | How far past the last traded price the exit orders are priced when the target is hit, from 1% to 20%. |
| **Stop-loss offset** | The same, for exits triggered by the loss limit. |
| **Arm rule** | Starts watching. On an armed rule it reads **Update rule** and saves new values. |
| **Disarm** | Stops watching. For a rule that has reset it reads **Dismiss**. |

When the rule fires, every leg is closed with a **limit order** priced off its last traded price by the offset: buy legs above the price, sell legs below it. That makes the order likely to fill quickly without being an unprotected market order. The rule is checked on the same cycle as the app's live P&L (see [Engine Settings](settings-automation.md#engine-settings)).

Short legs are bought back first, then long legs are sold. If a short leg's buy-back cannot be placed, the long legs on the same side (calls or puts) are **held back** rather than sold, because selling them would leave that short uncovered. The Telegram message and the rule's details mark those legs **Held back**; close them together yourself. If you trade a leg of the group while the exit orders are still going out, the rule stops placing the rest and resets.

If you have not linked Telegram, the dialog offers a **Register** link. With Telegram linked you get a message the moment a rule fires. See [Telegram Alerts](settings-automation.md#telegram-alerts).

> [!WARNING]
> **This is best-effort protection, not a guaranteed stop-loss.** Your server does the watching, not ICICI. A rule can only fire while your server is running, connected to the live quote feed and signed in to ICICI for the day. If any of those is not true at the moment the threshold is crossed, no exit orders are placed. The target and loss limit are **triggers**, not guaranteed exit prices: in a fast market the limit orders may fill partly, at a worse price, or not at all.

A rule is only checked while **every leg** in its group has a recent price. If any leg goes about two minutes without one, the rule **pauses**: it still shows as armed, but it is not checked. This happens when a far strike has not traded or quoted, or when the price feed drops. The rule starts checking again by itself as soon as every leg has a fresh price, so you do not need to re-arm it. With Telegram linked, a pause of more than about 30 seconds sends a **PB/SL rules unmonitored** message naming the rules. Watch those positions yourself until **PB/SL rules monitored again** arrives. That message is sent only once the feed has worked for 5 minutes in a row, so a feed that keeps dropping does not flood you. A new stop after that message is always reported straight away.

Remember that **logging out ends your ICICI session and stops every rule**. Close the browser tab instead; rules keep working without it until midnight IST. See [Signing out](signing-in.md#signing-out).

Armed rules and the orders they place are also listed on the [Order Book](order-book.md#profit-booking--stop-loss) page, under **Profit Booking / Stop Loss**.

### When a rule resets

A rule **resets** (stops watching) when it can no longer act safely: for example you changed the group by adding or closing a leg, one of its exit orders was rejected, you traded the group while its exit orders were going out, or your server restarted while it was placing them. After a restart the rule does not send the remaining exits; it resets and lists the orders it had already sent. A reset rule shows **Reset** and a short explanation under the group's name. Click the explanation to read it in full.

A reset stops **future** automation, but it does **not** cancel exit orders it already placed. Those can still fill:

- **Reset · N live** (amber): exit orders are still working at ICICI.
- **Reset · action needed** (red): an exit order is still working for a leg that is **already closed**. If it fills it opens a **new position in the opposite direction**. A red banner at the top of every page warns you until it is dealt with.

If the explanation says ICICI's answer to an exit order was **lost**, that order may have reached the exchange even though the app could not confirm it. Check the [Order Book](order-book.md) before placing anything yourself.

Open the rule dialog to see the **Still live** orders. **Cancel remaining exit orders** cancels them for you. You cannot dismiss or re-arm the rule while any of its exit orders is still working.

## Squaring off

**Square Off Selected** (group view) or **Square Off** (leg view) opens the **Square off** dialog.

![The Square off dialog](images/dark/portfolio-square-off.png)

- Each leg is listed with its quantity and an editable **price**. The price starts at the current market price.
- The **lightning bolt** next to a price switches that leg to an [aggressive order](place-order.md#aggressive-orders), which fills faster.
- When more than one leg is listed, untick any leg you want to keep.
- **Square Off** passes the legs to the order confirmation dialog, where you review and send them. See [Confirming an order](place-order.md#confirming-an-order).

When the market is closed the orders can only be **parked** for later. See [Order Book](order-book.md#parked-execution).

## Exits for a single leg (GTT)

With **Group legs** switched off, each leg's actions are **Profit Booking / Stop Loss** and **Square Off**. The Profit Booking / Stop Loss button here works differently from the group rule: it places a **GTT (Good Till Trigger) OCO order directly with ICICI** for that one leg.

![A GTT exit for a single leg](images/dark/portfolio-leg-gtt.png)

| Field | What it does |
|---|---|
| **Target trigger** | When the option's price reaches this level, ICICI sends the target order. |
| **Target limit** | The limit price of the target order. |
| **Stoploss trigger** | When the price reaches this level, ICICI sends the stop-loss order. |
| **Stoploss limit** | The limit price of the stop-loss order. |
| **Place GTT order** | Places the bracket with ICICI. When one side fills, ICICI cancels the other (one-cancels-other). |
| **Cancel GTT order** | Shown when a GTT is already in place; cancels it at ICICI. |

Because ICICI watches a GTT, it keeps working even when your server is off. It looks at one leg's **price**, not a group's P&L.
