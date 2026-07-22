"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { releaseWsSubscriptionHolder } from "@/lib/ws-subscription-api";

function newHolderId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `holder-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function storageKey(pathname: string): string {
  return `ws-subscription-holder:${pathname}`;
}

/**
 * Stable per-page subscription holder, persisted in sessionStorage (keyed by route) so a
 * full page reload reuses the same holder id -- and the backend's already-warm chain
 * subscriptions for it -- instead of cold-starting under a brand new id every time a wide
 * chain (e.g. NIFTY) is opened. On unmount (SPA navigation away from this route), releases
 * ICICI feed subscriptions without disconnecting the shared WebSocket. The sessionStorage
 * entry is intentionally left in place rather than removed here: React's dev-mode Strict
 * Mode double-invokes effect cleanup on every mount (mount -> cleanup -> mount again) even
 * though the component never really unmounted, which would otherwise wipe the entry it just
 * wrote. Leaving a released id in sessionStorage is harmless -- the backend has already
 * forgotten it, so a later visit reusing it just triggers an ordinary cold resubscribe under
 * the same id, identical to minting a fresh one.
 */
export function useWsSubscriptionHolder(): string {
  const pathname = usePathname() ?? "";
  // Lazy useState initializer (not useRef) -- it runs exactly once on mount and its
  // result is plain state, so reading it during render doesn't trip the "no ref access
  // during render" rule the way a lazily-populated ref would.
  const [holder] = useState(() => {
    const key = storageKey(pathname);
    if (typeof window === "undefined") {
      return { id: newHolderId(), key };
    }
    const existing = window.sessionStorage.getItem(key);
    const id = existing || newHolderId();
    window.sessionStorage.setItem(key, id);
    return { id, key };
  });

  useEffect(() => {
    return () => {
      releaseWsSubscriptionHolder(holder.id);
    };
  }, [holder]);

  return holder.id;
}
