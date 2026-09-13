import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";

/** Bots → Backtest (docs/bots-scalping-plan.md section 8.11). */

export type BacktestBot = "momentum" | "fly" | "expiry";

export const BACKTEST_BOTS: ReadonlyArray<{ value: BacktestBot; label: string }> = [
  { value: "momentum", label: "Bot 3 · Momentum scalper" },
  { value: "fly", label: "Bot 4 · Iron fly" },
  { value: "expiry", label: "Bot 2 · Expiry-day writer" },
];

export type BacktestJob = {
  id: string;
  kind: "probe" | "fetch" | "replay" | string;
  status: "running" | "completed" | "failed" | "stopped" | string;
  running: boolean;
  started_at: string;
  finished_at: string | null;
  message: string | null;
  error: string | null;
  calls: number;
  log: string[];
  bot?: BacktestBot;
  from_date?: string;
  to_date?: string;
  run_id?: string;
};

export type CoverageSpan = { days: number; from: string | null; to: string | null; short_days: number };

export type BacktestCoverage = {
  nifty_futures: CoverageSpan;
  nifty_index: CoverageSpan;
  sensex_index: CoverageSpan;
  vix: { days: number; from: string | null; to: string | null };
  option_contracts: number;
  option_bars: number;
  option_windows_empty: number;
  option_needs_pending: number;
};

export type SavedSummary = { label: string; lines: string[]; enabled?: boolean };

export type BacktestSummary = Record<string, unknown>;

export type BacktestRunListItem = {
  id: string;
  bot: BacktestBot;
  created_at: string;
  status: "running" | "completed" | "failed" | string;
  params: {
    label?: string;
    from?: string;
    to?: string;
    model?: boolean;
    sizing?: string;
  };
  summary: BacktestSummary | null;
  error: string | null;
};

export type BacktestTrade = Record<string, unknown> & {
  net_pnl: number;
  gross_pnl: number;
  friction: number;
};

export type BacktestRun = BacktestRunListItem & { trades: BacktestTrade[] };

export type ProbeOption = {
  expiry?: string;
  day?: string;
  contract?: string;
  minute_bars?: number;
  verdict?: string;
  request_clock?: string;
};

export type BacktestProbe = {
  per_call_cap?: number | string;
  expired_nifty_weekly?: ProbeOption;
  history_start_nifty_weekly?: ProbeOption;
  expired_sensex_weekly?: ProbeOption;
} & Record<string, unknown>;

export type BacktestOverview = {
  broker_mode: string;
  live: boolean;
  market_hours_block: string | null;
  history_start: string;
  default_to: string;
  job: BacktestJob | null;
  probe: BacktestProbe | null;
  probed_at: string | null;
  coverage: BacktestCoverage;
  saved: Record<BacktestBot, SavedSummary>;
  runs: BacktestRunListItem[];
};

export type ComparePair = {
  contract: string;
  paper_at: string;
  backtest_at: string;
  paper_entry: number | null;
  backtest_entry: number | null;
  entry_diff: number | null;
  paper_exit: string;
  backtest_exit: string;
  paper_net: number;
  backtest_net: number;
};

export type CompareTotals = { cycles: number; gross_pnl: number; friction: number; net_pnl: number };

export type ComparePayload = {
  day: string;
  price_source: string;
  settings_changed: boolean;
  lots: number | null;
  paper: CompareTotals;
  backtest: CompareTotals;
  pairs: ComparePair[];
  paper_only: string[];
  backtest_only: string[];
  median_abs_entry_diff: number | null;
};

export type RangeBody = { bot: BacktestBot; from_date: string; to_date: string };

export const fetchBacktestOverview = () => apiClient.get<BacktestOverview>("/bots/backtest/overview");
export const startBacktestProbe = () => apiClient.post<BacktestJob>("/bots/backtest/probe", {});
export const startBacktestFetch = (body: RangeBody) => apiClient.post<BacktestJob>("/bots/backtest/fetch", body);
export const startBacktestReplay = (body: RangeBody & { model: boolean }) =>
  apiClient.post<BacktestJob>("/bots/backtest/replay", body);
export const cancelBacktestJob = () => apiClient.post<{ cancelled: boolean }>("/bots/backtest/cancel", {});
export const fetchBacktestRun = (id: string) =>
  apiClient.get<BacktestRun>(`/bots/backtest/run?id=${encodeURIComponent(id)}`);
