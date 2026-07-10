import { useQuery } from "@tanstack/react-query";
import { CHAIN_WS_REFETCH_MS } from "@/lib/strategy-builder/chain-query";
import {
  fetchPnlEnginePreferences,
  PNL_ENGINE_PREFERENCES_QUERY_KEY,
} from "@/lib/settings/pnl-engine-preferences";

/** How often to re-poll the setting itself, so a change made elsewhere converges without a reload. */
const SETTING_POLL_MS = 30_000;

/**
 * Portfolio's live chain refetch cadence follows the user's "P&L recalc interval" setting
 * (Settings > Advanced > P&L Engine) instead of the fixed CHAIN_WS_REFETCH_MS every other
 * chain consumer (strategy-builder, place-order, basket-order) uses.
 */
export function usePnlRecomputeRefetchMs(): number {
  const q = useQuery({
    queryKey: PNL_ENGINE_PREFERENCES_QUERY_KEY,
    queryFn: fetchPnlEnginePreferences,
    staleTime: SETTING_POLL_MS,
    refetchInterval: SETTING_POLL_MS,
  });
  const seconds = q.data?.pnl_recompute_interval_seconds;
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) {
    return CHAIN_WS_REFETCH_MS;
  }
  return seconds * 1000;
}
