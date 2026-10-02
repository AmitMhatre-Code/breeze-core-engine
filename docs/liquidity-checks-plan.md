# Order-book liquidity checks — plan

Status: **built** (2026-10-02), uncommitted. The rationale is recorded as design-decisions #62.

A thin strike can fill far from its LTP, and every place we trade sizes a quantity without asking
whether the book can absorb it. Today the only liquidity test is `total_buy_qty > 0 and
total_sell_qty > 0` (the strategy engine) or `quantity <= total_buy_qty` (the uncovered-shorts
scan). Both read the whole book's resting totals, which include orders far from the touch, so
neither says what a given quantity would actually pay.

---

## 1. Decisions you made (2026-10-02)

| # | Question | Answer |
|---|---|---|
| 1 | Book data | **5-level depth for every strike of every streamed chain.** If ICICI refuses or caps depth subscriptions: keep depth for the strikes nearest the money and for the legs being checked, and estimate the rest from the top of book (best bid/ask and their sizes), labelled "estimate: top of book only". |
| 2 | Benchmark | **LTP.** The estimated average fill for the whole quantity is compared with the LTP. |
| 3 | When it fails | More than **10% of LTP and more than 5 ticks** away from it, adversely. Both numbers are Settings, not environment variables. |
| 4 | LTP that can't be trusted | An old last trade, or an LTP outside the current bid and ask, gets **its own warning**. The benchmark stays the LTP. Bots treat it as a failed check. |
| 5 | Order bigger than the visible book | **Fails**, with "beyond the visible book". The visible part is priced; the rest is unknown. Bots shrink to what is visible. |
| 6 | Place Order, Basket, Strategy Builder Execute | **Warn only.** A ⚠ triangle beside the quantity; hovering it says the quantity is large enough that the fill could be very different from LTP. The confirmation modal repeats the same message in red, prefixed with ⚠. The ⚡ Market and LTP±tolerance modes are unchanged. |
| 7 | Strategy Builder proposals | Sized to margin as today, then **capped** at what the thinnest leg's book absorbs within the threshold. A strike that cannot take 1 lot is excluded. |
| 8 | Bots | **Shrink to the largest lot count that passes, else skip** with a Telegram and Activity note. A bot never changes strikes. |
| 9 | Exits | **Never blocked.** Square-off, SG exits, bot stops and CAS liquidation are sent regardless; a manual exit's confirmation still shows the warning. |

Smaller choices accepted with "proceed":

