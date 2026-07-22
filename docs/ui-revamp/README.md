# Handoff: Breeze Terminal Pro — UI Revamp

## ⭐ START HERE: DESIGN_LANGUAGE.md
Before writing a single line of UI code, read **`DESIGN_LANGUAGE.md`** in this folder. It is the
authoritative, verbose specification of the design language — exact fonts, the complete color-token
tables for both themes, a Tailwind mapping, the radius/spacing ladder, and copy-paste component
recipes for every button, input, card, badge, toggle, dropdown, and table. If a value isn't in this
README, it's in DESIGN_LANGUAGE.md. Do not improvise colors, fonts, or shapes — they are all defined
there exactly.

## Overview
This is a full visual + IA revamp of the Breeze Core Engine trading web app ("Breeze Modern" in the existing codebase) into a denser, terminal-style dark/light UI ("Terminal Pro"). It covers login, the main app shell/nav, dashboard, portfolio, performance, order book, place order, basket order, strategy builder, and a fully rebuilt Settings section (previously popup-dialog based, now a persistent in-page sub-navigation with 9 sub-screens, including a rebuilt API Playground and Reference Data Management screen that reflect the real backend catalog in the `testing` branch).

## About the Design Files
The `.dc.html` files in this bundle are **design references built as static/lightly-interactive HTML prototypes** — they show intended layout, color, typography, copy, and basic state changes (e.g. tab switches, dropdown open/close, theme toggle). They are **not production code to copy verbatim**. Your task is to **recreate these designs inside the existing breeze-core-engine frontend** (Next.js + React + TypeScript + Tailwind, per `frontend/src/components/layout/AppShell.tsx` and friends), reusing the app's existing data-fetching (`@tanstack/react-query`), routing (Next.js App Router), and component conventions — not by embedding this HTML directly.

Each `.dc.html` file opens standalone in a browser (double-click or serve statically) via the bundled `support.js` runtime — open them to click through real interactions (theme toggle, Settings sub-nav, API Playground method picker, WS subscribe-mode picker, etc.) before implementing.

## Fidelity
**High-fidelity.** Colors, spacing, type scale, and copy are final-intent. **See `DESIGN_LANGUAGE.md` for every exact value** — this section is a summary; that file is the contract. Recreate pixel-close using the codebase's existing Tailwind setup and design tokens where they already exist; where the app currently has no equivalent token (e.g. the terminal color palette below), introduce it following the codebase's existing token pattern (likely Tailwind theme extension or CSS variables, matching how `AppShell.tsx` currently uses `dark:` classes and `app-text-muted` utility classes).

## Global Shell (all screens)
- **Sidebar** (`width:236px`, fixed): logo (Breeze "B" mark, see Assets) + "Breeze / Terminal" wordmark, primary nav (Dashboard, Portfolio, Performance, Order Book, Place Order, Basket Order — badged "NEW", Strategy Builder, Settings), a session indicator pinned to the bottom ("ICICI Breeze · active" with a pulsing green dot).
- **Header** (52px tall): breadcrumb-style trading context (name, free margin), then on the right: ICICI daily API call counter (`1,284 / 5,000`), connection status dot, app version, theme toggle (sun/moon icon swap), log out icon button.
- **Main content**: `max-width:1280px`, centered, `padding:22px 24px 44px`.
- **Theme**: dark by default, persisted to `localStorage` (`breeze-tp-theme`), full light theme included via a `.theme-light` class swapping CSS custom properties (see Design Tokens).
- Typography: **IBM Plex Sans** for UI text, **IBM Plex Mono** for all numeric/code values (prices, quantities, method names, params, IDs).

## Screens / Views

### 1. Login
- Centered card, Breeze logo + wordmark, "Sign in with your ICICI user ID and app password" subhead, two labeled inputs (User ID, App Password), primary CTA button in accent color.

### 2. Dashboard
- KPI/summary row + market/positions widgets in the terminal shell. (See file for exact grid — unchanged from prior iteration except shell chrome.)

### 3. Portfolio
- Holdings/positions table-first layout with KPI strip above.

### 4. Performance
- Chart-and-stats screen for account performance over time.

### 5. Order Book
- Live/working orders table with status pills, cancel/modify row actions.

### 6. Place Order
- Single-order ticket: instrument search, buy/sell segmented control, order type, quantity/price fields, margin preview, submit.

### 7. Basket Order (NEW)
- Multi-leg order builder — add/remove legs, per-leg product/qty/price, aggregate margin summary.

### 8. Strategy Builder
- Option-strategy construction surface (legs, payoff, PoP/margin/loss estimates) feeding the Strategy Builder Audit Logs in Settings.

