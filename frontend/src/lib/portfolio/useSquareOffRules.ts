"use client";

import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  SQUAREOFF_RULES_QUERY_KEY,
  fetchSquareOffRules,
  squareOffRuleGroupKey,
  type SquareOffRuleRecord,
} from "@/lib/portfolio/squareoff-rules";
import { usePnlRecomputeRefetchMs } from "@/lib/portfolio/usePnlRecomputeRefetchMs";

/**
 * Armed/fired/fire-failed square-off rules for the current user, keyed by the
 * same (stock_code, expiry_display) bucket Hedge/Square Off All group by,
 * polled at the same cadence as the P&L engine so a group's badge reflects a
 * fire within one recompute cycle. The backend can return more than one row
 * for the same group (e.g. a fire_failed/fired row alongside a freshly-armed
 * replacement) since only 'armed' rows are upserted in place — rows come back
 * newest-first, so the first one seen per key wins.
 */
export function useSquareOffRulesByGroup(): Map<string, SquareOffRuleRecord> {
  const refetchMs = usePnlRecomputeRefetchMs();
  const query = useQuery({
    queryKey: SQUAREOFF_RULES_QUERY_KEY,
    queryFn: fetchSquareOffRules,
    refetchInterval: refetchMs,
  });
  const rules = query.data;
  return useMemo(() => {
    const map = new Map<string, SquareOffRuleRecord>();
    for (const rule of rules ?? []) {
      const key = squareOffRuleGroupKey(rule.stock_code, rule.expiry_display);
      if (!map.has(key)) map.set(key, rule);
    }
    return map;
  }, [rules]);
}

export function useInvalidateSquareOffRules() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: SQUAREOFF_RULES_QUERY_KEY });
}
