import { useQuery } from "@tanstack/react-query";
import {
  fetchPnlEnginePreferences,
  PNL_ENGINE_PREFERENCES_QUERY_KEY,
} from "@/lib/settings/pnl-engine-preferences";

const SETTING_POLL_MS = 30_000;
const DEFAULT_FLUSH_MS = 2000;

/**
 * Navbar index-quote poll cadence follows the user's "WS quote flush interval"
 * setting (Settings > Advanced > P&L Engine), the same value the backend uses
 * to flush ticks into Redis -- mirrors `usePnlRecomputeRefetchMs`, reading
 * `quote_flush_interval_seconds` instead of `pnl_recompute_interval_seconds`.
 * Shares the same query key/fn as that hook, so this doesn't add a second poll loop.
 */
export function useQuoteFlushRefetchMs(): number {
  const q = useQuery({
    queryKey: PNL_ENGINE_PREFERENCES_QUERY_KEY,
    queryFn: fetchPnlEnginePreferences,
    staleTime: SETTING_POLL_MS,
    refetchInterval: SETTING_POLL_MS,
  });
  const seconds = q.data?.quote_flush_interval_seconds;
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) {
    return DEFAULT_FLUSH_MS;
  }
  return seconds * 1000;
}
