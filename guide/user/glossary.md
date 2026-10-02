# Glossary

Terms used in the app and in this guide.

### ATM, OTM, ITM
At the money, out of the money, in the money: where an option's strike sits relative to the underlying's current price. The ATM strike is the one closest to spot.

### Bhavcopy
The exchange's official end-of-day file of closing prices, open interest and volumes. Breeze Modern uses NSE's and BSE's F&O bhavcopies for prices after the close.

### bps (basis point)
0.01%. On NIFTY at 24,000, 1 bps is about 2.4 points.

### Carry
What a position would still make or lose if held to expiry and every option expired worthless: the premium left in it. **Carry return** is carry as an annualised return on margin.

### CAS (closing auction session)
The auction from 15:15 to 15:35 that sets F&O closing and expiry settlement prices. Continuous trading stops at 15:15; auction orders are collected 15:20–15:30.

### CE, PE
Call option, put option.

### Chunk
One of the separate orders a large quantity is split into, to stay within the exchange's freeze limit.

### Credit spread, debit spread
Two options of the same type and expiry. A **credit spread** sells the nearer strike and buys a further one, collecting premium. A **debit spread** buys the nearer strike and sells a further one, paying premium.

### Delta
How much an option's price moves for a 1-point move in the underlying: between 0 and +1 for a call, between −1 and 0 for a put. A position's delta is that times the quantity, negative when you sold, and reads as **units of the underlying** the position behaves like: −40 means it gains about ₹40 for each point the underlying falls. Deltas of legs on the same underlying add up exactly, so a Strategy Group's or basket's **Net Δ** is the sum of its legs.

Breeze Modern works delta out from the option chain on your screen: the forward price the chain implies (from calls and puts at the strikes nearest the money), each strike's own implied volatility (from its bid and offer, or the strikes around it when its own quote is thin) and the time left to the 15:30 close on expiry day. Other platforms use slightly different inputs, so their figures can differ in the second decimal. It changes as the market moves, fastest close to expiry. Hover a delta to see how it was worked out.

### Drawdown
The largest fall in running P&L from a peak to a later trough.

### ELM (extreme loss margin)
An extra margin exchanges charge on short options as a buffer against extreme moves. Breeze Modern shows its own ELM estimate separately, on top of ICICI's figure. See [Margins](margins.md#about-elm).

### EMA
Exponential moving average: a trend line that weights recent prices more heavily.

### Fade, follow
Trading against a signal's call, or with it.

### Freeze limit
The largest quantity the exchange accepts in a single order for a contract. See [Quantity Limits](settings-trading.md#quantity-limits).

### Friction
Brokerage, taxes, exchange fees and the bid-ask spread paid on each trade.

### GTT (Good Till Trigger)
An order ICICI holds and sends when the price reaches a trigger level. A GTT **OCO** (one-cancels-other) pairs a target and a stop-loss; when one fills, ICICI cancels the other.

### Iron fly
A short at-the-money call and put, with long out-of-the-money call and put wings that cap the maximum loss.

### LTP
Last traded price.

### MTM (mark-to-market)
The P&L of an open position at current prices.

### Open interest (OI)
The number of futures or option contracts outstanding. Rising OI means new positions are being opened; falling OI means positions are being closed.

### Parked order
An order saved on your server while the market is closed, to be sent from the Order Book once it opens.

### PB/SL (Profit Booking / Stop Loss)
A rule that closes every leg of a Strategy Group when its P&L reaches a profit target or a loss limit. See [Portfolio](portfolio.md#profit-booking--stop-loss).

### PCR (put-call ratio)
Total put open interest divided by total call open interest for an expiry.

### PoP (probability of profit)
The estimated chance, from the option chain's implied volatility, that a position ends in profit at expiry. For positions that collect premium, Breeze Modern follows ICICI's convention: it is the chance that every option you sold **expires out of the money** (for a strangle or iron condor, that the underlying finishes between the short strikes). Each edge of that range uses the implied volatility of its own side of the chain: puts below the underlying, calls above it, so a chain whose puts are dearer than its calls is scored as the market prices it. Portfolio, Basket Order and the Strategy Builder all compute it this way, and agree. The premium you collected is not counted, so the true chance of at least a small profit is a little higher. For positions that pay premium, it is the chance the position ends above zero. It is a model estimate, not a guarantee.

### Read-only mode
The state your deployment enters when its license is not active: you can see your account but not trade. See [Read-only mode and your license](read-only-mode.md).

### Rollover day
The days around month-end when futures positions move from the expiring contract to the next one.

### Scrip master
ICICI's list of every contract it supports, with strikes, expiries and lot sizes.

### Signal
A short-lived reading of NIFTY or SENSEX direction: Bullish, Bearish, Quiet or No reading. See [Signals](signals.md).

### SPAN
The exchanges' standard margin method, which prices a position's risk across a range of price and volatility scenarios. The exchanges publish SPAN files several times a day. See [Margins](margins.md).

### Strangle
A call and a put at different out-of-the-money strikes: both sold (short strangle) or both bought (long strangle).

### Strategy Group
All your open legs in one underlying and expiry, treated as one position on the Portfolio page.

### VWAP
Volume-weighted average price: the average price of everything traded so far today, weighted by quantity.

### WebSocket
The streaming connection over which ICICI sends live prices. It does not count against your daily API limit.
