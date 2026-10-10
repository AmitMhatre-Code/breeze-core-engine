import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";
import {
  BOT_CAS_BINGO,
  BOT_DYNAMIC_CONDOR,
  BOT_EXPIRY_INDEX_WRITER,
  BOT_IRON_FLY_SCALPER,
  BOT_MOMENTUM_LONG_SCALPER,
  type BotType,
} from "@/lib/use-bots";

/** Bot backtests: started from the clock on each card, recorded in Activity (design-decisions #35, #36). */

export type BacktestBot = "momentum" | "fly" | "expiry" | "cas" | "condor";

/** Which bots have a replay, and what the API calls each one.
 *
 *  The Holdings Writer has none, so its card carries no backtest icon at all: an entry point
 *  that leads to "this bot cannot be backtested" is worse than no entry point. */
export const BACKTEST_SLUG: Partial<Record<BotType, BacktestBot>> = {
  [BOT_MOMENTUM_LONG_SCALPER]: "momentum",
  [BOT_IRON_FLY_SCALPER]: "fly",
  [BOT_EXPIRY_INDEX_WRITER]: "expiry",
  [BOT_CAS_BINGO]: "cas",
  [BOT_DYNAMIC_CONDOR]: "condor",
};

export const BACKTEST_BOT_TYPE: Record<BacktestBot, BotType> = {
  momentum: BOT_MOMENTUM_LONG_SCALPER,
  fly: BOT_IRON_FLY_SCALPER,
  expiry: BOT_EXPIRY_INDEX_WRITER,
  cas: BOT_CAS_BINGO,
  condor: BOT_DYNAMIC_CONDOR,
};

/** One signal setting's line in a bot backtest's comparison (docs/signals-streamline-plan.md 8):
 *  every setting the bot could trade on, replayed with its other settings as saved. */
export type ComparisonRow = {
  id: string;
  label: string;
  signal: { mechanism: string; version?: number; duration: number; direction?: string } | null;
  is_saved: boolean;
  trades: number;
  win_rate_pct: number | null;
  gross_pnl: number | null;
  friction: number | null;
  net_pnl: number | null;
  max_drawdown: number | null;
  worst_day: number | null;
  best_day: number | null;
  days_replayed: number | null;
  days_awaiting_data: number | null;
};

/** One settings combination's line in a condor backtest's comparison (design-decisions #71):
 *  the five settings it sets, every other one as saved. */
export type CondorComparisonRow = {
  id: string;
  label: string;
  varied: {
    net_delta_band_per_lot: number;
    min_roll_credit_points: number;
    max_loss: string;
    no_roll_within_days_of_exit: number;
    exit_action: "time_roll" | "close";
    /** "off" or the threshold ("1.00x"). Absent on runs from before #78. */
    premium_gate?: string;
    /** "off" or "0.30Δ to 0.20Δ" (trigger to landing). Absent on runs from before #80. */
    recentre?: string;
  };
  is_saved: boolean;
  trades: number;
  rolls: number;
  win_rate_pct: number | null;
  net_pnl: number | null;
  max_drawdown: number | null;
  worst_at_check: number | null;
  complete: boolean;
  stopped_at: string | null;
};

export function condorComparisonRows(summary: BacktestSummary | null | undefined): CondorComparisonRow[] {
  return comparisonRows(summary) as unknown as CondorComparisonRow[];
}

export function comparisonRows(summary: BacktestSummary | null | undefined): ComparisonRow[] {
  const rows = (summary as { comparison?: unknown } | null | undefined)?.comparison;
  return Array.isArray(rows) ? (rows as ComparisonRow[]) : [];
}

/** The card dialog's only question (#36). */
export type BacktestPeriod = "last_day" | "last_week" | "last_month" | "custom";

export const BACKTEST_PERIODS: ReadonlyArray<{ value: BacktestPeriod; label: string }> = [
  { value: "last_day", label: "Last trading day" },
  { value: "last_week", label: "Last trading week" },
  { value: "last_month", label: "Last trading month" },
  { value: "custom", label: "Custom range" },
];

export type BacktestStartBody = {
  bot: BacktestBot;
  period: BacktestPeriod;
  from_date?: string;
  to_date?: string;
  /** The Dynamic Iron Condor only: ICICI's intraday prices, or NSE's daily closes from 2020
   *  (docs/condor-daily-history-plan.md). */
  prices?: BacktestPrices;
};

export type BacktestPrices = "icici" | "nse_daily";

/** The first session of the condor's NSE daily-price history. */
export const DAILY_HISTORY_START = "2020-01-01";

export type BacktestJobStatus = {
  job: BacktestJob | null;
  /** The container's memory, or null where it cannot be read (a dev machine). */
  memory?: { held_bytes: number; cap_bytes: number; stop_at: number } | null;
  budget: { daily_calls: number; spent_today: number; remaining_today: number };
  market_hours_block: string | null;
  live: boolean;
};

/** One key for the single backtest job, shared by the card's dialog and Activity's progress panel. */
export const BACKTEST_JOB_KEY = ["bots", "backtest", "job"] as const;

export const startBotBacktest = (body: BacktestStartBody) =>
  apiClient.post<BacktestJob>("/bots/backtest/start", body);
export const fetchBacktestJobStatus = () => apiClient.get<BacktestJobStatus>("/bots/backtest/job");
export const cancelBacktestJob = () => apiClient.post<{ cancelled: boolean }>("/bots/backtest/cancel", {});