- Legs of one ticket on the same contract and side are summed before the check.
- Freeze-limit slices are checked as one quantity against the current book; the book is not assumed to refill between slices.
- Outside market hours the check is "unknown" and nothing is shown. Bots already refuse to trade on non-live quotes (#52).
- The two existing tests above are replaced by this check, so there is one definition of "liquid".

---

## 2. What the feed gives us

| Room | Token | Fields we use |
|---|---|---|
| Quote (L1), subscribed today | `4.1!{token}` NFO, `8.1!{token}` BFO | `last`, `bPrice`, `bQty`, `sPrice`, `sQty`, `ltt` |
| Depth (L2), new | `4.2!{token}` NFO, `8.2!{token}` BFO | `depth[k]`: `BestBuyRate-k`, `BestBuyQty-k`, `BestSellRate-k`, `BestSellQty-k` for k = 1..5 |

Prices are rupees and quantities are units (the 2026-06-29 NIFTY capture shows `bQty: 65`, one
lot). `ltt` is breeze_connect's `%c` string in the process's local time; the mock sends epoch
seconds, so both are read. **The depth payload has never been seen live** (#33): the parser keys on
the `BestBuyQty-k` names both exchange layouts share, not on position, and the first live session
needs the tick-debug capture on.

breeze_connect stamps contract identity onto *every* message, depth included. A depth message
reaching the chain pipeline would read as a quote with no LTP and could blank a real cell, so
`ws_tick_pipeline.ingest_tick` hands depth messages to the raw listeners and stops there, as it
did while the W-OBI depth feed existed.

---

## 3. The check

`app/services/liquidity/estimate.py` is pure. For one contract, one side and one quantity:

1. **The book** is the depth levels when a depth message arrived this session, otherwise the single
   top-of-book level from the quote (labelled `top_of_book`), otherwise unknown.
2. **Walk it.** A sell fills against bids, best first; a buy against asks. The average price over
   the visible levels is the estimate. If the quantity exceeds the visible size, the reason is
   `beyond_book`.
3. **Compare with the LTP.** The adverse deviation (LTP − estimate for a sell, estimate − LTP for a
   buy) fails when it exceeds `max_deviation_pct` of the LTP **and** `min_deviation_ticks` ticks. A
   fill better than the LTP never fails.
4. **The LTP itself.** `stale_ltp` when the last trade is older than `ltp_stale_seconds`, or the LTP
   is strictly outside the current bid and ask.
5. **No other side.** `no_book` when there is no bid to sell into or no ask to buy from.

The result carries `ok`, the reason codes, the estimate, the deviation, the visible quantity, the
source (`depth` / `top_of_book` / `unknown`) and **`max_lots`**: the largest lot count up to the
requested one that passes the size test (deviation and visible book; staleness does not shrink).
The deviation only grows with quantity, so it is a binary search over a handful of levels.

Settings → **Liquidity checks** holds `max_deviation_pct` (10), `min_deviation_ticks` (5) and
`ltp_stale_seconds` (300), in one global row in `users.sqlite3` (the `pnl_engine_settings`
pattern), read fresh on every check.

---

## 4. Subscriptions

- `sync_holder_chain_subscriptions` subscribes each liquid contract's depth token beside its quote
  token, under the same holder, so they are released together.
- If ICICI refuses a depth batch (`subscribe_feeds` returns an error string), depth is marked
  **capped** for the day. From then on a chain subscribes depth only for the 10 strikes on either
  side of the money, using the latest spot reading and falling back to the middle strike.
- `POST /api/liquidity/check` subscribes the depth of the legs it is asked about as a lookup
  (released after 15 idle minutes, B-41), so a ticket's legs have depth even when capped.
- The book store lives in the API process (the socket's process), keyed by exchange and token.
  It is a few hundred small dicts.
- A refusal that ICICI reports as success, with no depth ever arriving, is not detected as a cap;
  those contracts simply fall back to the top of book, labelled as such.

---

## 5. Surfaces

| Surface | Change |
|---|---|
| Place Order | ⚠ beside Quantity. Before a side is chosen it checks both and names the side(s) that fail. |
| Basket, Strategy Builder legs | ⚠ beside each leg's quantity, from one check per page. |
| Confirmation modal (every manual ticket, square-offs included) | A red "⚠ …" line under each failing leg. Never blocks. |
| Strategy Builder proposals | Each candidate's lots capped at `min(max_lots)` over its legs; a strike whose 1-lot check fails is not "liquid". |
| Uncovered-shorts scan (`processor.get_options`) | The `total_buy_qty` cap becomes the sell-side `max_lots`. |
| Bot 1 Holdings Writer | Each leg shrinks to its own `max_lots`; a leg at 0 is dropped with a note. |
| Bot 2 Expiry Writer | Both legs shrink to the common `max_lots` in `execute_plan`; 0 skips with `liquidity_thin`. |
| Bot 3 Long Scalper | `plan_entry` caps lots; 0 skips. |
| Bot 4 Iron Fly | `plan_entry` caps lots after margin sizing (margin re-asked for the new size); below `min_lots` skips. |
| CAS Bingo | Checked before liquidation, so it never buys back shorts for an entry it then skips. |

Bots apply it in live and in paper (paper fills must not be better than live could get). Backtests
cannot: history carries no book. A skip or shrink uses the new reason code `liquidity_thin`,
appears in Activity and sends one Telegram alert.

---

## 6. Mock mode

`MockBreezeSdk` answers `.2!` tokens with a 5-level book around its random-walk price. The size
falls off with distance from the money, so far strikes warn at modest quantities and the guide
screenshots can show the warning.

---

## 7. Not covered

- **Hidden liquidity** beyond 5 levels, and iceberg orders, are unknowable from this feed.
- **Book refill** between freeze slices is not modelled; large sliced orders read pessimistic.
- **Futures and equity tickets** are not checked; this is an options feature.
- Whether ICICI caps subscriptions per session (B-41) and the live depth payload shape are both
  unverified until the first live session.
