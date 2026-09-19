import { describe, expect, it } from "vitest";

import { indexSignalChip } from "@/lib/index-signal-chip";
import type { IndexSignalSummary } from "@/lib/use-index-quotes";

function summary(overrides: Partial<IndexSignalSummary> = {}): IndexSignalSummary {
  return {
    state: "bullish",
    reason: null,
    signal: 0.92,
    mechanism: "expansion",
    duration_minutes: 15,
    computed_at: 1_800_000_000,
    thin_data: false,
    uses_oi: true,
    call_started_at: 1_800_000_000,
    // 10:15 IST on 2027-01-15.
    held_until: Date.UTC(2027, 0, 15, 4, 45) / 1000,
    ...overrides,
  };
}

describe("indexSignalChip", () => {
  it("shows a bullish arrow and word, naming the mechanism and when the call ends", () => {
    const chip = indexSignalChip("NIFTY", summary());
    expect(chip.visible).toBe(true);
    if (!chip.visible) return;
    expect(chip.arrow).toBe("▲");
    expect(chip.word).toBe("BULL");
    expect(chip.toneClass).toContain("up");
    expect(chip.title).toContain("Volume expansion 15m");
    expect(chip.title).toContain("call stands until 10:15");
  });

  it("shows bearish and quiet readings", () => {
    const bear = indexSignalChip("SENSEX", summary({ state: "bearish" }));
    const quiet = indexSignalChip("SENSEX", summary({ state: "neutral", held_until: null }));
    expect(bear.visible && bear.word).toBe("BEAR");
    expect(quiet.visible && quiet.word).toBe("NEUT");
    expect(quiet.visible && quiet.title).toContain("Quiet");
  });

  it("renders unavailable as a muted dash with the reason, never as neutral", () => {
    const chip = indexSignalChip("NIFTY", summary({ state: "unavailable", reason: "warming_up", signal: null }));
    expect(chip.visible).toBe(true);
    if (!chip.visible) return;
    expect(chip.arrow).toBe("—");
    expect(chip.word).toBe("");
    expect(chip.title).toContain("no reading");
    expect(chip.title).toContain("warming up");
  });

  it("says when SENSEX runs on thin data", () => {
    const chip = indexSignalChip("SENSEX", summary({ mechanism: "momentum", thin_data: true }));
    expect(chip.visible && chip.title).toContain("Momentum 15m");
    expect(chip.visible && chip.title).toContain("thin data");
  });

  it("hides the chip when there is no signal in the payload", () => {
    expect(indexSignalChip("NIFTY", null).visible).toBe(false);
    expect(indexSignalChip("NIFTY", undefined).visible).toBe(false);
  });
});
