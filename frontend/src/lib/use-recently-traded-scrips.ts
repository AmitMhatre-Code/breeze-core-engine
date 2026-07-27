import { useQuery } from "@tanstack/react-query";
import type { RecentScripEntry } from "@/lib/dashboard-bootstrap";

/**
 * Reads the session-cached "recently traded scrips" list seeded once at login by
 * `hydrateDashboardQueryCache` (see LoginDisclosureProvider). Never fetches on its own —
 * there's no standalone endpoint for this, only the dashboard-bootstrap payload — it just
 * reactively reads whatever is in the query cache for the rest of the session.
 */
export function useRecentlyTradedScrips(): RecentScripEntry[] {
  const q = useQuery({
    queryKey: ["scrips", "recent"],
    queryFn: () => Promise.resolve([] as RecentScripEntry[]),
    enabled: false,
    staleTime: Infinity,
    // Default gcTime (5 min) would evict this the moment no page using the hook is
    // mounted — e.g. a few minutes on Dashboard/Portfolio/Orders, none of which use
    // it — silently emptying the quick-select for the rest of the session. This is
    // meant to live for the whole SPA session regardless of which pages get visited
    // in between, so it must never be garbage-collected on its own.
    gcTime: Infinity,
  });
  return q.data ?? [];
}

/** Recent stock codes, most-traded first, narrowed to ones tradeable in the current segment. */
export function filterRecentStockCodes(
  recent: RecentScripEntry[],
  underlyings: { stock_code: string }[],
): string[] {
  const tradeable = new Set(underlyings.map((u) => u.stock_code.toUpperCase()));
  return recent
    .filter((r) => tradeable.has(r.stock_code.toUpperCase()))
    .map((r) => r.stock_code);
}
