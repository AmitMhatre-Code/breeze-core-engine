"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { RateLimitPauseOverlay } from "@/components/order/RateLimitPauseOverlay";

type RateLimitCountdownContextValue = {
  secondsRemaining: number | null;
  wait: (total: number) => Promise<void>;
};

const RateLimitCountdownContext =
  createContext<RateLimitCountdownContextValue | null>(null);

export function RateLimitCountdownProvider({ children }: { children: ReactNode }) {
  const [secondsRemaining, setSecondsRemaining] = useState<number | null>(null);

  const wait = useCallback(async (total: number) => {
    const n = Math.max(0, Math.floor(total));
    if (n === 0) {
      return;
    }
    for (let i = n; i > 0; i--) {
      setSecondsRemaining(i);
      await new Promise((r) => setTimeout(r, 1000));
    }
    setSecondsRemaining(null);
  }, []);

  const value = useMemo(
    () => ({ secondsRemaining, wait }),
    [secondsRemaining, wait],
  );

  return (
    <RateLimitCountdownContext.Provider value={value}>
      {secondsRemaining !== null ? (
        <RateLimitPauseOverlay secondsRemaining={secondsRemaining} />
      ) : null}
      {children}
    </RateLimitCountdownContext.Provider>
  );
}

export function useRateLimitCountdownContext(): RateLimitCountdownContextValue {
  const ctx = useContext(RateLimitCountdownContext);
  if (!ctx) {
    throw new Error(
      "useRateLimitCountdown must be used within RateLimitCountdownProvider",
    );
  }
  return ctx;
}
