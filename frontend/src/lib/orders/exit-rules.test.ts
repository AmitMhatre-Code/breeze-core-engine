import { describe, expect, it } from "vitest";
import {
  exitRuleResolvedDate,
  filterExitRuleRowsByResolvedDate,
  type ExitRuleRow,
} from "./exit-rules";

function row(partial: Partial<ExitRuleRow> & { id: string }): ExitRuleRow {
  return {
    kind: "group",
    stockCode: "NIFTY",
    expiryDisplay: "21-Jul-2026",
    exchangeCode: "NFO",
    legCount: 2,
    legs: null,
    strikePrice: null,
    right: null,
    action: null,
    quantity: null,
    averagePrice: null,
    effectiveStatus: "completed",
    targetValue: 25,
    stopValue: 25,
    targetPct: null,
    stopPct: null,
    placedAt: null,
    resolvedAt: null,
    orders: [],
    failureReason: null,
    rule: null,
    ...partial,
  };
}

describe("exitRuleResolvedDate", () => {
  it("takes the date half of a SQLite timestamp", () => {
    expect(
      exitRuleResolvedDate(row({ id: "a", resolvedAt: "2026-07-21 07:37:54" })),
    ).toBe("2026-07-21");
  });

  it("returns null when there is no resolved timestamp", () => {
    expect(exitRuleResolvedDate(row({ id: "a", resolvedAt: null }))).toBeNull();
    expect(exitRuleResolvedDate(row({ id: "b", resolvedAt: "  " }))).toBeNull();
  });

  it("returns null rather than a garbage date for an unexpected shape", () => {
    expect(exitRuleResolvedDate(row({ id: "a", resolvedAt: "21/07/2026" }))).toBeNull();
  });
});

describe("filterExitRuleRowsByResolvedDate", () => {
  // The two rows from a real Orders page: a rule resolved before the default
  // today->tomorrow window, and one placed days earlier that resolved inside it.
  const nifty = row({ id: "nifty", resolvedAt: "2026-07-20 17:23:03" });
  const bsesen = row({
    id: "bsesen",
    stockCode: "BSESEN",
    placedAt: "2026-07-16 07:58:16",
    resolvedAt: "2026-07-21 07:37:54",
  });

  it("keeps rows resolved inside the range and drops the rest", () => {
    const kept = filterExitRuleRowsByResolvedDate(
      [nifty, bsesen],
      "2026-07-21",
      "2026-07-22",
    );
    expect(kept.map((r) => r.id)).toEqual(["bsesen"]);
  });

  it("is inclusive of both endpoints", () => {
    const kept = filterExitRuleRowsByResolvedDate(
      [nifty, bsesen],
      "2026-07-20",
      "2026-07-21",
    );
    expect(kept.map((r) => r.id)).toEqual(["nifty", "bsesen"]);
  });

  it("scopes on resolved date, not placed date", () => {
    // bsesen was placed on the 16th; a range starting after that must still keep it.
    const kept = filterExitRuleRowsByResolvedDate(
      [bsesen],
      "2026-07-21",
      "2026-07-22",
    );
    expect(kept.map((r) => r.id)).toEqual(["bsesen"]);
  });

  it("keeps rows with no resolved timestamp at all", () => {
    // Leg-GTT rows: already scoped by the order fetch that made them History.
    const gtt = row({
      id: "gtt",
      kind: "leg_gtt",
      effectiveStatus: "exited",
      placedAt: "2026-06-01 10:00:00",
      resolvedAt: null,
    });
    const kept = filterExitRuleRowsByResolvedDate(
      [gtt],
      "2026-07-21",
      "2026-07-22",
    );
    expect(kept.map((r) => r.id)).toEqual(["gtt"]);
  });
});
