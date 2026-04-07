export type ChangelogRelease = {
  version: string;
  /** ISO date (YYYY-MM-DD) for sorting/display */
  date: string;
  /** Optional one-line headline for the release */
  summary?: string;
  changes: string[];
};

/** Newest first. Prepend a new entry when you ship; keep `version` in line with `package.json` when you bump it. */
export const changelogReleases: ChangelogRelease[] = [
  {
    version: "1.0.4",
    date: "2026-04-7",
    summary: "Advanced Order Management",
    changes: [
      "Clone orders from Order Book",
      "Park orders for later execution",
    ],
  },
  {
    version: "1.0.3",
    date: "2026-04-4",
    summary: "Rate Limit Fix",
    changes: [
      "Configurable delays for rate limiting when ICICI returns 429",
    ],
  },
  {
    version: "1.0.1",
    date: "2026-03-27",
    summary: "Gen AI Outlook & Portfolio Payoff",
    changes: [
      "Integration with Gemini and OpenAI APIs for Gen AI Outlook (BYOK)",
      "Payoff curve visualization in portfolio page",
    ],
  },
  {
    version: "1.0.0",
    date: "2026-03-24",
    summary: "Baseline product changelog",
    changes: [
      "Trading dashboard, portfolio, orders, and strategy builder flows",
      "Settings and session-aware ICICI Breeze integration",
    ],
  }  
];

export function getLatestRelease(): ChangelogRelease | undefined {
  return changelogReleases[0];
}

export function getOlderReleases(): ChangelogRelease[] {
  return changelogReleases.slice(1);
}
