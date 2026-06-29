import { describe, expect, it } from "vitest";
import {
  formatQuoteAsOf,
  formatQuoteSourceDetail,
  formatQuoteSourceLabel,
  isLiveQuoteSource,
  quoteMetaFromChain,
} from "@/lib/quote-source";
import type { ChainSuccess, QuoteMeta } from "@/lib/strategy-builder/types";

const baseChain: ChainSuccess = {
  chain_rows: [],
  spot_price: 24000,
  atm_strike: 24000,
  expiry_display: "27-Jun-2026",
  stock_code: "NIFTY",
  exchange_code: "NFO",
};

describe("quoteMetaFromChain", () => {
  it("returns null when quote_source is missing", () => {
    expect(quoteMetaFromChain(baseChain)).toBeNull();
  });

  it("extracts websocket metadata", () => {
    const meta = quoteMetaFromChain({
      ...baseChain,
      quote_source: "websocket",
      quote_as_of: "2026-06-27T10:15:00+05:30",
    });
    expect(meta).toEqual({
      quote_source: "websocket",
      bhavcopy_date: null,
      quote_as_of: "2026-06-27T10:15:00+05:30",
    });
  });
});

describe("formatQuoteSourceLabel", () => {
  it("labels websocket", () => {
    const meta: QuoteMeta = { quote_source: "websocket" };
    expect(formatQuoteSourceLabel(meta)).toBe("Live · WebSocket");
  });

  it("labels bhavcopy with date", () => {
    const meta: QuoteMeta = {
      quote_source: "bhavcopy",
      bhavcopy_date: "2026-06-27",
    };
    expect(formatQuoteSourceLabel(meta)).toBe("EOD · Bhavcopy (2026-06-27)");
  });

  it("labels icici api fallback", () => {
    const meta: QuoteMeta = { quote_source: "icici_api" };
    expect(formatQuoteSourceLabel(meta)).toBe("ICICI API");
  });
});

describe("formatQuoteAsOf", () => {
  const now = Date.parse("2026-06-27T10:15:10+05:30");

  it("shows relative age for websocket", () => {
    const meta: QuoteMeta = {
      quote_source: "websocket",
      quote_as_of: "2026-06-27T10:15:00+05:30",
    };
    expect(formatQuoteAsOf(meta, now)).toBe("Updated 10s ago");
  });

  it("shows session date for bhavcopy", () => {
    const meta: QuoteMeta = {
      quote_source: "bhavcopy",
      bhavcopy_date: "2026-06-27",
      quote_as_of: "2026-06-27",
    };
    expect(formatQuoteAsOf(meta, now)).toBe("Session close · 2026-06-27");
  });
});

describe("formatQuoteSourceDetail", () => {
  it("mentions websocket streaming", () => {
    expect(
      formatQuoteSourceDetail({ quote_source: "websocket" }),
    ).toMatch(/WebSocket/i);
  });
});

describe("isLiveQuoteSource", () => {
  it("is true only for websocket", () => {
    expect(isLiveQuoteSource({ quote_source: "websocket" })).toBe(true);
    expect(isLiveQuoteSource({ quote_source: "bhavcopy" })).toBe(false);
  });
});
