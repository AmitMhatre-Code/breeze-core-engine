# Dynamic Iron Condor — long history on NSE daily prices

Status: **built** (2026-10-09, uncommitted; design-decisions #77). Step 5 of the build after the 2026-10-07 options-trader review.
ICICI's option history starts 5 Jan 2026 (about eight monthly cycles), which cannot show a condor's
worst months. NSE publishes every session's F&O closing prices in a public archive.

## 1. Decisions (the user's, 2026-10-09)

| Question | Answer |
|---|---|
| How far back | **From 1 Jan 2020**: the March 2020 crash, the 2022 bear market, June 2024's election day |
| 10:30 check | **Close-only**: every decision once a day, at the close, from that day's closing prices |
| Sizing across index levels | **Same notional as today**: each cycle's quantity scaled by today's NIFTY ÷ NIFTY at its first tranche |

## 2. What NSE serves (verified 2026-10-09 from a dev machine)

| Sessions | File | Notes |
|---|---|---|
| to 5 Jul 2024 | `content/historical/DERIVATIVES/{YYYY}/{MON}/fo{DD}{MON}{YYYY}bhav.csv.zip` | `INSTRUMENT, SYMBOL, EXPIRY_DT, STRIKE_PR, OPTION_TYP, OPEN, HIGH, LOW, CLOSE, SETTLE_PR, CONTRACTS, …`. On expiry day `SETTLE_PR` is the index, not the option |
| from 5 Jul 2024 | `content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip` (the app's existing feed) | UDiFF: `FinInstrmTp=IDO`, `ClsPric`, `TtlTradgVol`, `UndrlygPric`; `SttlmPric` is the index on expiry day |
| every session | `content/indices/ind_close_all_{DDMMYYYY}.csv` | "Nifty 50" open and close |

A contract that **did not trade** carries a stale close (so it is priced off that session's smile instead; see design-decisions #77) (seen: 3,038 on a strike whose intrinsic
value was 1,516). Only traded rows are stored; an untraded day leaves that contract unquoted, as a
missing live quote would. Files are 0.3–1.2 MB; NSE returns 404 on holidays, which is how
non-sessions are recognised (the app's holiday calendar does not reach back to 2020).

## 3. Storage

In the backtest cache (`backtest_store`), so it counts on the Storage screen with the rest:
`nse_daily_options` (session, expiry, strike, right, open, close, volume, OI) for NIFTY index
options **that traded**, with expiry within 100 days and strike within ±18% of the close;
`nse_daily_index` (session, open, close); `nse_daily_fetches` (session, status). Roughly 100 MB
for 2020 onward.

## 4. Replay

The same `CondorReplay` and `engine.decide`, through a `DailySource` price source:
- checks at the EOD check time only;
- expiries and sessions from the data (2020's NIFTY expired on Thursdays);
- each cycle's units scaled to today's notional, the per-lot delta band with it, so a 2% move
  means the same in every year;
- spread modelled as today's (older far strikes were wider; the notes say so).

Settings set in index points (minimum roll credit) stay in points, so they are relatively larger at
older, lower index levels; the notes say so. Entry DTE above about 95 cannot be replayed (expiries
beyond 100 days are not stored).

## 5. Fetch

Part of the backtest run, like ICICI fetches: outside market hours only, one request at a time with
a pause, resumable (a session fetched is never fetched again), stoppable. The first run from 2020
downloads about 1,650 sessions (~1.3 GB, about an hour: 2.4 s a session measured on 2020's first half); later runs fetch only new sessions.

## 6. UI

The condor's backtest dialog gains **Prices**: *ICICI intraday (from 5 Jan 2026)* or *NSE daily
closes (from 1 Jan 2020)*, with a "Since 2020" shortcut for the range. Results say which.
