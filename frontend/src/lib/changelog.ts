import { parseAppVersion } from "./app-version";

export type ReleaseKind = "major" | "minor" | "patch" | "prerelease";

export type ChangelogRelease = {
  version: string;
  /** ISO date (YYYY-MM-DD) for sorting/display */
  date: string;
  /**
   * Semver-aligned release label:
   * `major` — major version bump; `minor` — minor bump (x.Y.0);
   * `patch` — patch bump; `prerelease` — suffix build (e.g. 1.4.2-a).
   */
  releaseKind: ReleaseKind;
  /** Optional one-line headline for the release */
  summary?: string;
  changes: string[];
};

/** Newest first. Prepend a new entry when you ship; keep `version` in line with `package.json` when you bump it. */
export const changelogReleases: ChangelogRelease[] = [
  {
    version: "2.7.1",
    date: "06-Aug-2026",
    releaseKind: "patch",
    summary:
      "The downloadable log bundle no longer contains your broker session key, live prices recover on their own when the feed drops, and logs now reach much further back.",
    changes: [
      "Fixed the Application Logs download (new in 2.7.0) writing your ICICI session key into the log. The key is part of the address ICICI redirects you to after login, and the routine per-request log line recorded that address in full — so it ended up in any bundle covering the day you logged in, and would have let anyone holding that bundle use your broker session until it expired that night. It is now replaced with \"<redacted>\" before anything is written to disk, as is any other session key, token, API key, password or checksum passed the same way. Bundles you downloaded before this release should be treated as sensitive and deleted; the key itself expires nightly, so nothing else is needed once it has.",
      "Fixed live prices freezing mid-session with no error shown. If the connection carrying ICICI's price feed dropped while leaving the app believing it was still connected, every attempt to restart the feed was refused and the app kept retrying an approach that could not work — prices across every screen sat unchanged until the connection happened to recover on its own, which on 6 August took about three minutes during market hours. The app now recognises that state and rebuilds the connection outright, restoring each option chain it was following along with the index prices.",
      "Routine background chatter no longer crowds real events out of the log. Successful screen-refresh requests, and a once-a-minute internal check that only ever confirms nothing has changed, are no longer recorded — while failures of those very same requests still are, so an expired session or a broker error stays visible. On a full trading day's traffic this cuts the log to roughly a seventh of its former size, so a downloaded bundle covers far more history: over seven weeks instead of about nine days.",
      "The reference number shown alongside an error message is now recorded as a searchable field in the log, so quoting it when you report a problem points straight at the failure it came from.",
    ],
  },
  {
    version: "2.7.0",
    date: "05-Aug-2026",
    releaseKind: "minor",
    summary:
      "Download application logs from Settings, Telegram linking actually completes, and far-dated expiries stop hanging on \"building live chain\".",
    changes: [
      "New Settings → Application Logs screen: download a zip of this deployment's logs for the last 1, 3, 7, 14 or 30 days, to check what the app was doing or to attach when reporting a problem. Passwords, session tokens and API keys are stripped before anything is written to disk, so the bundle is safe to share.",
      "Logging itself is more useful and much less noisy: routine per-request lines no longer drown the log, but when something does fail the app replays the minutes of activity leading up to the error, so the context around a failure survives even at the quieter production setting.",
      "Fixed Telegram account linking failing for everyone: because every deployment shares one bot, only whichever instance won a race could see your \"/start\" handshake — so scanning the QR code in Settings → Telegram Alerts typically did nothing at all, and could in principle have been answered by someone else's deployment. Linking is now routed to the correct deployment centrally. Alerts to accounts that were already linked were never affected.",
      "Option chains for far-dated and thinly traded expiries (BSE Sensex monthlies, single-stock options) no longer sit permanently on \"building live chain…\": a chain is now judged ready on the strikes near the money rather than on deep wings that may not trade at all in a session, and a request falls back to end-of-day prices after a few seconds instead of waiting on quotes that will never arrive.",
      "Fixed Modify being rejected by the exchange on a partially filled leg: the app was re-sending the order's original quantity rather than the part still open, so once filled + open crossed the exchange's per-order freeze limit ICICI refused the change with a \"maximum qty per order\" error. It now sends the remaining open quantity.",
    ],
  },
  {
    version: "2.6.0",
    date: "05-Aug-2026",
    releaseKind: "minor",
    summary:
      "Strategy Builder flags stale results after you change inputs, legs get inline strike editing, and quote badges show when a chain is still building instead of looking frozen.",
    changes: [
      "Strategy Builder now tells you when your results no longer match what's in the form: changing margin, max loss, min PoP, min return, or the ELM checkbox after generating dims the current results and relabels the button \"Regenerate …\", so you don't act on strategies built from parameters you've since changed.",
      "Basket Order and Strategy Builder legs now have an inline strike selector in the legs table instead of static text — click a leg's strike to change it directly, without reopening the option chain. Strategy Builder's legs table gains this for the first time; Basket Order's Strike column header is now also clickable to sort ascending/descending, and a leg added via \"Add leg\" starts with a blank strike (instead of silently defaulting to ATM) and stays pinned to the bottom until you pick one.",
      "Pressing Tab out of a scrip, expiry, or strike dropdown (Place Order, Basket Order, Strategy Builder) now selects the highlighted option and moves on to the next field, instead of just closing the dropdown and leaving whatever you'd typed.",
      "Fixed a leg pricing bug in Basket Order and Strategy Builder: once you'd manually typed a price for a leg, changing that leg's strike or Call/Put afterwards silently kept the old, now-wrong price instead of refreshing it from the live chain. Price now always follows strike/right changes unless the leg is set to aggressive pricing.",
      "Net premium now shows green for a net credit, matching the red already used for a net debit, in both the Basket Order and Strategy Builder legs panels — a positive net premium previously rendered in the same neutral colour as zero.",
      "Place Order, Basket Order, and Strategy Builder now show an animated \"building live chain…\" badge while a chain has fallen back to end-of-day prices and is being rebuilt from the WebSocket feed in the background, instead of looking stuck on stale prices with no explanation. After about a minute without success it settles into a static message with a manual refresh button.",
      "Reduced ICICI API usage from routine screen refreshes: position and margin data used by the navbar, Dashboard, Portfolio, and the Profit-Booking/Stop-Loss background monitor is now shared for a few seconds instead of every screen independently re-querying the broker for data someone else just fetched.",
      "Strategy generation's final margin/return refresh now yields to the order-placement budget reserved in 2.5.0 instead of competing with it: under tight ICICI capacity, results still display — just without an updated SPAN margin / annualized-return figure for that batch — rather than spending calls meant to stay free for placing and cancelling orders.",
    ],
  },
  {
    version: "2.5.0",
    date: "03-Aug-2026",
    releaseKind: "minor",
    summary:
      "Profit Booking / Stop Loss keeps monitoring after a restart, retries exit orders through ICICI throttles, and stops a leftover Reset rule from eating your daily API quota.",
    changes: [
      "Fixed a serious gap where Profit Booking / Stop Loss silently stopped evaluating after the app restarted (including during an automatic version upgrade). The rules still showed as Armed and the price feed stayed live, but nothing was being measured until someone opened the Portfolio page — so a breach could pass without any exit order being placed. Positions are now reloaded on startup and refreshed every minute, independently of any browser.",
      "If the app can't reach your broker session while rules are armed, it now says so on Telegram instead of failing quietly — repeating every 30 minutes during market hours until you log back in, then confirming once monitoring has resumed. Protection is deliberately left off rather than resuming against a position that may have changed while the app was down.",
      "Fixed a bug where a single Reset Profit Booking / Stop Loss rule left sitting on the Orders page could exhaust your entire 5,000-call daily ICICI API limit in about an hour, by re-reading the order book on every 2-second refresh. Rules left over from a previous day now cost nothing at all, and the order book is read once and shared instead of once per rule.",
      "The app now paces itself against ICICI's per-minute request limit rather than only reacting after being throttled, and reserves the last 500 calls of your daily quota for placing and cancelling orders — so non-essential screen refreshes can no longer starve your ability to trade.",
      "Exit orders now retry for about 50 seconds when ICICI rate-limits them, instead of giving up after ~4 seconds and leaving a position half unwound. Only throttles are retried — a rejection or a timeout is never re-sent, so an order can't be duplicated.",
      "You now get a Telegram message the moment a retry starts, not just when it ends. If you place the order yourself in the meantime, the app detects it and stops retrying, so you won't end up with a duplicate — and the message says so.",
      "The alert for a failed exit leg no longer just says \"check the app\". It now warns that any leg which did fill has already changed your position, so you review before placing anything manually.",
      "A Profit Booking / Stop Loss rule now Resets correctly when you close the entire strategy group yourself elsewhere. Previously it stayed Armed indefinitely, which also blocked you from arming a new rule on that group.",
    ],
  },
  {
    version: "2.4.0",
    date: "31-Jul-2026",
    releaseKind: "minor",
    summary:
      "Order Modify and Cancel now show live per-order progress, matching the placement confirmation modal.",
    changes: [
      "The Cancel confirmation dialog now shows a progress bar and \"Cancelling order X of N\" status while multiple orders are cancelled one by one, instead of a single static \"Cancelling…\" spinner.",
      "The Modify dialog now shows a progress bar and \"Modifying order X of N\" status as a leg's underlying orders are cancelled, resized, or newly placed to reach the requested quantity/price — this required moving leg modification from one opaque server-side call to a step-by-step flow the app can report progress on as it happens.",
    ],
  },
  {
    version: "2.3.1",
    date: "28-Jul-2026",
    releaseKind: "patch",
    summary:
      "Margin figures now tally across the navbar, Dashboard and Portfolio — including a fix for Margin used always showing ₹0.",
    changes: [
      "Fixed Dashboard \"Margin used\" always showing ₹0: it was computed the wrong way round and clamped to zero whenever margin was actually blocked. It now shows the capital ICICI has blocked against your positions.",
      "Free margin stays ICICI's own available balance, and the ELM buffer is shown as a separate line beneath it (\"− ELM … after buffer\") instead of being quietly subtracted — so the number on the Dashboard matches what ICICI tells you is available to trade.",
      "Portfolio \"Span + ELM margin\" now breaks the figure down into what's actually blocked plus the ELM buffer, so you can see which part of it is our conservative overlay rather than broker-blocked capital.",
      "Margin and capital figures are formatted consistently everywhere (navbar, Dashboard, Portfolio), so the same amount reads the same on every screen.",
      "Added an info tooltip on the margin tiles explaining that ICICI's margin API doesn't always report the amount upstreamed to the exchange, which is why blocked-margin figures can occasionally differ slightly between screens or from ICICI's own portal.",
    ],
  },
  {
    version: "2.3.0",
    date: "27-Jul-2026",
    releaseKind: "minor",
    summary:
      "Quick-select your most-traded scrips, and see live progress while a large order is placed.",
    changes: [
      "Place Order, Basket Order, and Strategy Builder now show your most commonly traded underlyings as one-click pills right under the scrip search (up to 5, or 3 in the compact ticker layout) — computed once from your last 30 days of trades when you log in and reused for the rest of the session, so you don't have to retype the same few symbols every time.",
      "The order confirmation modal now shows real progress while a large order is split into exchange-freeze-limited chunks and placed: a progress bar, a live \"chunk X of Y · qty placed\" status line, and a per-leg status marker on each row, instead of a single static \"Placing…\" spinner that looked frozen while the app was actually working through the chunks.",
    ],
  },
  {
    version: "2.2.1",
    date: "26-Jul-2026",
    releaseKind: "patch",
    summary: "Scale to margin/premium can now shrink an oversized basket, not just grow it.",
    changes: [
      "Scale to margin/premium now scales down as well as up: if a single basket already exceeds your target, the app reduces every leg together to the largest size that fits, instead of refusing with an \"exceeds your target\" message. Scaling snaps to the strategy's smallest whole-number leg ratio, so a ratio spread keeps its shape exactly and the target is met as closely as possible in both directions.",
    ],
  },
  {
    version: "2.2.0",
    date: "26-Jul-2026",
    releaseKind: "minor",
    summary:
      "Aggressive orders are now enabled — fill via a Market or LTP-tolerance limit — and baskets can be scaled to a target margin or premium.",
    changes: [
      "Aggressive orders are now switched on for everyone. Tap the ⚡ toggle to fill a leg quickly instead of resting at a price you type, and choose how it executes, per order: \"Limit + tolerance\" places an ordinary limit priced a set percentage past the last traded price — above LTP to buy, below to sell — so it fills fast but never worse than your tolerance, and works today with no dependency on ICICI. \"Market\" sends a native ICICI market order (which ICICI may still reject until they enable native market orders). The price is derived live at the moment you confirm.",
      "Set your preferred aggressive mode and default tolerance % once in Settings; every order form seeds from it and you can still override either value for an individual order. Aggressive fills are now available consistently across Place Order, Strategy Builder, Basket Order, and portfolio square-off.",
      "Scale a whole basket at once: on the Basket Order page you can grow or shrink every leg together to hit a target deployed margin (using the broker's netted SPAN + ELM) or a target net premium debit, instead of adjusting lots leg by leg. It warns you when a basket can't be scaled the way you picked — for example a net-credit basket in premium mode or a basket with no short leg in margin mode — and flags when the premium shown is an estimate.",
    ],
  },
  {
    version: "2.1.2",
    date: "24-Jul-2026",
    releaseKind: "patch",
    summary: "Fixed live option chain prices freezing mid-session.",
    changes: [
      "Live option chain prices no longer freeze mid-session: a momentary drop in the broker's market-data connection could leave option chains stuck on stale prices for the rest of the day — while index quotes kept updating, which made the feed look healthy — and only a restart recovered it. The app now detects and clears that state on its own, and reports a genuine error when the broker refuses a price subscription instead of treating it as success.",
    ],
  },
  {
    version: "2.1.1",
    date: "23-Jul-2026",
    releaseKind: "patch",
    summary:
      "Corrected Dashboard Day's P&L, netted portfolio margin, after-hours option quotes, and clearer strategy/automation wording.",
    changes: [
      "Dashboard Day's P&L is rebuilt and now correct: it marks your open positions against the previous trading day's close and adds any realized profit from intraday round-trips, so the tile no longer swings to nonsensical figures (it now separates Realized and Open, gross of brokerage and taxes).",
      "Portfolio margin is now netted, not summed per leg: Total Margin and Carry Return reflect the broker's netted SPAN + ELM for each option group and for the portfolio as a whole, instead of adding up each leg and overstating the margin.",
      "Option chains and quotes now load reliably after market hours (and on a brief live-feed miss): the app falls back through captured live snapshots, Bhavcopy, and the broker API, fixing spurious \"Bhavcopy not loaded yet\" errors when data was actually available.",
      "Clearer wording in Strategy Builder: near-threshold strategies are described as the \"closest matches\" rather than \"best available/recommended\", and a new help topic explains that Profit Booking / Stop Loss is best-effort automation — not a guaranteed stop.",
    ],
  },
  {
    version: "2.1.0",
    date: "22-Jul-2026",
    releaseKind: "minor",
    summary:
      "Automated Profit-Booking & Stop-Loss (PB/SL) square-off for strategy groups, real-time P&L, live WebSocket quotes, Telegram alerts, and Basket Orders.",
    changes: [
      "Profit-Booking / Stop-Loss (PB/SL): arm a rupee profit target and loss limit on a whole strategy group (all legs of a stock + expiry). The app tracks the group's live P&L and automatically fires exit orders to close every leg the moment your target or stop is hit — no manual watching.",
      "Real-time P&L: open positions are now revalued roughly every 2 seconds from live market data, so group and leg P&L update continuously without hammering the broker.",
      "Per-leg Exit Rules: attach a target-and-stop (GTT OCO) bracket to an individual leg, placed and monitored by ICICI, straight from the Portfolio page.",
      "Telegram alerts: link your Telegram account to get notified the instant a Profit-Booking or Stop-Loss rule fires — including a warning if any exit orders are still live after you reset a rule.",
      "Live quotes now stream over WebSocket during market hours (with Bhavcopy data after hours), replacing slower on-demand quote polling.",
      "New Basket Order page: build a multi-leg option basket, review payoff and PoP, and place all legs together.",
      "Modify an open order's price or quantity in place from the Orders page, instead of cancelling and re-placing it.",
      "Aggressive limit orders in Place Order and Strategy Builder — ICICI derives the price from LTP, so no manual limit price is needed.",
      "Reference data (scrip master, NSE/BSE Bhavcopy, SPAN baselines) now loads at startup and refreshes on a daily schedule, with a new Reference Data Loads view in Settings showing status and history.",
    ],
  },  
  {
    version: "2.0.1",
    date: "16-Jun-2026",
    releaseKind: "patch",
    summary: "Login Disclosure bug fix, minor UI tweaks and storage cleanup",
    changes: [
      "The 'Proceed' button on the Risk Disclosure modal now works correctly.",
      "Minor UI tweaks to the Dashboard so it doesn't throw errors while the data is being fetched.",
      "Cleanup of storage to remove old stopped containers and any dangling images.",
    ],
  },  
  {
    version: "2.0.0",
    date: "14-Jun-2026",
    releaseKind: "major",
    summary: "Strategy Builder (New)",
    changes: [
      "New Strategy Builder (New) page generates Income and Directional (Bullish and Bearish) strategies based on target PoP, margin to deploy, and max loss appetite",
      "Terms & Conditions and SEBI mandated risk disclosure modal after each ICICI login",
    ],
  },
  {
    version: "1.6.3",
    date: "6-Jun-2026",
    releaseKind: "patch",
    summary: "Branding cleanup",
    changes: [
      "Standardized branding to Breeze Modern",
    ],
  },
  {
    version: "1.6.2",
    date: "5-Jun-2026",
    releaseKind: "patch",
    summary: "License Deployment Fixes",
    changes: [
      "License deployment now shows the correct status and message.",
    ],
  },
  {
    version: "1.6.1",
    date: "4-Jun-2026",
    releaseKind: "patch",
    summary: "Login Timestamp Fix",
    changes: [
      "Console now shows the correct login timestamp.",
    ],
  },
  {
    version: "1.6.0",
    date: "1-Jun-2026",
    releaseKind: "minor",
    summary: "DRM Hardening",
    changes: [
      "DRM Hardening: Improved DRM by adding a new layer of security to the application.",
    ],
  },
  {
    version: "1.5.0",
    date: "31-May-2026",
    releaseKind: "minor",
    summary: "Allow invocation of raw ICICI Breeze APIs",
    changes: [
      "Allow invocation of raw ICICI Breeze APIs from the Settings page. This allows testing the APIs for their functionality and response times.",
    ],
  },
  {
    version: "1.4.2",
    date: "12-Apr-2026",
    releaseKind: "patch",
    summary: "Clone and Square-off Fixes",
    changes: [
      "Application doesn't 'lookup' scrip expiries and strikes on cloning and square-off. It just uses the expiry and strike from the order being cloned / squared-off",
    ],
  },
  {
    version: "1.4.1",
    date: "11-Apr-2026",
    releaseKind: "patch",
    summary: "Application Password Reset",
    changes: [
      "Application now allows resetting the app password if user can authenticate with ICICI",
    ],
  },
  {
    version: "1.4.0",
    date: "11-Apr-2026",
    releaseKind: "minor",
    summary: "Responsive Design",
    changes: [
      "Application now is responsive to smaller screens; although only tested on iPhone 13 Pro",
    ],
  },
  {
    version: "1.3.2",
    date: "10-Jun-2026",
    releaseKind: "patch",
    summary: "Strategy Builder (New)",
    changes: [
      "New Strategy Builder (New) page generates all options strategies from range, margin, and risk parameters",
      "Proposed trades use batched market data with single-trade selection, editable legs, and payoff simulation",
    ],
  },
  {
    version: "1.3.1",
    date: "10-Apr-2026",
    releaseKind: "patch",
    summary: "LLM Model Fallback Fix",
    changes: [
      "LLM model fallbacks can be configured in the Settings page",
    ],
  },
  {
    version: "1.3.0",
    date: "10-Apr-2026",
    releaseKind: "minor",
    summary: "Options Strategies Enabled",
    changes: [
      "All options strategies are now enabled",
      "LLM generated market outlook now displays the right error message when the API call fails",
    ],
  },
  {
    version: "1.2.1",
    date: "9-Apr-2026",
    releaseKind: "patch",
    summary: "Square-off and Cloning Fixes",
    changes: [
      "Square-off and cloning takes the user to the 'Place Order' page to allow them to change the price and quantity",
    ],
  },
  {
    version: "1.2.0",
    date: "7-Apr-2026",
    releaseKind: "minor",
    summary: "Advanced Order Management",
    changes: [
      "Clone orders from Order Book",
      "Park orders for later execution",
    ],
  },
  {
    version: "1.1.1",
    date: "4-Apr-2026",
    releaseKind: "patch",
    summary: "Rate Limit Fix",
    changes: [
      "Configurable delays for rate limiting when ICICI returns 429",
    ],
  },
  {
    version: "1.1.0",
    date: "27-Mar-2026",
    releaseKind: "minor",
    summary: "Gen AI Outlook & Portfolio Payoff",
    changes: [
      "Integration with Gemini and OpenAI APIs for Gen AI Outlook (BYOK)",
      "Payoff curve visualization in portfolio page",
    ],
  },
  {
    version: "1.0.0",
    date: "24-Mar-2026",
    releaseKind: "major",
    summary: "Baseline product changelog",
    changes: [
      "Trading dashboard, portfolio, orders, and strategy builder flows",
      "Settings and session-aware ICICI Breeze integration",
    ],
  },
];

