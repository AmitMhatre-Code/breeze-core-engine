"use client";

import { useCallback, useEffect, useRef } from "react";
import { releaseWsSubscriptionHolder } from "@/lib/ws-subscription-api";

function newHolderId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `holder-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Stable per-group-key WS subscription holder ids, shared by every component that needs
 * live quotes for the same expanded group (payoff chart + hedge panel both read the same
 * chain). A group's holder is created lazily the first time `getHolderId` sees that key,
 * and released as soon as the group collapses — mirrors `useWsSubscriptionHolder`, but
 * keyed per group instead of one id for the whole page.
 */
export function useGroupSubscriptionHolders() {
  const holdersRef = useRef<Map<string, string>>(new Map());

  const getHolderId = useCallback((groupKey: string): string => {
    const existing = holdersRef.current.get(groupKey);
    if (existing) return existing;
    const id = newHolderId();
    holdersRef.current.set(groupKey, id);
    return id;
  }, []);

  const releaseGroup = useCallback((groupKey: string) => {
    const id = holdersRef.current.get(groupKey);
    if (!id) return;
    holdersRef.current.delete(groupKey);
    releaseWsSubscriptionHolder(id);
  }, []);

  useEffect(() => {
    const holders = holdersRef.current;
    return () => {
      for (const id of holders.values()) {
        releaseWsSubscriptionHolder(id);
      }
      holders.clear();
    };
  }, []);

  return { getHolderId, releaseGroup };
}
