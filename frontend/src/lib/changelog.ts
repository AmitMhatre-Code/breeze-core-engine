export type ReleaseKind = "major" | "minor";

export type ChangelogRelease = {
  version: string;
  /** ISO date (YYYY-MM-DD) for sorting/display */
  date: string;
  /**
   * `major` — feature milestones; shown as the main story in What’s new.
   * `minor` — patches / urgent fixes; always listed so users can match their build.
   */
  releaseKind: ReleaseKind;
  /** Optional one-line headline for the release */
  summary?: string;
  changes: string[];
};

/** Newest first. Prepend a new entry when you ship; keep `version` in line with `package.json` when you bump it. */
export const changelogReleases: ChangelogRelease[] = [
  {
    version: "1.4.1",
    date: "11-Apr-2026",
    releaseKind: "minor",
    summary: "Application Password Reset",
    changes: [
      "Application now allows resetting the app password if user can authenticate with ICICI",
    ],
  },
  {
    version: "1.4.0",
    date: "11-Apr-2026",
    releaseKind: "major",
    summary: "Responsive Design",
    changes: [
      "Application now is responsive to smaller screens; although only tested on iPhone 13 Pro",
    ],
  },
  {
    version: "1.3.1",
    date: "10-Apr-2026",
    releaseKind: "minor",
    summary: "LLM Model Fallback Fix",
    changes: [
      "LLM model fallbacks can be configured in the Settings page",
    ],
  },
  {
    version: "1.3.0",
    date: "10-Apr-2026",
    releaseKind: "major",
    summary: "Options Strategies Enabled",
    changes: [
      "All options strategies are now enabled",
      "LLM generated market outlook now displays the right error message when the API call fails",
    ],
  },
  {
    version: "1.2.1",
    date: "9-Apr-2026",
    releaseKind: "minor",
    summary: "Square-off and Cloning Fixes",
    changes: [
      "Square-off and cloning takes the user to the 'Place Order' page to allow them to change the price and quantity",
    ],
  },
  {
    version: "1.2.0",
    date: "7-Apr-2026",
    releaseKind: "major",
    summary: "Advanced Order Management",
    changes: [
      "Clone orders from Order Book",
      "Park orders for later execution",
    ],
  },
  {
    version: "1.1.1",
    date: "4-Apr-2026",
    releaseKind: "minor",
    summary: "Rate Limit Fix",
    changes: [
      "Configurable delays for rate limiting when ICICI returns 429",
    ],
  },
  {
    version: "1.1.0",
    date: "27-Mar-2026",
    releaseKind: "major",
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

/** Newest major release in the log (for highlighting when the latest build is a minor). */
export function getLatestMajorRelease(): ChangelogRelease | undefined {
  return changelogReleases.find((r) => r.releaseKind === "major");
}

export function getOlderReleases(): ChangelogRelease[] {
  return changelogReleases.slice(1);
}

function releaseKey(r: ChangelogRelease): string {
  return `${r.version}\0${r.date}`;
}

/**
 * Entries for the collapsible history: excludes the latest build and the major
 * row already shown in the featured major section (when those differ).
 */
export function getHistoryReleases(
  latest: ChangelogRelease | undefined,
  featuredMajor: ChangelogRelease | undefined,
): ChangelogRelease[] {
  if (!latest) return [];
  const skip = new Set<string>();
  skip.add(releaseKey(latest));
  if (
    featuredMajor &&
    releaseKey(featuredMajor) !== releaseKey(latest)
  ) {
    skip.add(releaseKey(featuredMajor));
  }
  return changelogReleases.filter((r) => !skip.has(releaseKey(r)));
}