### 9. Settings (rebuilt this round — most detail below)
Persistent **left sub-nav** (252px, sticky) inside the main content area — not a separate route shell, not modals. Groups, in order:
- **(ungrouped)**: Broker Credentials, Quantity Limits, API Usage
- **Automation**: GenAI Settings, Reference Data Loads, Exchange Calendar
- **Diagnostics**: Audit Logs (Strategy Builder Audit Logs)
- **Danger zone** (red-tinted section label, red hover/active states): API Playground, Delete Account

Clicking a nav item swaps the right-hand content pane in place (client-side state, no navigation/route change, no modal/dialog). Active item gets a left accent bar + tinted background; danger items get a red bar instead.

#### 9.1 Broker Credentials
Read-only ICICI User ID field (dashed border, disabled), editable API Key + Secret Fragment (password-masked) fields, Save button.

#### 9.2 Quantity Limits
Explains the source of freeze-quantity data (BSE `Abridged_CO.ZIP`, NSE `NSE_FO_contract_ddmmyyyy.csv.gz`) with links, then an editable table: Symbol / Exchange / Segment / Qty limit (per-row numeric input), "Save all" button in the header.

#### 9.3 API Usage
Day-range selector (7/30/60/90), a rate-limit backoff explainer card with an editable seconds value + Save, a segmented control (API-wise / Route-wise), and a per-day breakdown card (order placement / market quotes / portfolio calls).

#### 9.4 GenAI Settings
BYOK provider cards (Gemini — configured, shows model list with Primary/Fallback tags and per-model health; OpenAI — not configured, "Add key" CTA). Below: Market Feeds list (RSS URLs) and a Prompts section with editable System Prompt + Prompt Template textareas (supports `{scope}`, `{symbol}`, `{sources_json}`, `{required_schema_json}` placeholders) + Save.

#### 9.5 Reference Data Loads (rebuilt — previously missing/merged incorrectly)
This screen **replaces** the old separate "Margin Calculation Source" and "Scrip Master" settings — they are now sections within this one screen:
- Daily schedule toggle + IST time input ("Load now" + "Save schedule" actions).
- **Margin calculation source** toggle: off = Breeze API (default), on = Exchange Risk Baseline (SPAN) for Strategy Builder margins, with an explainer that ICICI margins may differ and unmatched contracts fall back to Breeze API.
- **Source status** list: NSE FO BhavCopy, BSE FO BhavCopy, ICICI Scrip Master, NSE SPAN Baseline, BSE SPAN Baseline — each with a last-refreshed/data-date line.
- **BSE SPAN Baseline** manual-upload card (amber-tinted): explains BSE doesn't publish a direct archive URL like NSE, links to the BSE Risk Parameter report, restricts ingestion to `BSXOPT`/`BKXOPT` portfolios, shows last-loaded filename + row count, file input + Upload button.
- **Ingest history** table: Source / File date / Rows / Status / Ingested-at, with a "Show all history (N)" link.

#### 9.6 Exchange Calendar
Regular session open/close time inputs (HH/MM), an editable holiday list (date + name, add/remove rows), Save, plus a "Sync from Breeze Console" card to pull an operator-maintained calendar from breeze-ui.com Console.

#### 9.7 Strategy Builder Audit Logs
Explains up to 10 recent propose-trades audits are stored server-side, Levels 1–3 = human-readable explainability, Level 4 = full technical JSON download. Table: Finished (timestamp) / Scrip / Strategy / PoP / Margin / Loss / Session / Transparency (L1–L4 badge links). "Download all as ZIP" button in the header.

#### 9.8 API Playground (rebuilt — was previously a static/incomplete mock)
Reflects the **real Breeze Connect method catalog** from the backend (`backend/src/icici_breeze_backend/app/domain/breeze_api_tester_catalog.py` in the `testing` branch) — 33 methods across 4 risk tiers:
- **Read-only** (green): get_customer_details, get_demat_holdings, get_funds, get_historical_data(+v2), get_margin, get_names, get_option_chain_quotes, get_order_detail, get_order_list, get_portfolio_holdings, get_portfolio_positions, get_quotes, get_trade_detail, get_trade_list, gtt_order_book, limit_calculator, margin_calculator, preview_order, ws_connect, ws_disconnect, subscribe_feeds.
- **Funds** (amber): set_funds.
- **Trade (orders)** (red): place_order, modify_order, cancel_order, square_off.
- **GTT** (purple): gtt_three_leg_place_order/modify_order/cancel_order, gtt_single_leg_place_order/modify_order/cancel_order.