export const deleteBacktestRun = (id: string) =>
  apiClient.delete<{ deleted: boolean }>(`/bots/backtest/run?id=${encodeURIComponent(id)}`);
export const fetchBacktestCompare = (bot: "momentum" | "fly", date: string, model: boolean) =>
  apiClient.get<ComparePayload>(
    `/bots/backtest/compare?bot=${bot}&date=${encodeURIComponent(date)}&model=${model}`,
  );

export async function downloadBacktestCsv(id: string): Promise<void> {
  const url = new URL(`/bots/backtest/run/csv?id=${encodeURIComponent(id)}`, getBackendBaseUrl());
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) throw new Error((await res.text()) || "Download failed");
  const blob = await res.blob();
  const match = /filename="?([^";\n]+)"?/i.exec(res.headers.get("content-disposition") ?? "");
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = match?.[1] ?? `backtest-${id.slice(0, 8)}.csv`;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

// --- derived numbers --------------------------------------------------------------------

export function num(value: unknown): number | null {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export type RunTotals = { trades: number; net: number; gross: number; friction: number; winRate: number | null };

/** Totals from the trades themselves, so all three bots read the same way. */
export function tradeTotals(trades: ReadonlyArray<BacktestTrade>): RunTotals {
  let net = 0;
  let gross = 0;
  let friction = 0;
  let wins = 0;
  for (const t of trades) {
    net += num(t.net_pnl) ?? 0;
    gross += num(t.gross_pnl) ?? 0;
    friction += num(t.friction) ?? 0;
    if ((num(t.net_pnl) ?? 0) > 0) wins += 1;
  }
  return {
    trades: trades.length,
    net: round2(net),
    gross: round2(gross),
    friction: round2(friction),
    winRate: trades.length ? round2((100 * wins) / trades.length) : null,
  };
}

/** Headline for the runs list, which carries only the summary (no trades). */
export function summaryTotals(summary: BacktestSummary | null): { trades: number | null; net: number | null } {
  if (!summary) return { trades: null, net: null };
  if (summary.net_pnl !== undefined) {
    return { trades: num(summary.cycles), net: num(summary.net_pnl) };
  }
  const byStrategy = summary.by_strategy as Record<string, { trades?: number; net_pnl?: number }> | undefined;
  if (!byStrategy) return { trades: null, net: null };
  let trades = 0;
  let net = 0;
  for (const s of Object.values(byStrategy)) {
    trades += s.trades ?? 0;
    net += s.net_pnl ?? 0;
  }
  return { trades, net: round2(net) };
}

/** When each trade's P&L landed: exits for the scalpers, the expiry day for Bot 2. */
export function tradeClosedAt(t: BacktestTrade): string {
  return String(t.exited_at ?? t.day ?? t.entered_at ?? "");
}

export type EquityPoint = { at: string; cumulative: number };

export function equityCurve(trades: ReadonlyArray<BacktestTrade>): EquityPoint[] {
  const sorted = [...trades].sort((a, b) => tradeClosedAt(a).localeCompare(tradeClosedAt(b)));
  let running = 0;
  return sorted.map((t) => {
    running += num(t.net_pnl) ?? 0;
    return { at: tradeClosedAt(t), cumulative: round2(running) };
  });
}

/** Deepest fall from a running peak of the equity curve, as a positive rupee figure. */
export function maxDrawdown(points: ReadonlyArray<EquityPoint>): number {
  let peak = 0;
  let worst = 0;
  for (const p of points) {
    peak = Math.max(peak, p.cumulative);
    worst = Math.max(worst, peak - p.cumulative);
  }
  return round2(worst);
}

const EXIT_LABELS: Record<string, string> = {
  square_off: "Square-off",
  trailing_stop: "Trailing stop",
  stop_loss: "Stop-loss",
  time_invalidation: "Time stop",
  credit_decay_target: "Decay target",
  drift_stop: "Drift stop",
  terminated_for_day: "Daily loss cap",
  stale_feed: "Stale feed",
  session_end: "Session end",
  group_stop_loss_hit: "Loss stop",
  group_target_hit: "Profit target",
  expired: "Expired",
};

export function exitLabel(code: unknown): string {
  const key = String(code ?? "");
  return EXIT_LABELS[key] ?? key.replace(/_/g, " ");
}

export function inr(value: number | null | undefined, digits = 0): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const body = Math.abs(value).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${value < 0 ? "−" : ""}₹${body}`;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
