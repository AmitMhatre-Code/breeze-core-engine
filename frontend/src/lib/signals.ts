import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { BacktestPeriod } from "@/lib/bots-backtest";

/** The Signals page (docs/signals-streamline-plan.md sections 4–6). Every signal is a cell of a
 *  fixed grid — mechanism × duration × index — and a pure function of the one-minute futures
 *  bars ICICI's history serves, so every live reading can be backtested. */

export type SignalMechanism = "expansion" | "momentum";
export type SignalDuration = 1 | 5 | 15;
export type SignalDirection = "follow" | "fade";
export type SignalIndex = "nifty" | "sensex";
export type SignalState = "bullish" | "bearish" | "neutral" | "unavailable";

export const MECHANISM_LABEL: Record<SignalMechanism, string> = {
  expansion: "Volume expansion",
  momentum: "Momentum",
};
export const DURATIONS: SignalDuration[] = [1, 5, 15];
export const INDEX_LABEL: Record<SignalIndex, string> = { nifty: "NIFTY", sensex: "SENSEX" };

export type SignalReading = {
  state: SignalState;
  reason: string | null;
  signal: number | null;
  call_started_at: number | null;
  held_until: number | null;
  computed_at: number | null;
  thin_data?: boolean;
  uses_oi?: boolean;
};

export type SideVerdict = {
  hit_rate: number | null;
  trend_share: number | null;
  verdict: "better" | "worse" | "no_edge" | "too_few_calls" | "no_calls";
  separate_calls: number;
};

/** One horizon, one direction: what a trade taken on every call would have averaged. */
export type HorizonScore = {
  calls: number;
  days: number;
  right: number;
  wrong: number;
  hit_rate: number | null;
  mean_move_bps: number | null;
  /** The average move once the round trip is paid. Positive means it made money. */
  net_bps: number | null;
  /** How many times its own day-to-day scatter that average is. ~2+ means it stands out. */
  t: number | null;
  stands_out: boolean;
  verdict: "pays" | "unclear" | "loses" | "not_enough_days";
};

export type BestHorizon = HorizonScore & {
  horizon_minutes: number;
  direction: SignalDirection;
};

export type BreakevenDetail = {
  bps: number | null;
  charges_bps: number | null;
  spread_bps: number | null;
  lots: number | null;
  lot_size: number | null;
  spread_source: string | null;
  premium: number | null;
};

export type SeriesBacktestSummary = {
  sessions_replayed: number;
  calls: number;
  bullish_calls: number;
  bearish_calls: number;
  right: number;
  wrong: number;
  hit_rate: number | null;
  verdict: "edge" | "worse" | "no_edge" | "too_few_calls";
  sides: Record<"bullish" | "bearish", SideVerdict>;
  breakeven_bps: number | null;
  rough_pnl_rupees: number | null;
  mean_move_bps: number | null;
  withdrawn_early: number;
  directional_share: number | null;
  /** Keyed by minutes ("1" | "5" | "15" | "30"), each with both directions. */
  horizons: Record<string, Record<SignalDirection, HorizonScore>> | null;
  best: BestHorizon | null;
  tradeable: boolean;
  breakeven: BreakevenDetail | null;
};

export type SignalSeries = {
  id: string;
  mechanism: SignalMechanism;
  duration: SignalDuration;
  index: SignalIndex;
  index_name: string;
  version: number;
  uses_oi: boolean;
  thin_data: boolean;
  name: string;
  reading: SignalReading;
  last_backtest: SeriesBacktestSummary | null;
};

export type MechanismAvailability = {
  available: boolean;
  reason: string | null;
  run_id: string | null;
  from: string | null;
  to: string | null;
  range_days: number | null;
  longest_days: number;
  version: number;
};

export type MechanismSection = {
  id: SignalMechanism;
  name: string;
  summary: string;
  version: number;
  availability: MechanismAvailability;
  series: SignalSeries[];
};

