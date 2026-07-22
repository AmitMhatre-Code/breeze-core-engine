import { describe, expect, it } from "vitest";
import {
  formatDepthAsOf,
  formatQuoteAsOf,
  formatQuoteSourceDetail,
  formatQuoteSourceLabel,
  isBhavcopyStale,
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
      depth_as_of: null,
      bhavcopy_date: null,
      quote_as_of: "2026-06-27T10:15:00+05:30",
      bhavcopy_stale: false,
    });
  });

  it("extracts bhavcopy staleness", () => {
    const meta = quoteMetaFromChain({
      ...baseChain,
      quote_source: "bhavcopy",
      bhavcopy_date: "2026-06-27",
      bhavcopy_stale: true,
    });
    expect(meta?.bhavcopy_stale).toBe(true);
  });
});

describe("formatQuoteSourceLabel", () => {
  it("labels websocket", () => {
    const meta: QuoteMeta = { quote_source: "websocket" };
    expect(formatQuoteSourceLabel(meta)).toBe("Live · WebSocket");
  });

  it("labels bhavcopy with dd-MMM-yyyy date, no EOD prefix", () => {
    const meta: QuoteMeta = {
      quote_source: "bhavcopy",
      bhavcopy_date: "2026-06-27",
    };
    expect(formatQuoteSourceLabel(meta)).toBe("Bhavcopy (27-Jun-2026)");
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

  it("has no separate asOf line for bhavcopy (date is already in the label)", () => {
    const meta: QuoteMeta = {
      quote_source: "bhavcopy",
      bhavcopy_date: "2026-06-27",
      quote_as_of: "2026-06-27",
    };
    expect(formatQuoteAsOf(meta, now)).toBeNull();
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

describe("isBhavcopyStale", () => {
  it("is true only for bhavcopy with bhavcopy_stale set", () => {
    expect(
      isBhavcopyStale({ quote_source: "bhavcopy", bhavcopy_stale: true }),
    ).toBe(true);
    expect(
      isBhavcopyStale({ quote_source: "bhavcopy", bhavcopy_stale: false }),
    ).toBe(false);
    expect(isBhavcopyStale({ quote_source: "bhavcopy" })).toBe(false);
    expect(
      isBhavcopyStale({ quote_source: "websocket", bhavcopy_stale: true }),
    ).toBe(false);
    expect(isBhavcopyStale(null)).toBe(false);
  });
});

describe("snapshot quote source", () => {
  const snapshotMeta: QuoteMeta = {
    quote_source: "snapshot",
    depth_as_of: "2026-06-27T15:29:41+05:30",
    quote_as_of: "2026-06-27T15:29:41+05:30",
  };

  it("is recognised as a valid source on the chain payload", () => {
    expect(
      quoteMetaFromChain({ ...baseChain, quote_source: "snapshot" })
        ?.quote_source,
    ).toBe("snapshot");
  });

  it("is never treated as live", () => {
    expect(isLiveQuoteSource(snapshotMeta)).toBe(false);
  });

  it("labels itself as captured session-close data", () => {
    expect(formatQuoteSourceLabel(snapshotMeta)).toBe("Session close (captured)");
  });

  it("explains that BSE clears its book at close", () => {
    expect(formatQuoteSourceDetail(snapshotMeta)).toContain("order book");
  });

  it("surfaces when the depth was actually captured", () => {
    expect(formatDepthAsOf(snapshotMeta)).toBe("Depth as of 03:29 pm");
    expect(formatQuoteAsOf(snapshotMeta)).toBe("Depth as of 03:29 pm");
  });

  it("has no depth line when the chain carried no capture time", () => {
    expect(formatDepthAsOf({ quote_source: "snapshot" })).toBeNull();
    expect(formatDepthAsOf({ quote_source: "bhavcopy" })).toBeNull();
    expect(formatDepthAsOf(null)).toBeNull();
  });
});
