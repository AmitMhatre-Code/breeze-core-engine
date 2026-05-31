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
    version: "1.5.0",
    date: "31-May-2026",
    releaseKind: "major",
    summary: "Allow invocation of raw ICICI Breeze APIs",
    changes: [
      "Allow invocation of raw ICICI Breeze APIs from the Settings page. This allows testing the APIs for their functionality and response times.",
    ],
  },
  {
    version: "1.4.2-o",
    date: "25-May-2026",
    releaseKind: "prerelease",
    summary: "Add support for unlicensed deployments",
    changes: [
      "Add support for unlicensed deployments",
    ],
  },
  {
    version: "1.4.2-m",
    date: "25-May-2026",
    releaseKind: "prerelease",
    summary: "Harden in-place upgrade helper recreate",
    changes: [
      "Upgrade helper writes merged env to /opt/breeze-core-engine/.upgrade.env before recreate (host .env plus running container).",
      "Upgrade steps append to /opt/breeze-core-engine/upgrade.log; helper container is kept for docker logs on failure.",
      "Recreate verifies the old container name is removed before docker run (avoids name-conflict leaving the site down).",
    ],
  },
  {
    version: "1.4.2-j",
    date: "25-May-2026",
    releaseKind: "prerelease",
    summary: "In-place upgrade uses detached docker-cli helper",
    changes: [
      "Console upgrades recreate the container via a detached helper instead of stopping the app from inside (fixes failed upgrades leaving the site down).",
      "Upgrades apply /opt/breeze-core-engine/.env via --env-file on recreate.",
    ],
  },
  {
    version: "1.4.2-h",
    date: "23-May-2026",
    releaseKind: "prerelease",
    summary: "Live testing of license deployment",
    changes: [
      "Live testing of license deployment",
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
