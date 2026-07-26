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