export function getLatestRelease(): ChangelogRelease | undefined {
  return changelogReleases[0];
}

/** Newest feature release (major or minor) for highlighting when the latest build is a patch/pre-release. */
export function getLatestFeatureRelease(): ChangelogRelease | undefined {
  return changelogReleases.find(
    (r) => r.releaseKind === "major" || r.releaseKind === "minor",
  );
}

export function getOlderReleases(): ChangelogRelease[] {
  return changelogReleases.slice(1);
}

function releaseKey(r: ChangelogRelease): string {
  return `${r.version}\0${r.date}`;
}

/**
 * Entries for the collapsible history: excludes the latest build and the feature
 * row already shown in the featured section (when those differ).
 */
export function getHistoryReleases(
  latest: ChangelogRelease | undefined,
  featuredRelease: ChangelogRelease | undefined,
): ChangelogRelease[] {
  if (!latest) return [];
  const skip = new Set<string>();
  skip.add(releaseKey(latest));
  if (
    featuredRelease &&
    releaseKey(featuredRelease) !== releaseKey(latest)
  ) {
    skip.add(releaseKey(featuredRelease));
  }
  return changelogReleases.filter((r) => !skip.has(releaseKey(r)));
}

export function inferReleaseKind(
  version: string,
  previousVersion?: string,
): ReleaseKind | null {
  const parsed = parseAppVersion(version);
  if (!parsed) return null;
  if (parsed.prerelease) return "prerelease";
  if (!previousVersion) return "major";
  const previous = parseAppVersion(previousVersion);
  if (!previous) return null;
  if (parsed.major > previous.major) return "major";
  if (parsed.minor > previous.minor) return "minor";
  if (parsed.patch > previous.patch) return "patch";
  return null;
}

export function assertReleaseKindMatchesVersion(
  release: ChangelogRelease,
  previous?: ChangelogRelease,
): void {
  const expected = inferReleaseKind(release.version, previous?.version);
  if (expected !== release.releaseKind) {
    throw new Error(
      `${release.version}: expected releaseKind "${expected}", got "${release.releaseKind}"`,
    );
  }
}
