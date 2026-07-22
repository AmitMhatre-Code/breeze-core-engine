# User Acceptance Test (UAT) Plan

Manual regression checklist for a **post-release smoke test** of Breeze Modern. Intended
audience: the deployment owner (or a delegated tester), run against a real deployment
after shipping a new image — not an automated suite. See `docs/functionality.md` for the
full feature map and `docs/architecture.md` for how the pieces fit together.

## Why this is split by market hours

A meaningful chunk of this app's behavior is conditional on **NSE/BSE market state**
(`app/services/market_calendar.py::is_market_open()`), not just feature flags:

- **Order execution vs. parking** — placing an order when the market is open executes it
  immediately; placing the same order when the market is closed silently **parks** it for
  later ("AMO"-style) instead of erroring. These are two different code paths and both need
  independent verification — a release can break one without breaking the other.
- **Quote source** — option chain cells are served from **WebSocket ticks** when the market
  is open, from the previous session's **bhavcopy (EOD file)** when it's closed and fresh, or
  a **REST fallback** when bhavcopy is stale (`quote_source_router.resolve_quote_source`).
- **Dashboard P&L reference price** — the Day's P&L tile behaves differently in each of four
  session states: `open`, `pre_open`, `post_close`, `closed_non_trading_day`.
- A test case run at the wrong time of day will exercise the wrong code path and can produce
  a **false pass** (e.g. testing "order execution" after hours actually only tests parking).

Run **Section A** any time. Run **Section B** during a live trading session (Mon–Fri,
9:15–15:30 IST, non-holiday). Run **Section C** after market close or on a
weekend/holiday. If a release lands mid-week, you can spread B and C across two days —
they don't need to happen in the same session.

> **Live vs. mock broker mode**: live ICICI order/quote calls only succeed from this
> deployment's whitelisted static IP (see broker session constraints) — run this plan
> against the actual deployed instance, not a local dev checkout, if `ICICI_BROKER_MODE=live`.
> If the instance runs in `mock` mode, order-placement outcomes are simulated; still run every
> case, but treat "did it execute vs. park" as a logic check rather than a real fill.

## Setup checklist (once per UAT pass)

- [ ] Confirm which build/tag is deployed (compare against the release you're validating).
- [ ] Confirm license status is `active` (`/settings` or the top banner) — a lapsed/revoked
      license puts the app in read-only mode and every mutation case in Section B/C will
      403 by design, not by bug (see `docs/functionality.md` — license/read-only mode).
- [ ] Have one ICICI-linked test account ready (or the account you're comfortable placing a
      small real/mock order with).
- [ ] Open only the app's single public origin (e.g. `https://<host>:3000` or your nginx
      port) — don't hit the backend port directly, or cookies/OAuth state will mismatch.
- [ ] Have a note of the current NSE session state so you know which of Section B/C applies
      today: check the navbar dot next to "ICICI Breeze" (green = market open/ticking).

## How to record results

For each case: ✅ Pass / ❌ Fail / ⚠️ Pass-with-notes. Capture a screenshot on any ❌ and the
exact error text (correlation ID if shown — every error JSON carries one for support).

---

## Section A — Run any time (market open or closed)

These flows have no market-hours dependency; run them regardless of session state.

### A1. Auth & account lifecycle

| # | Case | Steps | Expected |
|---|------|-------|----------|
| A1.1 | Direct login | Go to `/login`, sign in with app user id/password | Redirects into the ICICI broker login step |
| A1.2 | ICICI broker login | Complete the ICICI redirect/challenge flow | Lands on `/dashboard` fully authenticated |
| A1.3 | Logout | Use `/logout` | Session cleared; protected pages redirect to `/login` |
| A1.4 | Legacy login blocked | Hit the old `/auth/login` path directly (if you have a bookmark) | HTTP 410 — confirms deprecated path stays disabled |
| A1.5 | Registration (new account, non-production instance only) | `/register` → direct user id/password + ICICI credentials | Account created, can subsequently log in |
| A1.6 | Correct broker credentials | `/register/correct` | Updates stored ICICI API credentials for existing account |
| A1.7 | Forgot/reset app password | `/register/forgot-password` → `/register/recover-complete` | Password reset completes, can log in with new password |