export type SignalJob = {
  id: string;
  kind: string;
  status: "running" | "completed" | "failed" | "stopped";
  started_at: string;
  finished_at: string | null;
  message: string | null;
  error: string | null;
  calls: number;
  log: string[];
  running?: boolean;
  from_date?: string;
  to_date?: string;
};

export type SignalsOverview = {
  mechanisms: MechanismSection[];
  navbar_mechanism: SignalMechanism;
  navbar_duration: number;
  gate_days: number;
  cost_lots: number;
  max_cost_lots: number;
  last_backtest: { id: string; from: string; to: string; range_days: number; finished_at: string | null } | null;
  job: SignalJob | null;
};

export type SignalBacktestRun = {
  id: string;
  triggered_at: string;
  finished_at: string | null;
  period: BacktestPeriod;
  from: string;
  to: string;
  range_days: number | null;
  status: "running" | "completed" | "failed" | "stopped";
  error: string | null;
  notes: string[];
  calls: number;
  has_zip: boolean;
  counts_for_gate: boolean;
  series: Record<string, {
    calls: number | null;
    hit_rate: number | null;
    verdict: string | null;
    tradeable?: boolean;
    best_direction?: SignalDirection | null;
    best_net_bps?: number | null;
  }>;
};

export const SIGNALS_QUERY_KEY = ["signals"] as const;
export const SIGNAL_RUNS_QUERY_KEY = ["signals", "runs"] as const;
const RUNS_QUERY_KEY = SIGNAL_RUNS_QUERY_KEY;

function jobRunning(job: SignalJob | null | undefined): boolean {
  return Boolean(job && (job.running ?? job.status === "running"));
}

export function useSignals() {
  return useQuery({
    queryKey: SIGNALS_QUERY_KEY,
    queryFn: ({ signal }) => apiClient.get<SignalsOverview>("/api/signals", signal),
    // Readings change on the minute; a running backtest reports progress faster.
    refetchInterval: (q) => (jobRunning(q.state.data?.job) ? 2_000 : 10_000),
  });
}

export function useSignalBacktestRuns(running: boolean) {
  return useQuery({
    queryKey: RUNS_QUERY_KEY,
    queryFn: ({ signal }) =>
      apiClient.get<{ runs: SignalBacktestRun[]; gate_days: number }>("/api/signals/backtest/runs", signal),
    refetchInterval: running ? 3_000 : 30_000,
  });
}

export function useSetNavbarMechanism() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mechanism: SignalMechanism) =>
      apiClient.put<{ navbar_mechanism: SignalMechanism }>("/api/signals/navbar", { mechanism }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SIGNALS_QUERY_KEY });
      void qc.invalidateQueries({ queryKey: ["dashboard", "index-quotes"] });
    },
  });
}

export function useSetCostLots() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (lots: number) =>
      apiClient.put<{ cost_lots: number }>("/api/signals/cost-lots", { lots }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: SIGNALS_QUERY_KEY }),
  });
}

export function useStartSignalBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { period: BacktestPeriod; from_date?: string; to_date?: string }) =>
      apiClient.post<SignalJob>("/api/signals/backtest", body),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: SIGNALS_QUERY_KEY });
      void qc.invalidateQueries({ queryKey: RUNS_QUERY_KEY });
    },
  });
}

export function useCancelSignalBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<{ stopping: boolean }>("/api/signals/backtest/cancel", {}),
    onSettled: () => void qc.invalidateQueries({ queryKey: SIGNALS_QUERY_KEY }),
  });
}

export function signalZipHref(runId: string): string {
  return `/api/signals/backtest/runs/${encodeURIComponent(runId)}/zip`;
}

// --------------------------------------------------------------------------------------
// Plain-language copy — the page is for a layman (decision 4)
// --------------------------------------------------------------------------------------

export function stateLabel(state: SignalState): string {
  return { bullish: "Bullish", bearish: "Bearish", neutral: "Quiet", unavailable: "No reading" }[state];
}

