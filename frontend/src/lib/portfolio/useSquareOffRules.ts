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
 * Every Strategy Group worth surfacing for the current user (live ones + Reset), polled at
 * the same cadence as the P&L engine so a badge reflects a fire within one recompute
 * cycle.
 */
export function useSquareOffRules(): SquareOffRuleRecord[] | undefined {
  const refetchMs = usePnlRecomputeRefetchMs();
  return useQuery({
    queryKey: SQUAREOFF_RULES_QUERY_KEY,
    queryFn: fetchSquareOffRules,
    refetchInterval: refetchMs,
  }).data;
}

/**
 * Strategy Groups keyed by the same (stock_code, expiry_display) bucket Hedge/Square Off
 * All group by.
 *
 * At most ONE *live* SG can exist per key — that is now a DB invariant (a partial unique
 * index), not a convention. But a terminal `reset` row can coexist with a freshly-armed
 * replacement for the same key, so preference order still matters: a live SG wins over a
 * Reset, because the live one is what the user is relying on right now. Within the same
 * tier, rows come back newest-first and the first wins.
 */
export function useSquareOffRulesByGroup(): Map<string, SquareOffRuleRecord> {
  const rules = useSquareOffRules();
  return useMemo(() => {
    const map = new Map<string, SquareOffRuleRecord>();
    for (const rule of rules ?? []) {
      const key = squareOffRuleGroupKey(rule.stock_code, rule.expiry_display);
      const seen = map.get(key);
      if (!seen) {
        map.set(key, rule);
        continue;
      }
      // A live SG outranks a retired one for the same key.
      if (seen.status === "reset" && rule.status !== "reset") map.set(key, rule);
    }
    return map;
  }, [rules]);
}

export function useInvalidateSquareOffRules() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: SQUAREOFF_RULES_QUERY_KEY });
}
