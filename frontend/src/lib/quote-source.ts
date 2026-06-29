import type { ChainSuccess, QuoteMeta, QuoteSource } from "@/lib/strategy-builder/types";

const VALID_SOURCES: QuoteSource[] = ["websocket", "bhavcopy", "icici_api"];

function isQuoteSource(raw: unknown): raw is QuoteSource {
  return typeof raw === "string" && VALID_SOURCES.includes(raw as QuoteSource);
}

/** Extract quote provenance from a loaded option chain payload. */
export function quoteMetaFromChain(
  success: ChainSuccess | null | undefined,
): QuoteMeta | null {
  if (!success || !isQuoteSource(success.quote_source)) return null;
  return {
    quote_source: success.quote_source,
    bhavcopy_date: success.bhavcopy_date ?? null,
    quote_as_of: success.quote_as_of ?? null,
  };
}

export function isLiveQuoteSource(meta: QuoteMeta | null | undefined): boolean {
  return meta?.quote_source === "websocket";
}

export function formatQuoteSourceLabel(meta: QuoteMeta): string {
  switch (meta.quote_source) {
    case "websocket":
      return "Live · WebSocket";
    case "bhavcopy":
      return meta.bhavcopy_date
        ? `EOD · Bhavcopy (${meta.bhavcopy_date})`
        : "EOD · Bhavcopy";
    case "icici_api":
      return "ICICI API";
    default:
      return "Quote source unknown";
  }
}

export function formatQuoteSourceDetail(meta: QuoteMeta): string {
  switch (meta.quote_source) {
    case "websocket":
      return "Prices and depth are streamed from the ICICI Breeze WebSocket during market hours. Values refresh automatically while you stay on this page.";
    case "bhavcopy":
      return meta.bhavcopy_date
        ? `Closing prices from the NSE/BSE FO Bhavcopy for ${meta.bhavcopy_date}. Open interest and depth reflect the last concluded session, not live market data.`
        : "Closing prices from the NSE/BSE FO Bhavcopy after market hours. Not live market data.";
    case "icici_api":
      return "Quotes were fetched via the ICICI Breeze REST API because live WebSocket or Bhavcopy data was unavailable.";
    default:
      return "Quote source could not be determined.";
  }
}

function parseQuoteAsOfMs(meta: QuoteMeta): number | null {
  const raw = meta.quote_as_of?.trim();
  if (!raw) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const d = new Date(`${raw}T15:30:00+05:30`);
    return Number.isFinite(d.getTime()) ? d.getTime() : null;
  }
  const d = new Date(raw);
  return Number.isFinite(d.getTime()) ? d.getTime() : null;
}

export function formatRelativeAge(msAgo: number): string {
  const sec = Math.max(0, Math.round(msAgo / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  return `${hr}h ago`;
}

/** Short freshness line for badges (relative for live WS, date for EOD). */
export function formatQuoteAsOf(
  meta: QuoteMeta,
  nowMs: number = Date.now(),
): string | null {
  const asOfMs = parseQuoteAsOfMs(meta);
  if (asOfMs == null) return null;

  if (meta.quote_source === "websocket") {
    return `Updated ${formatRelativeAge(nowMs - asOfMs)}`;
  }
  if (meta.quote_source === "bhavcopy") {
    return meta.bhavcopy_date
      ? `Session close · ${meta.bhavcopy_date}`
      : "End of day prices";
  }
  if (meta.quote_source === "icici_api") {
    return `Fetched ${formatRelativeAge(nowMs - asOfMs)}`;
  }
  return null;
}
