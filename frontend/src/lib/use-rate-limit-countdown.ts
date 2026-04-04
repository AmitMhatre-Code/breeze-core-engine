"use client";

import { useCallback, useState } from "react";

/** Drives a 1-second tick countdown for ICICI 429 backoff (used with RateLimitPauseOverlay). */
export function useRateLimitCountdown() {
  const [secondsRemaining, setSecondsRemaining] = useState<number | null>(null);

  const wait = useCallback(async (total: number) => {
    const n = Math.max(1, Math.floor(total));
    for (let i = n; i > 0; i--) {
      setSecondsRemaining(i);
      await new Promise((r) => setTimeout(r, 1000));
    }
    setSecondsRemaining(null);
  }, []);

  return { secondsRemaining, wait };
}
