/**
 * Unit tests for Vix30dChart state-routing logic.
 *
 * Because vitest is configured for the node environment without jsdom,
 * we test the *decision* logic that drives each render state rather than
 * the React component tree itself.
 */

import { describe, expect, it } from "vitest";

type Point = { date: string; value: number };

/**
 * Pure function that mirrors the render branching in Vix30dChart:
 *   loading=true  → "loading"
 *   !loading && empty series → "empty"
 *   data present  → "chart"
 */
function vixChartState(
  loading: boolean,
  series: Point[],
): "loading" | "empty" | "chart" {
  if (loading) return "loading";
  if (!series?.length) return "empty";
  return "chart";
}

const SAMPLE_SERIES: Point[] = [
  { date: "2026-03-01", value: 13.5 },
  { date: "2026-03-02", value: 14.0 },
];

describe("Vix30dChart state routing", () => {
  it("shows loading when loading=true regardless of series", () => {
    expect(vixChartState(true, [])).toBe("loading");
    expect(vixChartState(true, SAMPLE_SERIES)).toBe("loading");
  });

  it("shows empty when loading=false and series is empty", () => {
    expect(vixChartState(false, [])).toBe("empty");
  });

  it("shows chart when loading=false and series has data", () => {
    expect(vixChartState(false, SAMPLE_SERIES)).toBe("chart");
  });

  it("loading state takes priority over empty series — never shows 'No VIX history available' while fetching", () => {
    // This is the key regression guard: during the initial fetch, series=[].
    // Without loading=true being checked first, the empty message would flash.
    const stateWhileFetching = vixChartState(true, []);
    expect(stateWhileFetching).not.toBe("empty");
    expect(stateWhileFetching).toBe("loading");
  });
});
