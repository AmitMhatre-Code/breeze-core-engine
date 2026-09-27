# Troubleshooting and FAQ

## Signing in

**ICICI rejects the sign-in, or I come back with an error instead of the Dashboard.**
Usually one of three things:
1. The memorised part of your Secret Key is wrong, or the stored part was mistyped when you registered. The two must join into your exact Secret Key. Fix the stored part with **Update credentials** on the login page ([Passwords and credentials](account-recovery.md#your-icici-api-key-or-secret-changed)).
2. ICICI gave you a new Secret Key (for example you regenerated it). Update it the same way.
3. The redirect URL or IP address on your ICICI API app does not match your server. See [Before you begin](before-you-begin.md#if-your-ip-address-or-redirect-url-is-wrong).

**The Complete ICICI login page says "Missing session token in URL".**
The page was opened directly instead of by ICICI's redirect. Start again from the login page.

**I have to sign in again every morning.**
That is ICICI's rule: API sessions end at midnight IST. Your Breeze Modern login may last longer, but ICICI needs a fresh login each trading day.

**I see the risk disclosure every time I sign in.**
It is shown after every ICICI login, by design. Scroll to the bottom to enable **Proceed**.

## Prices and data

**Prices are not moving.**
Check the **Session** dot at the bottom of the sidebar. Grey with **Market closed** is normal outside market hours; prices then come from the exchange's closing file (**Bhavcopy** badge). Red means the live stream has a problem; the text says what. If it stays red for more than a minute or two during market hours, sign out and in again.

**A price shows "· prev close" on Portfolio.**
No live tick has arrived for that contract yet, so the previous close is shown. It is replaced as soon as a live price arrives.

**The Bhavcopy badge is red.**
The market has opened since that closing file's date, so its prices are out of date. Live prices take over once the stream is running; if they do not, check **Settings → Reference Data Loads**.

**Margins or option chains look wrong after the close.**
Check **Settings → Reference Data Loads → Source status** for today's files. Click **Load now** if the daily load failed. See [Reference Data Loads](settings-automation.md#reference-data-loads).

**My margin differs from ICICI's.**
See [Why figures can differ from ICICI's screens](margins.md#why-figures-can-differ-from-icicis-screens).

## Orders

**The Confirm button is disabled and says the market is closed.**
Orders can only be parked while the market is closed. Park it, then execute it from the [Order Book](order-book.md#parked-execution) after the open. If the market is open and the app says closed, check the [Exchange Calendar](settings-automation.md#exchange-calendar).

**My order was split into several orders.**
Your quantity is above the exchange's freeze limit for that contract. See [Order chunking](place-order.md#order-chunking).

**A "Broker rate limit" countdown covered the screen.**
ICICI asked the app to slow down. Let the countdown finish; the app carries on by itself. See [ICICI API limits](api-limits.md).

**Clicking any button on Place Order shows a read-only message.**
Your deployment's license is not active. See [Read-only mode and your license](read-only-mode.md).

**A market order was rejected.**
ICICI has not yet enabled market orders through the API. Use the aggressive **Limit + tol** style instead. See [Aggressive orders](place-order.md#aggressive-orders).

## Automated exits and bots

**My Profit Booking / Stop Loss rule did not fire.**
Rules are watched by your server. They only fire while it is running, streaming live prices, signed in to ICICI for the day and licensed. Logging out ends the ICICI session and stops every rule. See the warning in [Profit Booking / Stop Loss](portfolio.md#profit-booking--stop-loss).

**A rule shows "Reset · action needed" and a red banner appeared.**
An exit order is still working for a leg that is already closed; if it fills it opens a new position. Open the rule and click **Cancel remaining exit orders**. See [When a rule resets](portfolio.md#when-a-rule-resets).

**A bot is armed but never trades.**
Check its Activity rows: every skip has a reason. Common ones are no ICICI session, a signal that has not passed the [30-day gate](signals.md#the-30-day-gate), not enough margin for one lot, outside its trading window, or read-only mode.

**I cannot switch a scalper to Live.**
It needs at least one completed Simulation day on its exact current settings, and its signal must be available to bots. The **Enable live trading?** dialog shows what is missing.

**Semi-auto is greyed out.**
Semi-auto asks for approval on Telegram, so it needs a linked chat. See [Telegram Alerts](settings-automation.md#telegram-alerts).

## Backtests

**My backtest reports missing days.**
History is downloaded only outside market hours and within the daily backtest budget. Run it again another evening and it continues where it left off. See [Backtests](backtests.md#where-the-history-comes-from).

**A backtest will not start.**
Only one backtest runs at a time, and backtests that download data are refused when storage is past your threshold. See [Storage](settings-diagnostics.md#storage).

## Your server

**A "Storage is N% full" banner appeared.**
Open **Settings → Storage** and delete old backtest history or logs for a date range. See [Storage](settings-diagnostics.md#storage).

**Something is broken and I need to report it.**
Download **Settings → Application Logs** for the period when it happened and send them with a description. Error messages in the app often include a reference ID; include that too. The logs contain account identifiers and IP addresses, so share them only with support.

**I want to know how Breeze Modern works under the hood.**
Ask us for the **technical overview**: a short description of how the app is built, what runs on your server and what information leaves it. Write to [sales@breeze-ui.com](mailto:sales@breeze-ui.com).
