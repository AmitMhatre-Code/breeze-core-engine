import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api-client";
import { fetchRealBasketMargins } from "@/lib/strategy-builder/real-margin";
import type { StrategyLeg } from "@/lib/strategy-builder/types";

function leg(overrides: Partial<StrategyLeg> = {}): StrategyLeg {
  return {
    id: "leg-1",
    right: "Call",
    side: "Sell",
    strike: 23500,
    lots: 1,
    premiumPerUnit: 100,
    ...overrides,
  };
}

const ctx = {
  stockCode: "NIFTY",
  exchangeCode: "NFO",
  expiryDate: "09-Jun-2099",
  lotSize: 75,
  spot: 23400,
};

describe("fetchRealBasketMargins — portfolio-aware netting (D1-D10)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("marginBenefit (intra-structure) uses standaloneSpan, not the netted span, when netting applied", async () => {
    // Two sell legs: per-leg standalone calls return 40000 each (sum=80000).
    // The basket call is netted against positions: span(incremental)=25000,
    // standalone_span_margin(basket's own pre-netting figure)=60000.
    const legs: StrategyLeg[] = [
      leg({ id: "a", strike: 23500 }),
      leg({ id: "b", strike: 24000 }),
    ];

    const postSpy = vi.spyOn(apiClient, "post").mockImplementation(async (_path, body) => {
      const legCount = (body as { legs: unknown[] }).legs.length;
      if (legCount === 1) {
        return { Status: 200, Error: null, Success: { span_margin_required: 40_000 } };
      }
      // whole-basket call
      return {
        Status: 200,
        Error: null,
        Success: {
          span_margin_required: 25_000,
          standalone_span_margin: 60_000,
          existing_span_margin: 30_000,
          combined_span_margin: 55_000,
          positions_margin_benefit: 35_000,
          netted_against_positions: true,
          netted_position_count: 1,
        },
      };
    });

    const result = await fetchRealBasketMargins({ legs, ...ctx });

    expect(postSpy).toHaveBeenCalled();
    expect(result.spanMargin).toBe(25_000); // incremental, shown as the headline
    expect(result.standaloneSpan).toBe(60_000);
    // marginBenefit = sumStandalone(80000) - standaloneSpan(60000) = 20000,
    // NOT sumStandalone - spanMargin(25000)=55000 -- the pre-fix (wrong) formula.
    expect(result.marginBenefit).toBe(20_000);
    expect(result.positionsMarginBenefit).toBe(35_000);
    expect(result.nettedAgainstPositions).toBe(true);
    expect(result.nettedPositionCount).toBe(1);
  });

  it("standaloneSpan falls back to span itself when the server did not net", async () => {
    const legs: StrategyLeg[] = [leg({ id: "a", strike: 23500 })];

    vi.spyOn(apiClient, "post").mockResolvedValue({
      Status: 200,
      Error: null,
      Success: { span_margin_required: 40_000 },
    });

    const result = await fetchRealBasketMargins({ legs, ...ctx });

    expect(result.spanMargin).toBe(40_000);
    expect(result.standaloneSpan).toBe(40_000);
    expect(result.marginBenefit).toBe(0); // sumStandalone(40000) - standaloneSpan(40000)
    expect(result.nettedAgainstPositions).toBe(false);
    expect(result.positionsMarginBenefit).toBeNull();
    expect(result.nettingUnavailableReason).toBeNull();
  });

  it("passes through nettingUnavailableReason for the D7 fallback banner", async () => {
    const legs: StrategyLeg[] = [leg({ id: "a", strike: 23500 })];

    vi.spyOn(apiClient, "post").mockResolvedValue({
      Status: 200,
      Error: null,
      Success: {
        span_margin_required: 40_000,
        netting_unavailable_reason: "Unable to load open positions — showing standalone margin.",
      },
    });

    const result = await fetchRealBasketMargins({ legs, ...ctx });

    expect(result.nettingUnavailableReason).toBe(
      "Unable to load open positions — showing standalone margin.",
    );
  });

  it("buy legs never trigger a standalone fan-out call and carry zero standalone margin", async () => {
    const legs: StrategyLeg[] = [
      leg({ id: "a", strike: 23500, side: "Sell" }),
      leg({ id: "b", strike: 23000, side: "Buy" }),
    ];

    const postSpy = vi.spyOn(apiClient, "post").mockImplementation(async (_path, body) => {
      const legCount = (body as { legs: unknown[] }).legs.length;
      if (legCount === 1) {
        return { Status: 200, Error: null, Success: { span_margin_required: 40_000 } };
      }
      return { Status: 200, Error: null, Success: { span_margin_required: 30_000 } };
    });

    const result = await fetchRealBasketMargins({ legs, ...ctx });

    // One per-leg call (the sell) + one basket call = 2 total.
    expect(postSpy).toHaveBeenCalledTimes(2);
    expect(result.perLegMargin["b"]).toBe(0);
    expect(result.perLegMargin["a"]).toBe(40_000);
  });
});
