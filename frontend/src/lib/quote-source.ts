import type { ChainSuccess, QuoteMeta, QuoteSource } from "@/lib/strategy-builder/types";
import { quoteSourceDetailLine } from "@/lib/help/topic-content";
import { formatIsoDateDdMmmYyyy } from "@/lib/format-iso-date";

const VALID_SOURCES: QuoteSource[] = [
  "websocket",
  "bhavcopy",
  "icici_api",
  "snapshot",
];

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
    bhavcopy_stale: success.bhavcopy_stale ?? false,
    depth_as_of: success.depth_as_of ?? null,
  };
}

export function isLiveQuoteSource(meta: QuoteMeta | null | undefined): boolean {
  return meta?.quote_source === "websocket";
}

/** True once a trading session has opened after the bhavcopy source date. */
export function isBhavcopyStale(meta: QuoteMeta | null | undefined): boolean {
  return meta?.quote_source === "bhavcopy" && meta.bhavcopy_stale === true;
}

export function formatQuoteSourceLabel(meta: QuoteMeta): string {
  switch (meta.quote_source) {
    case "websocket":
      return "Live · WebSocket";
    case "bhavcopy":
      return meta.bhavcopy_date
        ? `Bhavcopy (${formatIsoDateDdMmmYyyy(meta.bhavcopy_date)})`
        : "Bhavcopy";
    case "icici_api":
      return "ICICI API";
    case "snapshot":
      return "Session close (captured)";
    default:
      return "Quote source unknown";
  }
}

/** Human label for when the depth in a snapshot chain was actually captured. */
export function formatDepthAsOf(meta: QuoteMeta | null | undefined): string | null {
  const raw = meta?.depth_as_of?.trim();
  if (!raw) return null;
  const d = new Date(raw);
  if (!Number.isFinite(d.getTime())) return null;
  const time = d.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
  });
  return `Depth as of ${time}`;
}

export function formatQuoteSourceDetail(meta: QuoteMeta): string {
  switch (meta.quote_source) {
    case "websocket":
      return quoteSourceDetailLine("websocket");
    case "bhavcopy":
      return quoteSourceDetailLine("bhavcopy", meta.bhavcopy_date, meta.bhavcopy_stale);
    case "icici_api":
      return quoteSourceDetailLine("icici_api");
    case "snapshot":
      return quoteSourceDetailLine("snapshot");
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
    // Bhavcopy date is already shown in the label itself; no separate asOf line.
    return null;
  }
  if (meta.quote_source === "icici_api") {
    return `Fetched ${formatRelativeAge(nowMs - asOfMs)}`;
  }
  if (meta.quote_source === "snapshot") {
    return formatDepthAsOf(meta);
  }
  return null;
}