### A2. Dashboard & read-only data

| # | Case | Steps | Expected |
|---|------|-------|----------|
| A2.1 | Dashboard loads | Visit `/dashboard` | Home summary renders without error; license banner absent (if active) |
| A2.2 | Portfolio holdings | Visit `/portfolio` | Holdings/positions list renders from `/portfolio/data` |
| A2.3 | Order book (read) | Visit `/orders`, view the order list (not placing anything yet) | Existing orders list from `/book/data` renders |
| A2.4 | Performance page | Visit `/performance` | Performance metrics render from `/performance/data` |
| A2.5 | Strategies summary | Visit `/strategies` | Hedge / vertical-spread / uncovered-shorts summary tiles render |

### A3. Settings

| # | Case | Steps | Expected |
|---|------|-------|----------|
| A3.1 | Credentials page | `/settings/credentials` | Shows masked ICICI API key/secret state |
| A3.2 | Quantity limits | `/settings/quantity-limits` | Existing limits list renders, edit saves |
| A3.3 | Margin source | `/settings/margin-source` | Breeze vs. exchange baseline toggle works; SPAN file upload accepted |
| A3.4 | Scrip master refresh | `/settings/scrip-master` | Manual refresh trigger completes, shows last-updated timestamp |
| A3.5 | Reference-data loads | `/settings/reference-data-loads` | Shows NSE/BSE bhavcopy + scrip + SPAN load status/history; "Load now" can be triggered on demand regardless of time of day |
| A3.6 | Exchange calendar | `/settings/exchange-calendar` | Holiday list and session hours visible/editable; portal sync (if configured) succeeds |
| A3.7 | API usage | `/settings/api-usage` | Usage stats render |
| A3.8 | Breeze API playground | `/settings/breeze-api-playground` | A read-only ICICI method call (e.g. `get_funds` or similar) succeeds against the live session |
| A3.9 | Strategy audit logs | `/settings/strategy-audit-logs` | Past strategy-builder audit entries browsable |
| A3.10 | Account deletion entry (don't complete) | `/settings/delete-account` | Confirmation UI appears; **do not submit** unless intentionally decommissioning the test account |

### A4. Market outlook & license

| # | Case | Steps | Expected |
|---|------|-------|----------|
| A4.1 | Market outlook widget | Dashboard outlook panel | RSS headlines + AI narrative render (pulled from portal, not generated locally) |
| A4.2 | License status | `/settings` or dashboard banner | Shows `active` (or correct current state) matching breeze-saas-portal's console |
| A4.3 | Health endpoint | `GET /health` (via browser or curl through the app origin) | Returns healthy status incl. Redis connectivity/fallback info |

---

## Section B — Run only during live market hours (Mon–Fri, exchange open, non-holiday)

These exercise the **live order execution** and **WebSocket quote** code paths, which
simply don't run outside market hours — running them after close will silently test the
wrong thing (parking, not execution).

| # | Case | Steps | Expected |
|---|------|-------|----------|
| B1 | Navbar market-data health | Look at the "ICICI Breeze · …" dot in the top nav | Dot is **green**, reason text says market open / ticking, not "market closed" |
| B2 | Live option chain | Open `/trade-options-chain` for NIFTY or SENSEX, watch a strike row for ~30s | LTP/bid/ask update live without a manual refresh (WebSocket-sourced ticks) |
| B3 | Live index ticker | Watch the NIFTY/SENSEX price in the navbar/dashboard | Value updates periodically, not frozen at a single snapshot |
| B4 | Strategy builder chain | `/strategy-builder`, pick an underlying, load the chain step | Chain populates with live prices, not a stale/EOD banner |
| B5 | **Live order placement** | From `/trade-options-chain` or `/strategy-builder`, place one small real (or mock-mode simulated) order | Order **executes immediately** — order confirmation shows a broker order id, *not* "parked for execution"; new order appears in `/orders` live list |
| B6 | Live order in book | Refresh `/orders` after B5 | The order placed in B5 shows with a live/broker status, not in the "parked" section |
| B7 | Hedge / vertical-spread / uncovered-shorts scan | `/hedge`, `/vertical-spread`, `/uncovered-shorts` | Scans return current-session candidates using live pricing |
| B8 | Day's P&L tile (open state) | Dashboard P&L tile, with at least one open position | Reflects intraday MTM movement (`open` session state), updates on refresh |
| B9 | Order margin check | Attempt to place an order sized to exceed available margin | Live margin check blocks it with a clear insufficient-margin message (not silently parked) |
| B10 | Cancel a live order | Cancel the order placed in B5 (or another open one) if still working | Cancels successfully via the broker, disappears/updates status in `/orders` |

> If your license is not `active`, B5/B9/B10 (and any other mutation) will correctly 403
> with a read-only message — that's expected per `docs/functionality.md`, not a failure of
> this test; verify the *message*, not that the order actually placed.

---

## Section C — Run only outside market hours (after close, before next open, weekend, or holiday)

These exercise the **AMO/parking** path and **EOD (bhavcopy) quote** path, which only
activate when the market is closed.

| # | Case | Steps | Expected |
|---|------|-------|----------|
| C1 | Navbar market-data health | Look at the "ICICI Breeze · …" dot | Dot is **not green** (gray/red), reason text explains why (e.g. "after market close", "weekend", "exchange holiday") |
| C2 | EOD-sourced option chain | Open `/trade-options-chain` for NIFTY/SENSEX | Chain still populates (from previous session's bhavcopy) but prices are static — no live ticking; page shouldn't error or hang |
| C3 | **Order parking (AMO)** | Place an order the same way as B5 | Order does **not** execute; response/toast says "Market is closed (...). Order parked for execution — execute it from Parked Execution when the market opens" |
| C4 | Parked order visible | Go to `/orders`, find the Parked Orders section | The order from C3 appears there with correct contract/qty/price |
| C5 | Edit a parked order | Adjust qty or price on the parked order from C4, save | Edit persists (`PATCH` on `/book/parked-orders`) |
| C6 | Attempt to execute a parked order while still closed | Try "Execute" on the parked order from C4/C5 | Rejected with "Market is still closed (...). Your order remains parked" — it must **not** silently execute |
| C7 | Delete a parked order | Remove the parked order from C4 | Removed from the list, no longer shown on next load |
| C8 | Day's P&L tile (closed state) | Dashboard P&L tile | Reflects one of `pre_open`/`post_close`/`closed_non_trading_day` — reference price uses previous session's close, not a live tick |
| C9 | Index ticker EOD snapshot | Navbar/dashboard NIFTY/SENSEX price | Shows previous session's close as a static value (fetched once via REST), does not appear to "tick" |
| C10 | Strategy builder chain (closed) | `/strategy-builder` chain step | Loads using EOD/bhavcopy data; UI should indicate stale/closed pricing rather than pretending it's live |
| C11 | Admin/integration test harness | `/admin` → run tests (if you use this internally) | Runs without the "blocked during market hours" 403 — this endpoint is intentionally restricted to non-market hours unless explicitly overridden |

---

## Section D — Optional: weekend / exchange-holiday specific

Only worth doing once per release if Section C already ran on a plain weekday evening;
skip if time-constrained.

| # | Case | Steps | Expected |
|---|------|-------|----------|
| D1 | Non-trading-day P&L state | Dashboard P&L tile on a Saturday/Sunday/holiday | Session state is `closed_non_trading_day`, not `pre_open`/`post_close` |
| D2 | Exchange calendar holiday entry | `/settings/exchange-calendar` | Today (if a holiday) is listed with the correct holiday name, matching `market_closed_reason()` text shown elsewhere in the app |

---

## Sign-off

| Section | Run by | Date | Result |
|---|---|---|---|
| A — Any time | | | |
| B — Market hours | | | |
| C — After hours | | | |
| D — Weekend/holiday (optional) | | | |

**Release approved for production traffic:** ☐ Yes ☐ No — blockers noted above.