**Method picker** is a custom combobox (not a native `<select>`):
- Closed trigger: two stacked rows — row 1 = method title (ellipsis-truncated) + chevron; row 2 = mono method name (ellipsis-truncated) + risk-tier badge (READ/FUNDS/TRADE/GTT, colored per tier).
- Open panel: search input ("Search APIs...") filtering by title or method name, grouped list under sticky-style uppercase section headers, each row = title + mono method name (left) and short colored risk badge (right); click a row or click-outside to close.
- Below the picker: full risk-tier label + method name, method description (if any), method-specific usage notes (amber box, e.g. interval formats, market-order caveats), and — for Trade/Funds/GTT methods — a red "can modify live orders/funds/GTT triggers" warning.
- **Parameters panel**: per-method param list generated from the catalog (label, required marker, placeholder, help text, JSON-array textarea vs plain text input as appropriate), a standing amber note about Python/JSON literal parsing for list-typed values (e.g. multi-token subscriptions), "Fire API" button (red, since most playground calls are live).
- **Response panel**: mono `<pre>` output area + Copy button.
- A standing danger banner at the top of the whole screen lists the real consequences (unintended live orders, fund moves, GTT triggers, quota consumption).

**WebSocket section** (separate card below):
- Status line reflecting connect/subscribe/disconnect state (color-coded).
- Connect / Release subscriptions / Disconnect socket / Start tick stream actions, with copy explaining to prefer "Release subscriptions" over full disconnect to avoid ICICI connection-thrashing penalties.
- **Subscribe mode** is also a custom dropdown (not native `<select>`) — trigger + chevron, panel with a checkmark next to the selected row (accent-tinted highlight), 6 modes: F&O exchange quotes, F&O OHLCV (interval), Cash/index exchange quotes, Stock token exchange quotes, Stock token OHLCV, Order notifications.
- Field set shown below the mode picker changes per mode (e.g. F&O quotes needs exchange_code/stock_code/expiry_date/strike_price/right/product_type/get_market_depth/get_exchange_quotes; token modes just need stock_token(+interval); order notifications just needs get_order_notification) — only filled fields should be sent to the backend.
- ICICI command log, Latest operation (JSON), and Ticks panels — all mono `<pre>` blocks that wrap long content (`white-space:pre-wrap; word-break:break-word`) rather than overflowing the layout.

#### 9.9 Delete Account
Danger-tinted card: explains this only removes the Breeze Modern account + stored broker credentials (not the ICICI account, not AWS resources), requires re-entering ICICI User ID + app password, red "Delete account" button.

## Interactions & Behavior
- **Settings nav**: client-side state swap of the right pane; no route change, no dialogs/modals (this was an explicit requirement — the prior implementation used popups).
- **Theme toggle**: swaps `.theme-light` class on the root, persists choice to `localStorage` under `breeze-tp-theme`, re-reads on mount.
- **API Playground method picker**: click trigger → opens panel with search focused conceptually; typing filters grouped rows live; selecting a row sets the method, resets param values, closes the panel; clicking the fixed full-screen backdrop behind the panel also closes it.
- **WS subscribe-mode picker**: same open/close pattern as the method picker, without search — just a grouped/flat list with a check icon on the active row.
- **WS action buttons** (Connect/Release/Disconnect/Start stream/Subscribe) are currently wired to canned demo responses in the prototype (see `wsConnect`, `wsRelease`, `wsDisconnect`, `wsSubscribe`, `wsStartStream` handlers in the logic class) — replace with real API calls to the backend's WS bridge.
- **Exchange Calendar** holiday rows are addable/removable client-side.
- Long text blocks (playground response, WS log/latest-op/ticks) must wrap (`white-space:pre-wrap; word-break:break-word`) and grid parents holding them need `min-width:0` — without this the layout overflows horizontally (a bug caught and fixed during this round).

## State Management
Minimum state needed per screen, based on the prototype's logic class:
- **Shell**: `theme` ('dark' | 'light'), persisted.
- **Settings**: `screen` (active sub-screen key), `marginSourceOn` (bool — Breeze API vs SPAN), `scheduleOn` (bool), `holidays` (array of {date, name}).
- **API Playground**: `selectedMethod` (string), `paramValues` (map of param name → string), `methodOpen`/`methodSearch` (combobox UI state), `wsMode` (string), `wsForm` (map of field key → string), `wsModeOpen` (dropdown UI state), plus WS status/log/latest-op/ticks display state driven by whatever the real WS bridge returns.
- Data requirements: the method catalog (title, method id, risk tier, description, notes, param defs) should come from the backend (already exists as `breeze_api_tester_catalog.py`) rather than being hardcoded in the frontend, so the two stay in sync.

## Design Tokens

