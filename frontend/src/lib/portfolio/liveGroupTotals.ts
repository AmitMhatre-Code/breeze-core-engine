"use client";

import { createContext, useContext, useEffect, useSyncExternalStore } from "react";

/** Live MTM/Carry rupees for one open-position group, summed off its live-overlay
 * rows. `null` means that group has no usable figure yet (chain not live and the
 * snapshot carries nothing) — the aggregator falls back to the snapshot for it. */
export type GroupLiveTotal = { mtm: number | null; carry: number | null };

export type LiveGroupTotalsStore = {
  set: (groupKey: string, value: GroupLiveTotal) => void;
  remove: (groupKey: string) => void;
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => ReadonlyMap<string, GroupLiveTotal>;
};

const EMPTY_MAP: ReadonlyMap<string, GroupLiveTotal> = new Map();
const NOOP_SUBSCRIBE = () => () => {};

/**
 * A tiny external store the Portfolio summary tiles subscribe to so they can
 * repaint at the per-group WS cadence (~`pnl_recompute_interval_seconds`) without
 * dragging `OpenPositionsTable` into a re-render every tick. Each group block
 * writes its live MTM/Carry sum here; `PortfolioSummaryPanel` reads the whole map
 * via `useSyncExternalStore` and no other subtree observes it.
 *
 * `set` is identity-stable when the value is unchanged, so an idle tick (or the
 * desktop + mobile group blocks both reporting the same numbers) emits nothing.
 */
export function createLiveGroupTotalsStore(): LiveGroupTotalsStore {
  let map: Map<string, GroupLiveTotal> = new Map();
  const listeners = new Set<() => void>();
  const emit = () => {
    for (const listener of listeners) listener();
  };
  return {
    set(groupKey, value) {
      const prev = map.get(groupKey);
      if (prev && prev.mtm === value.mtm && prev.carry === value.carry) return;
      map = new Map(map);
      map.set(groupKey, { mtm: value.mtm, carry: value.carry });
      emit();
    },
    remove(groupKey) {
      if (!map.has(groupKey)) return;
      map = new Map(map);
      map.delete(groupKey);
      emit();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot() {
      return map;
    },
  };
}

export const LiveGroupTotalsContext = createContext<LiveGroupTotalsStore | null>(
  null,
);

/**
 * Publishes one group's live MTM/Carry sum into the ambient store. No-op when
 * there's no provider (keeps `OpenPositionsTable` usable in isolation / tests).
 * The update and the unmount-cleanup are split so a ticking value only ever
 * `set`s — it never churns a remove+set pair that would double-emit.
 */
export function useReportGroupLiveTotals(
  groupKey: string,
  mtm: number | null,
  carry: number | null,
): void {
  const store = useContext(LiveGroupTotalsContext);
  useEffect(() => {
    store?.set(groupKey, { mtm, carry });
  }, [store, groupKey, mtm, carry]);
  useEffect(() => {
    if (!store) return;
    return () => store.remove(groupKey);
  }, [store, groupKey]);
}

/** The full per-group live-totals map, re-rendering the caller whenever it changes. */
export function useLiveGroupTotals(): ReadonlyMap<string, GroupLiveTotal> {
  const store = useContext(LiveGroupTotalsContext);
  return useSyncExternalStore(
    store?.subscribe ?? NOOP_SUBSCRIBE,
    store?.getSnapshot ?? (() => EMPTY_MAP),
    () => EMPTY_MAP,
  );
}
