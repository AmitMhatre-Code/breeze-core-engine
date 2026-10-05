# Dynamic Iron Condors — campaigns, Portfolio manager, backtest, bot

Status: **all five steps built** (2026-10-03, uncommitted): engine (#63); backtest, campaigns, scheduled checks, Portfolio card, Iron Condors page (#64; the page was later folded into the bot card and Basket Orders, #67); executor and Adjust ticket (#65); the bot with its paper → Telegram → auto gates (#66). Not yet proven against the live broker. Every decision in §1 was put to the user and
answered on 2026-10-02; nothing in it is an assumption. Source material: the user's Gemini
conversation (https://share.gemini.google/tcD1MjXdC38s), read in full, 16 turns.

---

## 0. What the source got wrong, recorded once

The method (sell both sides, buy wings, keep net delta near zero by rolling the untested side,
45 → 21 DTE, tranche into one expiry, manage the block) is standard and is what this plan
builds. These parts of the conversation must not leak into the build:

1. **Expiry days.** It says NIFTY expires Thursday and SENSEX Friday. They are Tuesday (NSE) and
   Thursday (BSE). Expiries come from the scrip master, never from a weekday rule.
2. **The "August–September 2026" campaign is invented.** Gemini says it has no data. Its DTE
   arithmetic does not close (Aug 28 = 27 DTE, Sep 4 = 21 DTE), and it switches lot size from 65
   to 75 mid-answer. Taken at face value it is **−16% of deployed margin in one trending month**
   against a 1.5–2.5%/month target: one such month erases about six good ones. How often that
   month happens is the whole question, and only §5's replay on real prices answers it.
3. **Delta wings and point wings disagree.** At VIX ≈ 12 and 45 DTE, one sigma is about 1,000
   NIFTY points. A 20Δ short is roughly 850–900 points out, a 5Δ wing about 1,700, so the
   20Δ→5Δ gap is about 850 points, not "400–600". Its worked example (24,200 spot, 23,700/24,700
   shorts) is nearer 30Δ. Its margin figures (₹55k a lot, 109 lots) are unverified, and ICICI's
   margin is not linear in quantity.
4. **Its adjustment rules overlap with no precedence.** §3 gives one fixed order.
5. **"Confirm the breach at 3:15 PM"** is the start of the closing auction (since 2026-08-03).
   The user moved the end-of-day check to 15:31, after the auction close.
6. **"Roll out for a credit to absorb the loss"** is bookkeeping. The loss is realised either way;
   the ledger (§2) shows realised P&L and new credit separately.
7. **SENSEX** 45-DTE monthlies are thin and BFO options carry no OI (§8.7a of
   `bots-scalping-plan.md`). Out of scope for v1.

---

## 1. Decisions agreed with the user (2026-10-02)

| Topic | Decision |
|---|---|
| Sequencing | One shared rule engine built first; backtest and Portfolio manager built on it in parallel; bot after |
| Underlying | **NIFTY only** in v1. Code takes the underlying as a parameter so SENSEX can follow once liquidity checks show its monthlies can take the size |
| Expiry cycle | **Configurable**, weeklies included: expiry kind (monthly / any), entry DTE, tranche cut-off DTE, exit DTE are settings. Monthly 45 / 30 / 21 is the first preset |
| Identity | An **explicit campaign** the user creates or adopts. It owns a ledger that survives rolls and expiry changes |
| Strike selection | **Delta shorts, delta wings** (defaults 20Δ / 5Δ), so one rule works at any DTE. Credit ÷ width is shown, never enforced |
| Roll target | The untested short goes to the **tested side's \|Δ\|**, **capped at the tested strike** (iron fly). **Never invert** |
| Wing on roll | **Same point width** as the tested side's wing |
| Tested side | **Never moved** by an adjustment. It closes only through max-loss, exit DTE, break-even exit or a time roll |
| Roll trigger | **Either** the leg rule (untested side < 10Δ or ≥ 80% decayed) **or** the block rule (\|net Δ\| per lot outside a band), whichever first |
| Minimum roll credit | A roll must add at least a set **net** credit (after buy-back, wing change and charges), else it is skipped and logged |
| Rolls near the exit | **No rolls within N days of exit**, a setting, **default 0 (off)** (2026-10-03). The flat-market replay rolled a day before the 21-DTE exit, so the new short was held one day and the roll mostly paid the spread. A due roll inside the window is reported, not done. Backtest N=0 against N=3 and keep the better |
| Timing | Decisions only at two scheduled checks: **start-of-day 10:30** and **end-of-day 15:31** (after the auction close, options trade to 15:40). Both times are settings |
| Max-loss stop | **Yes**, on campaign P&L (realised + open). **Evaluated only at the two checks**, not on ticks (user's choice, see §9) |
| Credit basis | **Net of everything**: short premium − wing cost − realised roll losses − brokerage/STT/charges, all from actual fills |
| At exit DTE | Manual: **ask** (time roll or close, with preview). Bot: a setting |
| Beyond break-even at the cap | At the 15:31 check, if the position is at the straddle cap and spot is beyond break-even: **early exit**, treated like exit DTE (ask / bot setting) |
| Tranche ejection | **Not built.** Max-loss, exit DTE and the straddle cap cover the case |
| Sizing | A ₹ **margin ceiling** per campaign; the rest of capital is the buffer. **N tranches** spread evenly between entry DTE and cut-off DTE, same expiry, managed as one block |
| Entry check | Which check (10:30 or 15:31) enters tranches is a **setting** |
| Coexistence | **Exclusive + flag.** A campaign owns its NIFTY+expiry group: arming PB/SL there is refused. Fills not placed by the campaign are flagged for the user to assign or leave out |
| Bot rollout | **Backtest → paper → Telegram approval → auto.** Armable only after a backtest of its saved settings |
| Manual adjustments | An **Adjust ticket** on the campaign card: templates or blank, every leg editable, before/after preview, executed through the campaign executor (§6a) |
| Manual vs engine rules | **Warn, overridable.** The only hard block is a final position with a short that has no wing |
| Manual on a bot campaign | Executing a manual ticket **pauses the bot** for that campaign until the user resumes it |
| Square off on a campaign group | **Routed through the campaign** as "Close selected": campaign executor, recorded in the ledger |

---

## 2. The campaign and its ledger

ICICI's positions have no strategy concept, and a Strategy Group knows only the legs open now
(`strategy-group-pbsl-plan.md` §1). The ledger is the new thing this plan adds; everything in §3
reads from it.

**Schema** (new migration, `app/db/condor_migrate.py`):

| Table | Holds |
|---|---|
| `condor_campaigns` | id, user_id, underlying, exchange, origin (`manual`/`bot`), mode (`live`/`paper`), status (`active`/`closed`), settings JSON (frozen at creation, versioned on edit), margin ceiling, created/closed timestamps |
| `condor_cycles` | campaign_id, expiry, opened_at, closed_at, close_reason (`exit_dte`, `breakeven_exit`, `max_loss`, `time_roll`, `manual`) |
| `condor_fills` | campaign_id, cycle_id, order_id, app tag, contract (strike, right), side, quantity, fill price, charges, kind (`entry`, `roll_open`, `roll_close`, `wing_open`, `wing_close`, `exit`, `manual_adjust`, `adopted`, `assigned`), filled_at |
| `condor_decisions` | campaign_id, check time, check kind (`sod`/`eod`/`on_demand`/`manual`), compact input snapshot, action, reason code, outcome, user note (manual only), overridden warnings (manual only). The backtest writes the same shape, so live and replay are directly comparable |

**Attribution.** Orders placed by the campaign carry a fresh app tag (#61) and are journalled
through the `order_intents` pattern (#54), so their fills attach automatically. Fill prices come
from the broker, never from order-notification fields (#58, `strategy-group-pbsl-plan.md` §3).
Any other fill on NIFTY at a campaign expiry is shown as **unassigned** until the user assigns it
or leaves it out. A left-out fill stays visible as "outside the ledger".

**Adopt.** A NIFTY group that already exists can be adopted: its open legs become `adopted` fills
at the broker's average price, labelled as such.

**Exclusivity.** `strategy_group_arm_guard` refuses to arm PB/SL on a group a live campaign owns;
creating a campaign on a group with an armed SG is refused with an offer to disarm it.

---

## 3. The rule engine

`app/services/condor/engine.py`: a **pure function**, no I/O:
`decide(campaign_state, market, settings, check_kind) -> Decision`. The backtest, the Portfolio
card and the bot all call it, so whatever is tested is exactly what trades.

**Inputs.** Open legs and ledger totals; cycle expiry and DTE; spot with provenance (#50); per-leg
and per-candidate bid/ask/LTP and IV from the chain; settings.

**Delta** uses the same model as the Portfolio leg-delta column (expiry clock to 15:30 IST, parity
forward, per-strike IV). The engine computes delta on the backend (`options_strategy_engine/greeks`),
the card shows the engine's figures, and a parity test pins backend against
`frontend/src/lib/strategy-builder/greeks.ts`.

**Sides with tranches.** The *tested* side is the side whose shorts have the larger quantity-weighted
\|Δ\|. All shorts on the untested side roll together into **one** new strike (Gemini's "block roll").
The wing width used for the new untested wing is that of the tested side's nearest-the-money
short/wing pair.

**Priority, first match wins:**

| # | Condition | Action |
|---|---|---|
| P0 | Any open leg unpriced, spot not live, or a feed down | **No decision** (`unavailable`), Telegram alert. Never treated as "nothing to do" (#27, #60) |
| P1 | Campaign P&L (ledger realised + open legs at buy-back prices: ask for shorts, bid for longs) ≤ −max-loss (₹ or % of ceiling, tighter binds) | **Close all** |
| P2 | DTE ≤ exit DTE | **Exit or time roll** (manual: ask; bot: setting) |
| P3 | At the straddle cap, EOD check, spot beyond a break-even | **Early exit or time roll** (as P2) |
| P4 | Leg rule or block rule fires | **Roll the untested side** to the tested \|Δ\|, capped at the tested strike, wing at same width. Skipped (`roll_credit_below_min`) if net credit after charges < minimum |
| P5 | Tranche due (schedule, at the configured check, DTE ≥ cut-off, margin headroom) | **Enter tranche** at short Δ / wing Δ, wings snapped outward |
| P6 | Otherwise | No action |

**Break-evens** are solved from the open legs' expiry payoff plus the ledger's realised net, so
they stay correct for any shape (condor, lopsided, fly) and include every past roll.

**Time roll** = close the cycle (P1's close sequence), then P5 entry into the expiry nearest the
entry DTE (respecting expiry kind), sized to the campaign ceiling. The ledger carries over.

Defaults that the backtest must calibrate before they are trusted: net-Δ band (start 0.15 Δ per
lot), minimum roll credit (start 20 points), max-loss (start 5% of ceiling).

---

## 4. Execution — one executor for card and bot

`app/services/condor/executor.py`. Every action is an ordered list of orders sent **one at a time**
(#24), each waiting for its fill on the order feed before the next goes, quantities above the
freeze limit sliced, also one at a time.

| Action | Order sequence (no naked short exists at any step) |
|---|---|
| Entry | BUY call wing → BUY put wing → SELL short call → SELL short put (Bot 4 §4.3) |
| Roll untested side | BUY new wing → BUY back old short → SELL new short → SELL old wing |
| Close all | BUY back shorts → SELL wings (`held_legs.close_sequence`) |
| Any manual ticket (§6a) | Sorted into four groups, in this order: BUY new longs → BUY back shorts → SELL new shorts → SELL old longs. Inside a group: the side nearest the money first |

The sort is the executor's job, not the caller's. A ticket in any leg order goes out in the safe
order, and at no point does the account hold a short whose wing has not been bought yet.

**Partial failure.** Steps 1–2 failing aborts with the old position intact (an extra long wing is
harmless). Step 3 failing leaves that side short-less but hedged; the next check retries. Step 4
failing leaves an extra long wing; it is retried. Every intent is journalled and settled from the
broker (#53, #54); a lost answer is looked up, never re-sent (#55).

**Guards.** Liquidity (#62): entries and rolls shrink or skip, closes are never held. Read-only
licence mode: entries and rolls are blocked (a roll opens a short), close-all is allowed (#57).
Margin: one `margin_calculator` call on all legs of the action (`scalping/margin.py`, action-aware),
shown as incremental to the portfolio (#23).

---

## 5. Backtest — on the engine, on real prices

Built on the `bots-scalping-plan.md` §8.7 harness: demand-driven fetches, `missing`/`no_data`/
`no_trade` states, a daily call budget (#42), and memory bounds (#41).

- **Replay only at the check times**, because the engine decides nowhere else. Spot is the index
  bar; each option is its traded price at the check minute; IV is implied from that price
  (`iv_compute`, parity forward from the ATM pair); fills are traded price ± the modelled
  half-spread (paper-mode samples); charges from `bots/charges.py`; margin is today's (#36).
- **Bars:** 5-minute (a new `backtest_store` interval). 1,000 rows cover about 13 sessions, so a
  contract costs about 2 calls per monthly cycle, against about 10 at 1-minute. The 15:30 bar's
  open stands in for 15:31. **Verify** that ICICI serves 5-minute option bars to 15:39, and spot-check
  them against 1-minute on a sample, before relying on them.
- **Strikes** are chosen by the engine from ATM IV, then only those contracts are fetched. That is
  the same "stop the day, queue the need, fetch, replay" loop.
- **Output per cycle:** every decision with its reason, the ledger, P&L, worst drawdown at a check,
  margin used. Aggregates over cycles; monthly and weekly presets side by side. Findings go to
  `guide/technical/backtest-findings.md`.
- **Honest reach:** history starts 2026-01-05, which gives about 8 monthly cycles and about 38
  weekly. That is enough to prove the mechanics and size the losing months; it is not enough to
  prove an edge.

---

## 6. Portfolio manager

The campaign attaches to the NIFTY+expiry group row in `OpenPositionsTable`.

- **Group row:** "Start campaign" / "Adopt into campaign" on a NIFTY group; a campaign badge once owned.
- **Campaign card:** cycle expiry and DTE; next check time; per-side short \|Δ\| and net Δ per lot
  against the band; untested-side decay %; the ledger summary (total net credit, realised,
  open MTM, campaign P&L against max-loss); break-evens; margin used against ceiling; tranche
  schedule progress; last decision and its reason.
- **Suggested action** from the last scheduled check. "Evaluate now" runs the engine off-schedule,
  labelled as such, and never acts.
- **Preview → Execute:** the order sequence with prices, net credit after charges, Δ after,
  incremental margin, liquidity ⚠, minimum-credit check. Execute hands it to §4.
- **Unassigned fills** banner: assign or leave out.
- **Ledger and decision history**, per cycle.
- **Telegram**: when a scheduled check produces an action on a manual campaign, an alert with the
  action and a link to the app.
- **User guide**: new `guide/user/` section and screenshots in the same change (CLAUDE.md rule).

### 6a. Manual adjustments — the Adjust ticket

Today the Portfolio screen can only square off selected legs (`SquareOffLegsModal`), arm PB/SL
and suggest hedges. It cannot roll, add, or reshape. A campaign card gets an **Adjust** button
that opens a ticket scoped to the campaign.

**Templates.** Each one is pre-filled with the engine's strike-by-delta helpers (§3) and can be
edited leg by leg (strike, quantity, price):

| Template | Pre-fills |
|---|---|
| Suggested action | The last check's proposal |
| Roll selected legs | Legs ticked with the existing row checkboxes → a new strike chosen by strike or by Δ; the wing follows at same width (toggle, on by default) |
| Convert to iron fly | Untested short(s) to the tested strike, wing at same width |
| Add tranche | One tranche at the entry Δs and the size of one tranche |
| Close side / Close all | Shorts bought back, then wings sold |
| Time roll | Close this cycle; open the next expiry at the entry Δs |
| Blank | Free legs, NIFTY at any expiry the campaign may use |

**Before / after**, recomputed on every edit: payoff (reusing `BasketPayoffPanel`), net Δ per lot,
net credit after charges, the campaign's total net credit and break-evens, incremental margin
(#23), worst loss at the wings per side, liquidity ⚠ per leg (#62).

**Checks.** Breaking an engine rule is a **warning the user can override**: moving the tested side,
inverting, a roll below the minimum credit, a strike chosen against the engine's suggestion,
wings at unequal widths, exceeding the margin ceiling. Overridden warnings are written to
`condor_decisions` with the user's optional note. The one **hard block**: the final position
would hold a short with no long on the same side and right to cap it. The max-loss figure and
the margin ceiling both assume the wings exist.

**Execute** hands the ticket to the §4 executor: sorted into the safe order, one order at a time,
tagged, fills recorded as `manual_adjust`. Read-only licence mode blocks a ticket that opens any
short; a ticket that only closes is allowed (#57).

**After a manual adjustment** nothing needs resyncing. The engine reads only the current legs
and the ledger, so the next check evaluates the new position. Any pending suggestion is cleared.
If a **bot** runs the campaign, executing a ticket **pauses the bot** for that campaign until the
user resumes it, with a Telegram message saying so. It never undoes the user's change at its next check.

**Square off on a campaign group** becomes "Close selected": the same dialog and price controls,
but sent through the campaign executor (shorts first) and recorded in the ledger. Orders placed
anywhere else (Place Order, Basket, ICICI's own app) still arrive as unassigned fills (§2).

**Scheduled checks.** A runner (off the event loop, #61, on the `bots/scheduler.py` pattern) runs
each active campaign at the SOD and EOD times. It pins the campaign expiry's chain subscription a
few minutes before each check, so the quotes it reads are live (#50, #19), and releases the pin
afterwards. Quotes come from the WS feed; REST is spent only on margin, the trade list for
attribution, and orders.

---

## 7. The bot

`bot_type: "dynamic_condor"`. Its config is a campaign's settings plus: ceiling, mode, the P2/P3
action (time roll / close), the entry check. One active campaign per bot.

- **Arm gate:** a completed backtest of the exact saved settings, newer than the last settings change.
- **Modes, in order:** paper (simulated fills at live quotes, `scalping/paper.py` pattern) →
  Telegram approval (`bots/hitl.py`: re-price and refuse on approve; silence places nothing) →
  auto. Each mode is a switch the user turns on.
- **Guards:** pause when a feed it needs stops (#52), live quotes only (#50), liquidity shrink/skip
  (#62), licence, API budget, refuse on SG clash (CAS Bingo §8 pattern).
- **Manual override:** a manual ticket executed on the bot's campaign pauses the bot (§6a).
  Resuming is explicit; the bot then evaluates whatever the position is at its next check.

---

## 8. Build order

1. **Engine + settings model + delta strike selection** (pure; unit tests on hand-worked
   scenarios, including Gemini's 800-point gap, the whipsaw, and the iron-fly cap).
2. **Backtest harness on the engine** (§5), run on the monthly and weekly presets. Built in
   parallel with 3.
3. **Campaign schema, ledger, attribution, Portfolio card, read-only** (numbers and the suggested action).
4. **Executor, preview and execute, the Adjust ticket and "Close selected" (§6a), Telegram
   alerts, PB/SL exclusivity.**
5. **Bot:** paper → approval → auto.

Each step lands with its design-decision entry (#63 onward) and guide updates.

---

## 9. Risks and open items

- **Max-loss only at checks** (user's choice). A crash day runs until 10:30 or 15:31. The wings
  bound the worst case at (wing width − net credit) per unit per side; the card shows that number
  so the trade-off is visible.
- **Orders between 15:31 and 15:40:** confirm with one small live order that ICICI accepts NIFTY
  option orders there and that the book is usable. Mock mode cannot show this.
- **5-minute history bars** are unverified (§5).
- **Liquidity at size:** 5Δ wings at 45 DTE may not take a full ceiling's quantity; #62 will shrink
  entries. Serialized, sliced execution means spot moves during a large roll.
- **Delta parity** between the backend engine and the frontend leg column must be pinned by a test.
- **Margin:** ICICI's figure is not linear in quantity; without the portal add-on, SPAN toggles
  fall back to ICICI (#48).
- **Sample size** (§5).
