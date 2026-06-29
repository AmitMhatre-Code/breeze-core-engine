"use client";

import { useEffect, useRef } from "react";
import { releaseWsSubscriptionHolder } from "@/lib/ws-subscription-api";

function newHolderId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `holder-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Stable per-page subscription holder. On unmount, releases ICICI feed subscriptions
 * without disconnecting the shared WebSocket.
 */
export function useWsSubscriptionHolder(): string {
  const holderRef = useRef<string | null>(null);
  if (holderRef.current === null) {
    holderRef.current = newHolderId();
  }

  useEffect(() => {
    const holderId = holderRef.current!;
    return () => {
      releaseWsSubscriptionHolder(holderId);
    };
  }, []);

  return holderRef.current;
}
