# Read-only mode and your license

Every Breeze Modern deployment runs under a license issued through [breeze-ui.com](https://breeze-ui.com). Your server checks its license regularly. While the license is active, everything works. When it is not, the app switches to **read-only mode**: you can still see your account, but you cannot place trades.

## What the banner means

A banner across the top of every page shows the license state:

| Banner | License state | Trading |
|---|---|---|
| **Complete ICICI Direct login on this instance to start your 14-day trial.** (amber) | A new deployment waiting for its first ICICI login, which activates the trial. | Blocked until activation succeeds. It usually clears as soon as you sign in. |
| **License expired —** sign in at breeze-ui.com … to extend your license. (amber) | The license period has ended. | Still allowed for now: this is a warning. Extend the license at breeze-ui.com before it is revoked. |
| **Read-only mode — you cannot define strategies or execute trades.** … (red) | The license has been revoked. | Blocked. Follow the instructions for your license at breeze-ui.com. |
| **Read-only mode — this deployment has no valid license.** … (red) | No valid license is configured, or your server has not been able to confirm it recently. | Blocked. Obtain or configure a license at breeze-ui.com. |
| **Trial already used for this ICICI Direct User ID** … (red) | A free trial was already used for your ICICI account on another deployment. | Blocked. Contact sales for a paid license. |

Where the banner mentions breeze-ui.com, the name is a link. A **Contact Sales** link appears when your deployment has a sales contact configured.

## What still works

In read-only mode you can still:

- see the Dashboard, Portfolio, Performance, Order Book, Signals and Bots pages;
- change most settings and download logs and backtest results;
- **leave a trade or reduce risk**: cancel an open order, cancel a GTT exit, delete a parked order, arm or dismiss a Profit Booking / Stop Loss rule, and switch a bot off.

What is blocked:

- **Place Order, Basket Order and Strategy Builder**: the pages open, but clicking any button on them shows a read-only message instead of acting, so you cannot build or execute a trade there;
- placing and modifying orders anywhere else (Order Book modify, Portfolio square-offs, executing parked orders);
- bots opening new trades, and switching a bot on or changing its settings.

Nothing is ever sent to ICICI for a blocked action; the app shows why instead.

> [!NOTE]
> **Your exits keep working.** A Profit Booking / Stop Loss rule that is armed still places its exit orders in read-only mode, and a bot still closes a position it already holds. Read-only mode stops new trades; it never stops you leaving one.

## Why your server might say "no valid license" when you have one

Your server confirms its license with breeze-ui.com at regular intervals. If it cannot reach breeze-ui.com for long enough, it assumes the license is no longer valid and switches to read-only mode. It does this on purpose, so that a server cut off from licensing does not keep trading. The banner clears by itself once contact is restored. If it persists, check at breeze-ui.com that your deployment is running and its license is active, or contact support.