### Dark (default)
- `--bg:#0A0C10` `--panel:#12151C` `--panel2:#0F141C` `--elevated:#161B24`
- `--border:#232A36` `--border-soft:#1B222E`
- `--text:#E6EAF2` `--muted:#8A93A6` `--faint:#5C6577`
- `--accent:#22D3EE` `--accent-strong:#22D3EE` `--accent-ink:#06222A` `--accent-tint:#0C2229`
- `--up:#34D399` `--down:#F87171` `--up-tint:#0F1F1B` `--down-tint:#231518`
- `--up-btn:#0EA371` `--down-btn:#E5484D`
- `--amber:#FBBF24` `--amber-tint:#241D0E`
- `--gtt:#A78BFA` `--gtt-tint:#221E33` (used for the GTT risk-tier badge)
- `--shadow:0 18px 50px -20px rgba(0,0,0,.65)`

### Light
- `--bg:#EEF1F5` `--panel:#FFFFFF` `--panel2:#F5F7FA` `--elevated:#FFFFFF`
- `--border:#DBE1E9` `--border-soft:#E8ECF2`
- `--text:#0E1520` `--muted:#5A6473` `--faint:#93A0B0`
- `--accent:#0891B2` `--accent-strong:#0E7490` `--accent-ink:#FFFFFF` `--accent-tint:#E1F4FA`
- `--up:#0F9D6B` `--down:#DC2F44` `--up-tint:#E7F6F0` `--down-tint:#FCECEE`
- `--amber:#B45309` `--amber-tint:#FBF0DE`
- `--gtt:#7C3AED` `--gtt-tint:#F1EAFE`
- `--shadow:0 10px 34px -16px rgba(15,25,40,.20)`

### Typography
- UI: `IBM Plex Sans` (400/500/600/700)
- Numeric/code/mono: `IBM Plex Mono` (400/500/600/700)
- Base body size 14px; section titles 18px/700; screen icon chips 38×38px, 10px radius.

### Shape
- Cards/sections: `border-radius:13px`, `1px solid var(--border)`, `padding:20px`
- Inputs: `border-radius:7–9px`, `height:32–40px`
- Pills/badges: `border-radius:999px`
- Sidebar width: 236px · Settings sub-nav width: 252px · Content max-width: 1280px

## Assets
- **Logo**: `assets/breeze-logo.png` — the actual Breeze "B" mark, sourced directly from the breeze-core-engine repo (`frontend/src/app/android-chrome-192x192.png` on the `testing` branch). Used at 34×34px in all sidebars, 44×44px on the login screen. Do not redraw or substitute this — it's the real brand asset already in the codebase.
- No other custom icons/images — all other iconography is inline SVG (Lucide-style stroke icons, 1.9–2.4px stroke width).

## Screenshots
`screenshots/` contains a PNG per screen in **light theme** (the default in these prototypes' shell for print/handoff clarity), and `screenshots/dark/` has the same set in **dark theme** (the app's default). Filenames match: `login.png`, `dashboard.png`, `portfolio.png`, `performance.png`, `order-book.png`, `place-order.png`, `basket-order.png`, `strategy-builder.png`, and `settings-01-broker-credentials.png` through `settings-09-delete-account.png` (in Settings nav order). These are cropped to the visible viewport — some longer screens (Quantity Limits, Reference Data Loads, Exchange Calendar, Audit Logs) scroll further than the screenshot shows; open the corresponding `.dc.html` file to see the full screen and toggle themes yourself (moon/sun icon, top right).

## Files
All files are in this handoff folder:
- `Breeze Login - Terminal Pro.dc.html`
- `Breeze Dashboard - Terminal Pro.dc.html`
- `Breeze Portfolio - Terminal Pro.dc.html`
- `Breeze Performance - Terminal Pro.dc.html`
- `Breeze Order Book - Terminal Pro.dc.html`
- `Breeze Place Order - Terminal Pro.dc.html`
- `Breeze Basket Order - Terminal Pro.dc.html`
- `Breeze Strategy Builder - Terminal Pro.dc.html`
- `Breeze Settings - Terminal Pro.dc.html` ← most detailed / most recently revised; start here for the Settings IA and the API Playground rebuild.
- `support.js` — runtime required to open the `.dc.html` files standalone in a browser; not needed for the real app.
- `assets/breeze-logo.png` — brand mark, see Assets above.
- `screenshots/` — PNG reference for every screen, see Screenshots above.

Reference `backend/src/icici_breeze_backend/app/domain/breeze_api_tester_catalog.py` and the WebSocket subscribe-mode source (`frontend/src/lib/breeze-api-playground-ws-subscribe.ts`) on the `testing` branch of `breeze-core-engine` for the authoritative API/WS catalogs the Settings → API Playground screen is derived from.
