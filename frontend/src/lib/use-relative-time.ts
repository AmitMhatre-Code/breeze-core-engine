"use client";

import { useEffect, useState } from "react";
import { isLiveQuoteSource } from "@/lib/quote-source";
import type { QuoteMeta } from "@/lib/strategy-builder/types";

/** Tick every second while live WebSocket quotes need relative age display. */
export function useRelativeTime(meta: QuoteMeta | null | undefined): number {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (!isLiveQuoteSource(meta)) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [meta?.quote_source, meta?.quote_as_of]);

  return nowMs;
}
