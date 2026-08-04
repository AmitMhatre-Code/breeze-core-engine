"use client";

import { useEffect, useState } from "react";
import { isBhavcopyStale, isLiveQuoteSource } from "@/lib/quote-source";
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

/** How long a selection can sit on stale Bhavcopy data, animating as "still
 * building," before the badge gives up animating and settles into a static
 * state with a manual refresh affordance instead. The background chain
 * builder keeps retrying regardless — this only controls what the badge
 * implies while it waits. */
export const CHAIN_BUILD_GIVE_UP_MS = 60_000;

export type ChainBuildPhase = "live" | "building" | "gave_up" | "other";

/**
 * Classifies the current chain's freshness for badge rendering. `buildKey`
 * identifies the selection (e.g. `${exchange}:${stock}:${expiry}`) so the
 * "how long have we been stuck" clock resets when the user picks something
 * else — `bhavcopy_date` alone wouldn't change for that, since it's the same
 * EOD file for every symbol on a given day.
 */
export function useChainBuildPhase(
  meta: QuoteMeta | null | undefined,
  buildKey: string,
): ChainBuildPhase {
  const stale = isBhavcopyStale(meta);
  const [build, setBuild] = useState<{ key: string; startedAt: number } | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (!stale) {
      setBuild(null);
      return;
    }
    setBuild((prev) => (prev?.key === buildKey ? prev : { key: buildKey, startedAt: Date.now() }));
  }, [stale, buildKey]);

  useEffect(() => {
    if (!stale) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [stale, buildKey]);

  if (!meta) return "other";
  if (meta.quote_source === "websocket") return "live";
  if (!stale) return "other";
  // First render right after transitioning into "stale" — the effect above hasn't
  // committed a start time for this buildKey yet, so treat it as freshly building
  // rather than momentarily flashing "gave up".
  if (!build || build.key !== buildKey) return "building";
  const elapsed = nowMs - build.startedAt;
  return elapsed < CHAIN_BUILD_GIVE_UP_MS ? "building" : "gave_up";
}
