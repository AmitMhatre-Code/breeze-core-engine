import { describe, expect, it } from "vitest";
import {
  assertReleaseKindMatchesVersion,
  changelogReleases,
} from "@/lib/changelog";

describe("changelog releaseKind consistency", () => {
  it("matches semver rules for every entry", () => {
    for (let i = 0; i < changelogReleases.length; i += 1) {
      const release = changelogReleases[i];
      const previous = changelogReleases[i + 1];
      expect(() =>
        assertReleaseKindMatchesVersion(release, previous),
      ).not.toThrow();
    }
  });
});