/** A replay's own trail: one file for the whole run, not the live day's file (#35). */
export function backtestAuditHref(name: string): string {
  return `/api/settings/bot-audit-logs/backtest/${encodeURIComponent(name)}/download`;
}

export type BacktestJob = {
  id: string;
  kind: "backtest" | "probe" | "fetch" | "replay" | string;
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
  period?: BacktestPeriod;
  /** Where the job is. Absent from a server older than the progress panel. */
  phase?: BacktestPhase;
  step?: number | null;
  steps?: number | null;
  /** The session being replayed, `YYYY-MM-DD`; null while a setting loads its prices. */
  day?: string | null;
  elapsed_seconds?: number;
  /** Seconds since the job last showed any sign of progress: a log line, a call, a new day. */
  quiet_seconds?: number;
} & BacktestJobProgress;

/** The progress bar's numbers. All absent from a server older than the bar. */
export type BacktestJobProgress = {
  phase?: BacktestPhase;
  /** Session `session` of the `sessions` one setting walks, while replaying. */
  session?: number | null;
  sessions?: number | null;
  /** `done` of `total` `unit` (plural) in the current phase; null while its size is unknown. */
  done?: number | null;
  total?: number | null;
  unit?: string | null;
  /** Seconds left at the rate measured so far; null until there is a rate worth quoting. */
  eta_seconds?: number | null;
};

export type BacktestPhase = "starting" | "fetching" | "sizing" | "replaying" | "recording";

/** What a running job is doing, in one line. */
export function describePhase(
  job: Pick<BacktestJob, "phase" | "step" | "steps" | "day"> & { unit?: string | null },
): string {
  switch (job.phase) {
    case "fetching":
      // The condor's long-history run downloads NSE's public archive, not ICICI's history.
      return job.unit === "NSE sessions" ? "Downloading NSE daily prices" : "Fetching missing history from ICICI";
    case "sizing":
      return "Pricing one lot's margin at today's levels";
    case "replaying": {
      // The Signals backtest replays series, not one bot's settings session by session.
      if (job.unit === "series") return "Replaying every signal series";
      const setting =
        job.step && job.steps && job.steps > 1 ? `Replaying setting ${job.step} of ${job.steps}` : "Replaying";
      return job.day ? `${setting} · session ${job.day}` : `${setting} · loading prices and signal readings`;
    }
    case "recording":
      return "Writing results and the audit trail";
    default:
      return "Starting";
  }
}

/** `75` -> `1m 15s`; `3700` -> `1h 1m`. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** The bar's share done, 0..1, or null while the size of the phase is not known. */
export function progressShare(job: BacktestJobProgress): number | null {
  const { done, total } = job;
  if (done == null || !total || total <= 0) return null;
  return Math.min(1, Math.max(0, done / total));
}

/** `0.004` -> `<1%`, so a bar that has moved never reads as not started. */
export function formatShare(share: number): string {
  if (share > 0 && share < 0.01) return "<1%";
  return `${Math.floor(share * 100)}%`;
}

/** What the bar is counting, in words: the session while replaying, else the phase's own count. */
export function progressCount(job: BacktestJobProgress): string | null {
  if (job.phase === "replaying" && job.session && job.sessions) {
    return `Session ${job.session} of ${job.sessions}`;
  }
  if (job.done != null && job.total && job.unit) {
    return `${job.done.toLocaleString("en-IN")} of ${job.total.toLocaleString("en-IN")} ${job.unit}`;
  }
  return null;
}

/** An estimate, so in coarse words: `45` -> `under a minute left`, `200` -> `about 4 min left`. */
export function formatTimeLeft(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return "under a minute left";
  if (s < 3600) return `about ${Math.ceil(s / 60)} min left`;
  const m = Math.round((s % 3600) / 60);
  return `about ${Math.floor(s / 3600)}h${m ? ` ${m}m` : ""} left`;
}

/** Past this, a job that has said nothing is worth a closer look; not before. */
export const QUIET_WARN_SECONDS = 180;

/** How the progress panel reads the job's silence. `null` when it has spoken recently. */
export function quietVerdict(quietSeconds: number, phase: BacktestPhase | undefined): string | null {
  if (quietSeconds < QUIET_WARN_SECONDS) return null;
  const quiet = formatDuration(quietSeconds);
  if (phase === "fetching" || phase === "sizing") {
    // Every ICICI call waits its turn behind live orders, and a throttle's cooldown runs for
    // minutes (design-decisions #24), so a long pause here is often the queue, not a hang.
    return `No update for ${quiet}. ICICI calls queue behind live orders and rate-limit cooldowns, which can last a few minutes.`;
  }
  return `No update for ${quiet}. The job is still alive; if this keeps climbing, stop it and run a shorter period.`;
}

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

export const fetchBacktestRun = (id: string) =>
  apiClient.get<BacktestRun>(`/bots/backtest/run?id=${encodeURIComponent(id)}`);
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
  signal_window_ended: "Signal window over",
  credit_decay_target: "Decay target",
  drift_stop: "Drift stop",
  terminated_for_day: "Daily loss cap",
  stale_feed: "Stale feed",
  session_end: "Session end",
  group_stop_loss_hit: "Loss stop",
  group_target_hit: "Profit target",
  expired: "Expired",
  // A condor campaign still running when the period ends, marked at its last check.
  open_at_period_end: "Open at period end (marked)",
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
