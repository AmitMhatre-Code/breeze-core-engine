import { describe, expect, it } from "vitest";
import { indexQuotesAreTicking, type IndexQuotesResponse } from "@/lib/use-index-quotes";

const NOW = 1_800_000_000_000; // ms

function quote(updatedAtSeconds: number) {
  return {
    ltp: 100,
    previous_close: 99,
    change: 1,
    change_pct: 1,
    updated_at: updatedAtSeconds,
  };
}

function response(nifty: number | null, sensex: number | null): IndexQuotesResponse {
  return {
    quotes: {
      nifty: nifty == null ? null : quote(nifty),
      sensex: sensex == null ? null : quote(sensex),
    },
  };
}

describe("indexQuotesAreTicking", () => {
  it("treats a recent update as ticking", () => {
    expect(indexQuotesAreTicking(response(NOW / 1000 - 5, null), NOW)).toBe(true);
  });

  it("treats a stalled update as not ticking", () => {
    expect(indexQuotesAreTicking(response(NOW / 1000 - 300, null), NOW)).toBe(false);
  });

  it("one live index is enough", () => {
    expect(indexQuotesAreTicking(response(NOW / 1000 - 900, NOW / 1000 - 2), NOW)).toBe(true);
  });

  it("is false with no data at all", () => {
    expect(indexQuotesAreTicking(undefined, NOW)).toBe(false);
    expect(indexQuotesAreTicking(response(null, null), NOW)).toBe(false);
  });
});
