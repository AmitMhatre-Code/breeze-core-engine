"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getAggressiveOrderPreferences,
  type AggressiveOrderMode,
} from "@/lib/aggressive-order";
import { useAggressiveOrderConfig } from "@/lib/use-aggressive-limit-order-enabled";

export type AggressiveOrderControls = {
  enabled: boolean;
  mode: AggressiveOrderMode;
  tolerancePct: number;
  maxTolerancePct: number;
  setMode: (m: AggressiveOrderMode) => void;
  setTolerancePct: (pct: number) => void;
};

/**
 * Order-form controls for the aggressive (⚡) toggle. Seeds mode + tolerance from the user's saved
 * default (per-user preference) the first time it loads, then lets the form override either for the
 * current order. The saved default itself is only changed from Settings, not here.
 */
export function useAggressiveOrderControls(): AggressiveOrderControls {
  const config = useAggressiveOrderConfig();
  const prefsQ = useQuery({
    queryKey: ["aggressive-order", "prefs"],
    queryFn: getAggressiveOrderPreferences,
    enabled: config.enabled,
    staleTime: 5 * 60_000,
  });

  // Local overrides for the current order; null means "follow the saved default", which the
  // effective values below fall back to — so no seeding effect is needed.
  const [mode, setMode] = useState<AggressiveOrderMode | null>(null);
  const [tolerancePct, setTolerancePct] = useState<number | null>(null);

  return {
    enabled: config.enabled,
    mode: mode ?? prefsQ.data?.mode ?? "limit_tolerance",
    tolerancePct:
      tolerancePct ?? prefsQ.data?.tolerance_pct ?? config.defaultTolerancePct,
    maxTolerancePct: config.maxTolerancePct,
    setMode,
    setTolerancePct,
  };
}