const REASON_TEXT: Record<string, string> = {
  market_closed: "market closed",
  outside_session: "outside 09:15–15:15",
  warming_up: "warming up",
  no_bars: "no data yet today",
  stale: "data feed paused",
  excluded_session: "rollover day",
  no_open_interest: "no open-interest data",
  volume_unavailable: "volume unknown",
  anchor_not_traded: "nothing traded to measure from",
  vwap_unavailable: "no average price yet",
  not_published: "not running",
  no_expansion: "nothing unusual",
  unwind: "positions closing, not opening",
  volume_below_threshold: "trading too light",
  no_confluence: "no clear trend",
};

export function reasonText(reason: string | null | undefined): string {
  if (!reason) return "";
  return REASON_TEXT[reason] ?? reason.replace(/_/g, " ");
}

function bps(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${x > 0 ? "+" : ""}${x.toFixed(2)} bps`;
}

const DIRECTION_PHRASE: Record<SignalDirection, string> = {
  follow: "trading with it",
  fade: "trading against it",
};

/**
 * One sentence a layman can read: what the last backtest says about this series.
 *
 * It reports the best of the horizons in BOTH directions, because neither "at its own duration"
 * nor "with the signal" is privileged. A signal that is reliably wrong is a finding — it is
 * traded backwards — and the old sentence buried that under "worse than the market's own trend",
 * which reads like a failure.
 */
export function backtestSentence(s: SeriesBacktestSummary | null, duration: number): string {
  if (!s) return "Not backtested yet.";
  if (!s.sessions_replayed) return "No data in the last backtest.";
  if (!s.calls) return `No calls in ${s.sessions_replayed} sessions.`;
  const head = `${s.calls} calls over ${s.sessions_replayed} sessions`;
  const best = s.best;
  if (!best || best.net_bps === null) {
    return `${head}; none of them moved far enough to pay for a trade.`;
  }
  const how = DIRECTION_PHRASE[best.direction];
  const when = best.horizon_minutes === duration
    ? `over the ${duration} min it stands`
    : `held ${best.horizon_minutes} min rather than ${duration}`;
  if (best.verdict === "not_enough_days") {
    return `${head}; too few sessions to say anything yet.`;
  }
  if (best.verdict === "pays") {
    return `${head}. Best was ${how}, ${when}: ${bps(best.net_bps)} a call after costs, `
      + `which stands out against the day-to-day swings.`;
  }
  if (best.verdict === "unclear") {
    return `${head}. The best it managed was ${how}, ${when}: ${bps(best.net_bps)} a call after `
      + `costs — but that is inside the normal day-to-day swings, so it may be luck.`;
  }
  return `${head}; nothing paid for its costs, either with the signal or against it `
    + `(best ${bps(best.net_bps)} a call).`;
}

/** The second line: where the money was, direction by direction, at the best horizon. */
export function horizonDetail(s: SeriesBacktestSummary | null): string | null {
  if (!s?.horizons || !s.best) return null;
  const row = s.horizons[String(s.best.horizon_minutes)];
  if (!row) return null;
  const parts = (["follow", "fade"] as SignalDirection[]).map((d) => {
    const h = row[d];
    return `${d === "follow" ? "with" : "against"} ${bps(h?.net_bps)}`;
  });
  const be = s.breakeven;
  const bar = be?.bps
    ? ` Costs ${be.bps.toFixed(2)} bps at ${be.lots ?? 1} lot${(be.lots ?? 1) === 1 ? "" : "s"}`
      + (be.charges_bps !== null && be.spread_bps !== null
        ? ` (${be.charges_bps.toFixed(2)} charges, ${be.spread_bps.toFixed(2)} spread).`
        : ".")
    : "";
  return `At ${s.best.horizon_minutes} min: ${parts.join(", ")} a call after costs.${bar}`;
}

export function verdictTone(
  best: BestHorizon | null | undefined,
): "up" | "down" | "muted" {
  if (!best || best.net_bps === null) return "muted";
  if (best.verdict === "pays") return "up";
  if (best.verdict === "loses") return "down";
  return "muted";
}
