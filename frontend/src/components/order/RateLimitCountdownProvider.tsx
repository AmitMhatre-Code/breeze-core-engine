"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { RateLimitPauseOverlay } from "@/components/order/RateLimitPauseOverlay";
import { apiClient } from "@/lib/api-client";
import { shouldFetchLicenseHomeData } from "@/lib/public-auth-routes";

type RateLimitCountdownContextValue = {
  secondsRemaining: number | null;
  wait: (total: number, reason?: string) => Promise<void>;
};

type IciciPacingStatus = {
  throttling_active: boolean;
  backing_off: boolean;
  reason: string | null;
  seconds_remaining: number;
};

type OverlayState = {
  secondsRemaining: number;
  reason?: string;
};

const RateLimitCountdownContext =
  createContext<RateLimitCountdownContextValue | null>(null);

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function RateLimitCountdownProvider({ children }: { children: ReactNode }) {
  const [clientOverlay, setClientOverlay] = useState<OverlayState | null>(null);
  const pathname = usePathname();
  const pacingPollEnabled = shouldFetchLicenseHomeData(pathname);

  const pacingQ = useQuery({
    queryKey: ["icici", "pacing-status"],
    queryFn: () =>
      apiClient.get<IciciPacingStatus>("/api/icici/pacing-status", {
        sessionPolicy: "passive",
      }),
    enabled: pacingPollEnabled,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.backing_off && (data.seconds_remaining ?? 0) > 0) {
        return 500;
      }
      return 5000;
    },
    retry: false,
    refetchOnWindowFocus: true,
  });

  const serverOverlay = useMemo((): OverlayState | null => {
    const data = pacingQ.data;
    if (!data?.backing_off) return null;
    const sec = Math.floor(Number(data.seconds_remaining) || 0);
    if (sec <= 1) return null;
    return {
      secondsRemaining: sec,
      reason: data.reason?.trim() || undefined,
    };
  }, [pacingQ.data]);

  const activeOverlay = useMemo((): OverlayState | null => {
    if (!clientOverlay && !serverOverlay) return null;
    if (!clientOverlay) return serverOverlay;
    if (!serverOverlay) return clientOverlay;
    return clientOverlay.secondsRemaining >= serverOverlay.secondsRemaining
      ? clientOverlay
      : serverOverlay;
  }, [clientOverlay, serverOverlay]);

  const wait = useCallback(async (total: number, reason?: string) => {
    const n = Math.max(1, Math.floor(total));
    const showOverlay = n > 1;
    for (let i = n; i > 0; i--) {
      if (showOverlay) {
        setClientOverlay({ secondsRemaining: i, reason });
      }
      await sleepMs(1000);
    }
    if (showOverlay) {
      setClientOverlay(null);
    }
  }, []);

  const value = useMemo(
    () => ({
      secondsRemaining: activeOverlay?.secondsRemaining ?? null,
      wait,
    }),
    [activeOverlay?.secondsRemaining, wait],
  );

  return (
    <RateLimitCountdownContext.Provider value={value}>
      {activeOverlay ? (
        <RateLimitPauseOverlay
          secondsRemaining={activeOverlay.secondsRemaining}
          reason={activeOverlay.reason}
        />
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
