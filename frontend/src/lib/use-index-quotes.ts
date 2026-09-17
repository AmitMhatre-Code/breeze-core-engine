"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { fetchMarketStatus } from "@/lib/market-status";
import { useQuoteFlushRefetchMs } from "@/lib/settings/useQuoteFlushRefetchMs";

export type IndexQuote = {
  ltp: number;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  updated_at: number;
};

export type IndexSignalState = "bullish" | "bearish" | "neutral" | "unavailable";

/** The slim per-index direction-signal view the backend adds to this poll
 * (`index_signal.reader.navbar_view`). `reason` is set whenever `state` is
 * "unavailable"; "disabled" means switched off in Settings → Index Signal. */
export type IndexSignalSummary = {
  state: IndexSignalState;
  reason: string | null;
  signal: number | null;
  coverage: number | null;
  /** Order-book imbalance carries enter/exit; volume expansion carries percentiles (#34). */
  thresholds:
    | { enter: number; exit: number }
    | { price_percentile: number; volume_percentile: number }
    | null;
  /** Which mechanism produced this reading. Absent from payloads written before #34. */
  mechanism?: "wobi" | "expansion" | null;
  computed_at: number | null;
  weights_source: string | null;
  weights_as_of: string | null;
};

export type IndexQuotesResponse = {
  quotes: {
    nifty: IndexQuote | null;
    sensex: IndexQuote | null;
  };
  /** Absent from an older backend; the navbar then shows no signal chip. */
  signals?: {
    nifty?: IndexSignalSummary | null;
    sensex?: IndexSignalSummary | null;
  };
};

/** Slow heartbeat once the calendar says closed and the ticks have stopped. */
export const CLOSED_HEARTBEAT_MS = 30_000;
/** How recent an `updated_at` must be to count as "ticks are still arriving". */
const TICKING_WITHIN_MS = 60_000;

export function indexQuotesAreTicking(
  data: IndexQuotesResponse | undefined,
  nowMs: number,
): boolean {
  const quotes = [data?.quotes.nifty, data?.quotes.sensex];
  return quotes.some(
    (q) =>
      typeof q?.updated_at === "number" &&
      nowMs - q.updated_at * 1000 < TICKING_WITHIN_MS,
  );
}

/** Live NIFTY/SENSEX spot + day's change for the navbar ticker.
 *
 * Poll cadence follows the user's WS quote-flush-interval setting (see
 * `useQuoteFlushRefetchMs`) instead of a fixed interval.
 *
 * The cadence follows the feed rather than the calendar: ticks still arriving
 * after the configured close keep it at the live cadence, so a session the
 * exchange runs later than the calendar knows about doesn't freeze the ticker.
 * Once they stop it drops to a slow heartbeat — the backend then serves a one-off
 * REST EOD quote (2 ICICI calls for the whole day, cached with no TTL), so the
 * heartbeat itself costs nothing further. */
export function useIndexQuotes() {
  const flushMs = useQuoteFlushRefetchMs();
  const marketStatus = useQuery({
    queryKey: ["settings", "market-status"],
    queryFn: fetchMarketStatus,
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const marketOpen = marketStatus.data?.is_open ?? true;

  return useQuery({
    queryKey: ["dashboard", "index-quotes"],
    queryFn: () => apiClient.get<IndexQuotesResponse>("/dashboard/index-quotes"),
    refetchInterval: (query) => {
      if (marketOpen) return flushMs;
      return indexQuotesAreTicking(query.state.data, Date.now())
        ? flushMs
        : CLOSED_HEARTBEAT_MS;
    },
    staleTime: 0,
  });
}
