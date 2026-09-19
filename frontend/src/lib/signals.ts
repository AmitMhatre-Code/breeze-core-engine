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
  rough_pnl_one_lot_rupees: number | null;
  mean_move_bps: number | null;
  withdrawn_early: number;
  directional_share: number | null;
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
  series: Record<string, { calls: number | null; hit_rate: number | null; verdict: string | null }>;
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

function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`;
}

/** One sentence a layman can read: what the last backtest says about this series. */
export function backtestSentence(s: SeriesBacktestSummary | null, duration: number): string {
  if (!s) return "Not backtested yet.";
  if (!s.sessions_replayed) return "No data in the last backtest.";
  if (!s.calls) return `No calls in ${s.sessions_replayed} sessions.`;
  const decided = s.right + s.wrong;
  const head = `${s.calls} calls over ${s.sessions_replayed} sessions`;
  if (!decided) return `${head}; none moved far enough to pay for a trade within ${duration} min.`;
  const verdict = {
    edge: "better than the market's own trend",
    worse: "worse than the market's own trend",
    no_edge: "no better than the market's own trend",
    too_few_calls: "too few calls to judge yet",
  }[s.verdict];
  return `${head}; right ${pct(s.hit_rate)} of the time after costs — ${verdict}.`;
}

export function verdictTone(v: SeriesBacktestSummary["verdict"] | undefined): "up" | "down" | "muted" {
  if (v === "edge") return "up";
  if (v === "worse") return "down";
  return "muted";
}
