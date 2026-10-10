# Premium gate — sell when premium is rich, buy when it is cheap

Status: **all steps built** (2026-10-08/09, uncommitted; design-decisions #75). Step 6: without a live ATM book the Holdings Writer reads last-traded prices and marks the leg indicative (the user's choice). Step 2 of the build that followed the 2026-10-07
options-trader review (step 1 was #74, scoring calls from the next trade).

---

## 1. Decisions (the user's, 2026-10-08)

| # | Question | Answer |
|---|---|---|
| 1 | Which bots | **Expiry-Day Index Writer** and **Intraday Iron Fly** sell only when premium is rich; the **Long Scalper** buys only when premium is cheap. The Dynamic Iron Condor: advice requested (section 6), not built now |
| 2 | Threshold | A **per-bot setting**, and every bot backtest replays the gate **off and at 0.9 / 1.0 / 1.2** beside the saved setting |
| 3 | Strike distance | Bot 2 (then Holdings) gets a **"distance by"** choice: % of spot (today's) or a **multiple of the implied move**. New bots default to the implied move; saved bots keep % until the user switches |
| 4 | Hedged shapes for Bot 2 | **Later** |

House rules that apply without asking: no environment variables (in-app settings only), the gate fails
closed (a reading that cannot be made is "no trade", never "fair"), and live and replay compute the
reading with the same code from the same kind of data.

---

## 2. The reading

One number: **implied move ÷ forecast move**, both measured from now to the option's expiry close.

**Implied move** — from the ATM call and put of the expiry the bot trades:
- forward from put-call parity at the ATM strike, `F = K + (C − P)·e^{rT}`;
- the IV of each side at that forward (`condor.pricing.implied_volatility`, the same solver the
  Portfolio deltas use), averaged;
- total implied variance to expiry `w = σ²·T`. Total variance does not depend on the day-count
  convention the IV was quoted in, which is what makes it comparable with a forecast built from
  trading sessions.
- Live prices are trusted mids of a two-sided book; replays use the traded price at that minute
  (there is no book in history), which the run notes say.

**Forecast move** — from the cash index's own one-minute bars (`spot_candles`, NSE NIFTY and BSE
SENSEX), never the futures: BSESEN futures do not trade in about half of all minutes.
- Each past session gives its **intraday variance** (sum of squared one-minute log returns, the
  first from the session's open) and its **overnight variance** (the squared gap from the previous
  session's close).
- Intraday forecast = ½ × mean of the last 5 sessions + ½ × mean of the last 20 (a fixed, unfitted
  blend: recent volatility counts, but so does the month). Overnight forecast = mean of the last 20.
- **Time of day**: index volatility is U-shaped, so "how much of today is left" is measured, not
  assumed. The share of a session's variance still to come after each minute is averaged over
  the same 20 sessions.
- Forecast variance to expiry = intraday × today's remaining share + (sessions after today up to
  and including expiry) × (overnight + intraday).
- Needs at least 15 complete sessions; fewer is `unavailable` (no trade).

**Ratio** `R = √(w / V)`. Sellers trade when `R ≥ threshold`; the scalper buys when
`R ≤ threshold`. Default threshold 1.0 for both.

Versioned (`GATE_VERSION = 1`) like a signal: changing what the reading computes is a new version,
and it is stamped on every backtest run and in each bot's settings fingerprint.

---

## 3. Where each bot checks it

| Bot | When | Fails closed means |
|---|---|---|
| Expiry-Day Index Writer | when it plans a trade (scheduled, Telegram re-price, or manual run sheet, which shows it but lets you place) | the index is skipped for the day with the reading in the reason |
| Intraday Iron Fly | every pass, beside the re-entry gate and the entry filter | waits, as the other filters do |
| Long Scalper | when a fresh signal call would open a trade | that call is skipped (one trade per call still holds) |

The reading appears in each bot's Activity reason ("Premium 1.24× the forecast move: implied ±0.82%,
forecast ±0.66% to expiry").

**Saved bots keep the gate off; new bots start with it on** (`repositories.bots` new-bot defaults,
not the model default, so a stored config without the field means "off"). A gate that is off is
dropped from the scalpers' evidence fingerprint, so no saved bot loses its Simulation evidence.

---

## 4. Backtests

- The cash-index bars for the range plus a month before it are fetched (they are also what Bot 2 and
  the fly already read spot from).
- At each entry decision the replay computes the reading from the sessions before that day and the
  ATM call and put's traded prices at that minute. The fly's shorts are the ATM pair already; Bot 2
  and the scalper add the two (or one) contracts per day.
- Comparison rows: the gate **off, 0.9, 1.0 and 1.2**, each at the bot's saved other settings — one
  factor at a time, not crossed with the signal grid, so the scalper compares 24 + 4 rows, not 96.

---

## 5. Strike distance by implied move (Bot 2, then Holdings)

`distance_basis: "pct" | "implied_move"`; with `implied_move`, each side's strike sits at
`spot × exp(±k·√w)`, rounded away from spot as today, with `k` per side (default 2.5 for Bot 2 — about
where 2% sits on an ordinary expiry morning). The same `w` the gate computes.

---

## 6. The Dynamic Iron Condor (advice, not built)

Yes, in principle: a short-premium campaign earns the gap between implied and realized volatility, and
entering when that gap is wide is the main lever it has. Three differences from the intraday bots:
- the horizon is weeks, so the forecast uses whole sessions (overnight included) over 20–60 sessions,
  and the implied side is the 45-DTE monthly ATM;
- it gates **tranche entries only**, never rolls or exits — a campaign already open must still be
  managed;
- about eight monthly cycles of ICICI history cannot show whether it helps. It belongs with the
  bhavcopy-history step (step 5), which can replay years of month-end decisions.

---

## 7. Build order

1. Pure reading module + tests (`services/premium_gate/`).
2. Live inputs: cash-index history cache and daily forecast; live ATM pair from the chain.
3. Bot 2: gate + distance by implied move; settings UI; guide.
4. Iron Fly gate; Long Scalper gate; settings UI; guide.
5. Backtests: replay the reading and the four comparison rows for all three.
6. Holdings Writer distance by implied move.
7. Design decision #75, architecture/functionality docs.
