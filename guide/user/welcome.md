# Welcome to Breeze Modern

Breeze Modern is a browser-based trading dashboard for **ICICI Direct**. It connects to your ICICI account through ICICI's official **Breeze API** and gives you one place to watch your F&O positions, place orders, build and execute option strategies, arm automated exits, and run rule-based bots.

![The Dashboard, the first page you see after signing in](images/dark/dashboard-overview.png)

## What Breeze Modern does

- **Shows your account live.** Open positions, holdings, margin and P&L, updated from ICICI's streaming quotes during market hours and from the exchanges' closing prices after the close.
- **Places orders for you.** Single orders, multi-leg baskets, and whole strategies, with large orders split automatically to stay within exchange freeze limits.
- **Proposes option strategies.** Strategy Builder suggests trades that fit your outlook, margin and minimum probability of profit, and prices each one's margin and payoff before you commit.
- **Protects open positions.** You can arm Profit Booking / Stop Loss rules on a group of positions, and the app places the exit orders when a threshold is crossed.
- **Automates routine trades.** Five bots cover covered-call writing, expiry-day premium selling, intraday scalping, iron flies and the closing auction. Each one can run by hand, with your approval on Telegram, or on its own.
- **Lets you check ideas against history.** Signals and bots can be backtested on ICICI's own historical data before you rely on them.

## What it does not do

- **It does not replace ICICI's own apps.** Breeze Modern covers the features listed above. For everything else (fund transfers, IPOs, equity delivery orders, account statements) use ICICI Direct's website or app.
- **It does not give investment advice.** Strategy proposals, signals and bots are tools. The decisions, and the risk, are yours.
- **It does not run in the cloud for everyone.** Your copy of Breeze Modern runs on **your own server**, in your own AWS account. Your ICICI credentials and your trades never pass through anyone else's systems.

## Your own server

Each customer runs their own copy of the app. That has a few practical consequences you will meet in this guide:

- **ICICI only accepts calls from your server's registered IP address.** You register that address with ICICI once, when you set up your API app. See [Before you begin](before-you-begin.md).
- **Your data stays on your server**, including your encrypted ICICI credentials, parked orders, bot settings, backtest history and logs.
- **Automated features only work while your server is running.** Profit Booking / Stop Loss rules and bots are watched by your server, not by ICICI. If the server is stopped, nothing is watching.
- **Your deployment is licensed.** A license issued through [breeze-ui.com](https://breeze-ui.com) keeps trading enabled. If it lapses, the app switches to read-only mode. See [Read-only mode and your license](read-only-mode.md).

## How this guide is organised

| Part | What it covers |
|---|---|
| **Getting started** | Setting up your ICICI API app, registering, signing in, and the parts of the screen you see on every page. |
| **The pages** | One section per page of the app, in the order they appear in the sidebar. Each section goes through every card, button and setting on that page. |
| **Topics** | Subjects that span several pages: margins, backtests, ICICI's API limits, read-only mode, troubleshooting and a glossary. |

## Conventions used in this guide

- **Bold text** is the exact label you see on screen, for example **Place order** or **Settings → Trading Costs**.
- **→** shows a path through menus: **Settings → Reference Data Loads** means open Settings, then choose Reference Data Loads.
- Times are **Indian Standard Time (IST)**, and amounts are in rupees, written the way the app shows them: **₹1.2L** is ₹1.2 lakh, **₹3.4Cr** is ₹3.4 crore, **₹25K** is ₹25,000.
- Screenshots were taken with sample data. Your numbers, scrips and dates will differ. They switch between the dark and light look when you change the app's theme.

> [!TIP]
> Press **?** on any page of the app to open the built-in Help. Its topics link back to the matching part of this guide.
