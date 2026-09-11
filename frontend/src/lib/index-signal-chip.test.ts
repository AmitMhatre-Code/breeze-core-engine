import { describe, expect, it } from "vitest";

import { indexSignalChip } from "@/lib/index-signal-chip";
import type { IndexSignalSummary } from "@/lib/use-index-quotes";

function summary(overrides: Partial<IndexSignalSummary> = {}): IndexSignalSummary {
  return {
    state: "bullish",
    reason: null,
    signal: 0.34,
    coverage: 0.92,
    thresholds: { enter: 0.3, exit: 0.2 },
    computed_at: 1_800_000_000,
    weights_source: "nse_api",
    weights_as_of: "2026-09-11",
    ...overrides,
  };
}

describe("indexSignalChip", () => {
  it("shows a bullish arrow and word with the numbers in the tooltip", () => {
    const chip = indexSignalChip("NIFTY", summary());
    expect(chip.visible).toBe(true);
    if (!chip.visible) return;
    expect(chip.arrow).toBe("▲");
    expect(chip.word).toBe("BULL");
    expect(chip.toneClass).toContain("up");
    expect(chip.title).toContain("+0.34");
    expect(chip.title).toContain("enter ±0.30");
    expect(chip.title).toContain("coverage 92%");
    expect(chip.title).toContain("weights: NSE, 2026-09-11");
  });

  it("shows bearish and neutral readings", () => {
    const bear = indexSignalChip("SENSEX", summary({ state: "bearish", signal: -0.41 }));
    const neut = indexSignalChip("SENSEX", summary({ state: "neutral", signal: 0.05 }));
    expect(bear.visible && bear.word).toBe("BEAR");
    expect(neut.visible && neut.word).toBe("NEUT");
  });

  it("renders unavailable as a muted dash with the reason, never as neutral", () => {
    const chip = indexSignalChip("NIFTY", summary({ state: "unavailable", reason: "low_coverage", signal: null }));
    expect(chip.visible).toBe(true);
    if (!chip.visible) return;
    expect(chip.arrow).toBe("—");
    expect(chip.word).toBe("");
    expect(chip.title).toContain("unavailable");
    expect(chip.title).toContain("live order book");
  });

  it("hides the chip when the signal is switched off or absent", () => {
    expect(indexSignalChip("NIFTY", summary({ state: "unavailable", reason: "disabled" })).visible).toBe(false);
    expect(indexSignalChip("NIFTY", null).visible).toBe(false);
    expect(indexSignalChip("NIFTY", undefined).visible).toBe(false);
  });

  it("labels seeded weights plainly", () => {
    const chip = indexSignalChip("NIFTY", summary({ weights_source: "seed", weights_as_of: "2026-09-10" }));
    expect(chip.visible && chip.title).toContain("built-in seed, 2026-09-10");
  });
});
