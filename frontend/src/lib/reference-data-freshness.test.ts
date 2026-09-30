import { describe, expect, it } from "vitest";
import { staleSourcesText } from "@/lib/reference-data-freshness";

describe("staleSourcesText", () => {
  it("names a stale scrip master and when it was loaded", () => {
    const text = staleSourcesText(["scrip"], "2026-09-27T21:44:00+05:30", false);
    expect(text).toContain("scrip master was last loaded 2026-09-27 21:44");
    expect(text).toContain("no live quote");
    expect(text).toContain("Load now");
  });

  it("names stale bhavcopy segments and a running load", () => {
    const text = staleSourcesText(["NFO", "BFO"], null, true);
    expect(text).toContain("NFO and BFO bhavcopy");
    expect(text).not.toContain("scrip master");
    expect(text).toContain("A load is running now.");
  });
});
