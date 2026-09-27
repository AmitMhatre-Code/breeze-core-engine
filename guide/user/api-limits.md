# ICICI API limits

Everything Breeze Modern does with your ICICI account, from fetching positions and quotes to placing orders, is an **API call** to ICICI. ICICI limits those calls in two ways, and the app is built around both.

## The daily limit: 5,000 calls

ICICI allows about **5,000 API calls per account per day**. The count resets at **midnight IST**.

The header bar's **API** counter shows how many your account has used today, and changes colour as you use them up:

| Calls used | Colour | What happens |
|---|---|---|
| Up to 4,000 | Normal | Nothing special. |
| 4,001 to 4,500 | Amber | A **Daily API usage warning** pops up once, telling you how many calls remain. |
| 4,501 to 5,000 | Red | The last **500 calls are reserved** for placing and cancelling orders. Less essential calls (refreshes, background price checks, backtest downloads) stop so that you can always still trade and exit. |
| 5,000 | | A banner reads **You have hit ICICI's daily limit of 5,000 API calls**. Anything needing ICICI (positions, margin, quotes, orders) stops until midnight IST. |

Breeze Modern is designed to use far fewer than 5,000 calls on a normal day. Live prices come over ICICI's streaming connection, which does not count against the limit, and the P&L engine, Profit Booking / Stop Loss and the Dashboard's live tiles run off those streamed prices.

**What uses the most calls:** repeatedly refreshing pages, loading many option chains in quick succession, calculating margins with ICICI's calculator for large baskets, backtest downloads and the margin comparison harness. **Settings → API Usage** shows exactly where your calls went; see [API Usage](settings-trading.md#api-usage).

## The per-minute limit

ICICI also limits how many calls you can make **within any rolling minute**. Go over it and ICICI refuses calls for a cooling-off period that can last longer than the minute itself.

Breeze Modern prevents this rather than recovering from it:

- **It paces calls** so they stay under ICICI's per-minute rate. **Settings → API Usage → Rate limit backoff** sets the minimum gap between calls.
- **If ICICI does refuse** ("too many requests"), the app waits and retries. During order placement a **Broker rate limit** countdown covers the screen while it waits; let it finish rather than repeating the action.

## Why orders go out one at a time

Every call to ICICI from your account, including every leg and every chunk of a multi-leg order, is sent **one after another, never in parallel**. That can make a large basket take a few seconds longer, and it is deliberate:

- **It keeps within the per-minute limit.** Sending everything at once uses up the same allowance faster and earns a longer penalty.
- **It makes retries safe.** With only one call in flight, a refusal from ICICI definitely means that order was not placed, so retrying it can never fill it twice. With calls in flight at the same time, a partial failure would leave it unclear which legs went through.

## How the automated features share the allowance

- **Backtests** have their own daily **Backtest call budget** (**Settings → API Usage**), well below the total, and never touch the order reserve.
- **Scalping bots** hold back a number of calls (**Broker calls held back** in their settings) so they can never starve your manual trading.
- **The margin comparison harness and calibration sweep** are classed as non-essential and stop before order placement would be affected.
- **The API Playground** has no such protection; every call you fire there counts.
