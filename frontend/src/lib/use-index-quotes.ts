"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

export type IndexQuote = {
  ltp: number;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  updated_at: number;
};

export type IndexQuotesResponse = {
  quotes: {
    nifty: IndexQuote | null;
    sensex: IndexQuote | null;
  };
};

const POLL_MS = 3000;

/** Live NIFTY/SENSEX spot + day's change for the navbar ticker. Polls faster
 * than ws-health since these are meant to feel like a live tape, not a status dot. */
export function useIndexQuotes() {
  return useQuery({
    queryKey: ["dashboard", "index-quotes"],
    queryFn: () => apiClient.get<IndexQuotesResponse>("/dashboard/index-quotes"),
    refetchInterval: POLL_MS,
    staleTime: 0,
  });
}
