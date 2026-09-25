"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import {
  bundleRuns,
  CLIENT_BUNDLE_MAX_DAYS,
  rangeDays,
  type DateRange,
} from "@/lib/bot-run-bundles";
import {
  SIGNALS_QUERY_KEY,
  type MechanismAvailability,
  type SignalDirection,
  type SignalDuration,
  type SignalMechanism,
  type SignalsOverview,
} from "@/lib/signals";

export const BOT_HOLDINGS_WRITER = "holdings_writer" as const;
export const BOT_EXPIRY_INDEX_WRITER = "expiry_index_writer" as const;
export const BOT_MOMENTUM_LONG_SCALPER = "momentum_long_scalper" as const;
export const BOT_IRON_FLY_SCALPER = "iron_fly_scalper" as const;
export const BOT_CAS_BINGO = "cas_bingo" as const;

export type BotType =
  | typeof BOT_HOLDINGS_WRITER
  | typeof BOT_EXPIRY_INDEX_WRITER
  | typeof BOT_MOMENTUM_LONG_SCALPER
  | typeof BOT_IRON_FLY_SCALPER
  | typeof BOT_CAS_BINGO;

// --- CAS Bingo (docs/bots-cas-bingo-plan.md) ------------------------------------------

/** `simulation` places nothing; `live` is the card's Autonomous. Off is `enabled=false`. */
export type CasBingoMode = "simulation" | "live";
export type CasBingoStrategy = "credit_spread" | "debit_spread" | "long_strangle";
export type CasBingoStructure =
  | "bull_put_credit"
  | "bear_call_credit"
  | "bull_call_debit"
  | "bear_put_debit"
  | "long_strangle";

export type CasBingoConfig = {
  mode: CasBingoMode;
  indices: Record<string, { enabled: boolean }>;
  pre_cas_window: SessionWindow;
  cas_window: SessionWindow;
  strategy: CasBingoStrategy;
  /** The signal both spreads read flips from. `direction` applies to the debit spread only. */
  signal: SignalChoice;
  credit: {
    margin_lakhs: number;
    move_trigger_pct: number;
    inner_pct: number;
    outer_pct: number;
    auction_gap_pct: number;
    auction_min_credit_pct: number;
    target_pct: number;
    stop_loss_pct: number;
  };
  debit: {
    premium_budget_inr: number;
    sustain_minutes: number;
    inner_pct: number;
    outer_pct: number;
    target_pct: number;
    stop_loss_pct: number;
  };
  strangle: {
    premium_budget_inr: number;
    entry_time_ist: string;
    call_pct: number;
    put_pct: number;
    target_pct: number;
    stop_loss_pct: number;
  };
  liquidation: { enabled: boolean; min_captured_pct: number; safety_buffer_pct: number };
  execution: ScalperExecutionConfig;
};

/** Shown on the Credit choice, the Autonomous confirmation and both credit rows of the sheet. */
export const CAS_BINGO_CREDIT_WARNING =
  "ICICI may square off your positions at an extreme loss if MTM or margin requirements spike during CAS.";
export const CAS_BINGO_REGIME_NOTE =
  "During CAS the index is an indicative auction value, and SEBI's consultation (comments due 3 Oct 2026) may move expiry settlement off the auction.";

export type CasBingoPlanLeg = {
  right: "call" | "put";
  strike_price: number;
  action: "Buy" | "Sell";
  bid: number | null;
  ask: number | null;
};

export type CasBingoPlan = {
  index_code: string;
  index_label: string;
  expiry_display: string;
  structure: CasBingoStructure;
  label: string;
  family: "credit" | "debit" | "strangle";
  legs: CasBingoPlanLeg[];
  lots: number;
  lot_size: number;
  quantity: number;
  reference: number;
  reference_kind: "open" | "spot";
  spot: number | null;
  net_premium_per_unit: number;
  net_premium_inr: number;
  margin_required: number;
  notes: string[];
};

export type CasBingoBuyBack = {
  right: "call" | "put";
  strike_price: number;
  quantity: number;
  average_price: number;
  ask: number;
  cap_price: number;
  captured_pct: number;
  est_release: number;
  cost: number;
};

export type CasBingoLiquidation = {
  shortfall?: number;
  target?: number;
  est_release?: number;
  covered: boolean;
  buybacks: CasBingoBuyBack[];
  ineligible?: { right: string; strike_price: number; quantity: number; reason: string }[];
  note: string | null;
};

export type CasBingoCandidate = {
  structure: CasBingoStructure;
  label: string;
  family: "credit" | "debit" | "strangle";
  plan?: CasBingoPlan;
  problem?: { reason_code: string; reason: string } | null;
  liquidation?: CasBingoLiquidation;
};

export type CasBingoSheetIndex = {
  index_code: string;
  index_label: string;
  expiry_display: string;
  day_open: number | null;
  spot: number | null;
  signal: { state: string; value: number | null; name: string };
  /** Why the chosen signal is not yet available to the bot (the 30-day backtest gate), or null. */
  signal_blocked: string | null;
  sg_conflict: boolean;
  candidates: CasBingoCandidate[];
};

export type CasBingoSheet = {
  indices: CasBingoSheetIndex[];
  available_margin?: number | null;
  liquidation_enabled?: boolean;
  message?: string;
  warnings: { credit: string; cas: string };
};

export type CasBingoExecuteResult = {
  opened: boolean;
  reason_code: string;
  reason_text: string;
  cycle_id: string | null;
  plan: CasBingoPlan;
  liquidation: { plan: CasBingoLiquidation; rounds: unknown[] } | null;
};

/** Price all five structures for every index expiring today. Places nothing. */
export function useCasBingoSheet() {
  return useMutation({
    mutationFn: () => apiClient.post<CasBingoSheet>("/bots/cas-bingo/plan", {}),
  });
}

/** Re-price one structure and place it for real, liquidating first if margin is short. */
export function useCasBingoExecute() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { indexCode: string; structure: CasBingoStructure }) =>
      apiClient.post<CasBingoExecuteResult>("/bots/cas-bingo/execute", {
        index_code: vars.indexCode,
        structure: vars.structure,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });
}

/** Whether each signal mechanism is available to bots: a signal backtest covering 30 days on
 *  its current version (docs/signals-streamline-plan.md section 5). */
export function useSignalAvailability(enabled = true) {
  return useQuery({
    queryKey: SIGNALS_QUERY_KEY,
    enabled,
    staleTime: 60_000,
    queryFn: ({ signal }) => apiClient.get<SignalsOverview>("/api/signals", signal),
    select: (d): Record<SignalMechanism, MechanismAvailability> =>
      Object.fromEntries(d.mechanisms.map((m) => [m.id, m.availability])) as Record<
        SignalMechanism,
        MechanismAvailability
      >,
  });
}

export const SCALPER_BOT_TYPES: BotType[] = [
  BOT_MOMENTUM_LONG_SCALPER,
  BOT_IRON_FLY_SCALPER,
];

export function isScalper(botType: BotType): boolean {
  return SCALPER_BOT_TYPES.includes(botType);
}

/** `partial` means orders reached the exchange and a position is open, but something
 *  after that fell short — a leg rejected, or the stop that protects it never armed. It is
 *  deliberately not `failed`: the reason code says which half is missing, and the user has
 *  a live position either way. */
export type BotRunStatus =
  | "running"
  | "completed"
  | "partial"
  | "proposed"
  | "skipped"
  | "failed";

/** How an unattended run commits. `auto` places straight away; `telegram` sends the priced
 *  proposal to the linked chat and places nothing until the user taps Approve — silence
 *  never trades. */
export type ApprovalMode = "auto" | "telegram";

export type HoldingsWriterConfig = {
  default_safety_pct_ce: number;
  default_safety_pct_pe: number;
  delivery_cash_budget: number;
  expiry_preference: "current" | "next";
  proposal_ttl_minutes: number;
  /** Trading days, not calendar days — the bot never fires into a closed market. */
  fire_days_before_expiry: number;
  /** Doubles as the entry time: with a session in hand the bot fires here. */
  nag_start_ist: string;
  cutoff_ist: string;
  nag_interval_minutes: number;
  approval_mode: ApprovalMode;
};

export type IndexStrategy = "naked_ce" | "naked_pe" | "short_strangle";

export const STRATEGY_LABEL: Record<IndexStrategy, string> = {
  naked_ce: "Naked CE",
  naked_pe: "Naked PE",
  short_strangle: "Short strangle",
};

export type IndexWriterLeg = {
  enabled: boolean;
  /** A shortlist. With more than one entry the bot trades whichever yields the most
   *  premium per rupee of margin — never the biggest absolute premium, which a strangle
   *  would win every time it was shortlisted. */
  strategies: IndexStrategy[];
  safety_pct_ce: number;
  safety_pct_pe: number;
  margin_pct_cap: number;
  priority: number;
};

export type ExpiryIndexWriterConfig = {
  indices: Record<string, IndexWriterLeg>;
  entry_time_ist: string;
  nag_start_ist: string;
  cutoff_ist: string;
  nag_interval_minutes: number;
  approval_mode: ApprovalMode;
  loss_limit_premium_multiple: number;
  /** Share of the premium to capture before booking. 100 means let it expire worthless —
   *  no profit exit is armed at all, and only the stop-loss stands. */
  profit_book_premium_pct: number;
};

/** Paper runs the full logic against live prices and places nothing; live places real
 *  orders. A different axis from Bots 1 and 2's `approval_mode` — a scalper never asks
 *  before a trade, because approving dozens of scalps a day is not a workflow. */
export type ScalperMode = "paper" | "live";

export type SessionWindow = { start: string; end: string };

export type ScalperRiskConfig = {
  cumulative_stop_inr: number;
  consecutive_loss_limit: number;
  cooldown_minutes: number;
  /** Broker calls held back so scalping can never starve the dashboard, the other bots,
   *  or — the one that matters — a manual square-off. */
  api_budget_reserve_calls: number;
};

export type ScalperExecutionConfig = {
  entry_limit_tolerance_pct: number;
  entry_fill_timeout_seconds: number;
  entry_retries: number;
  exit_limit_band_pct: number;
};

export type MomentumLongScalperConfig = {
  index: string;
  trade_on_expiry_day: boolean;
  mode: ScalperMode;
  sessions: SessionWindow[];
  hard_square_off_ist: string;
  /** Capital deployed, not risked. Lots = floor(outlay / cost), and an ATM option cheapens
   *  towards expiry, so this buys more lots the nearer expiry gets. */
  premium_outlay_inr: number;
  /** The signal that opens a trade. A trade is held until the call that opened it ends; the
   *  stop and the ladder still apply. One trade per call. */
  signal: SignalChoice;
  exits: {
    /** A trailing trigger, not a take-profit: reaching it starts the runner. */
    target_pts: number;
    stop_loss_pts: number;
    level_1_trigger_pts: number;
    level_1_lock_pts: number;
    level_2_trigger_pts: number;
    level_2_lock_pts: number;
    level_3_runner_step_pts: number;
  };
  execution: ScalperExecutionConfig;
  risk: ScalperRiskConfig;
};

export type IronFlyScalperConfig = {
  index: string;
  trade_on_expiry_day: boolean;
  mode: ScalperMode;
  sessions: SessionWindow[];
  hard_square_off_ist: string;
  margin_ceiling_inr: number;
  min_lots: number;
  structure: {
    wing_width_points: number;
    widen_above_vix: number | null;
    widened_wing_width_points: number;
  };
  exits: {
    target_decay_pct: number;
    /** Both stops are live and the tighter binds; either can be null to switch it off. */
    hard_stop_loss_inr: number | null;
    stop_loss_credit_pct: number | null;
    max_spot_drift_pct: number;
  };
  reentry: {
    cooldown_minutes: number;
    range_window_minutes: number;
    max_range_pct: number;
  };
  /** An extra condition on opening a fly. Fails closed: an unreadable input holds. */
  entry_filter: {
    kind: "none" | "vix_not_rising" | "signal_quiet";
    vix_lookback_minutes: number;
    vix_max_rise_pct: number;
    /** `signal_quiet`: the signal whose live call (either side) holds a fly. Direction unused. */
    signal: SignalChoice;
  };
  execution: ScalperExecutionConfig;
  risk: ScalperRiskConfig;
};

/** A cell of the signal grid, plus the bot's own direction (docs/signals-streamline-plan.md 7). */
export type SignalChoice = {
  mechanism: SignalMechanism;
  duration: SignalDuration;
  direction: SignalDirection;
};

/** One scalper round trip. `friction` is a first-class field, not a derived one: at roughly
 *  a hundred rupees a cycle it is the constraint that decides whether the strategy works. */
export type BotCycle = {
  id: string;
  run_id: string;
  bot_type: BotType;
  cycle_no: number;
  structure: string;
  legs: Record<string, unknown>[];
  lots: number | null;
  opened_at: string | null;
  closed_at: string | null;
  entry_value: number | null;
  exit_value: number | null;
  gross_pnl: number | null;
  friction: number | null;
  net_pnl: number | null;
  exit_reason_code: string | null;
  exit_reason_text: string | null;
  detail: Record<string, unknown> | null;
  paper: boolean;
};

export type TradingCharges = {
  brokerage_per_order_inr: number;
  brokerage_pct_of_premium: number;
  brokerage_cap_inr: number | null;
  stt_sell_pct: number;
  /** NSE (NFO). BSE charges a different rate, so one field could only ever be right
   *  for one exchange. */
  exchange_txn_pct: number;
  exchange_txn_pct_bse: number;
  sebi_pct: number;
  ipft_pct: number;
  stamp_buy_pct: number;
  gst_pct: number;
  slippage_spread_fraction: number;
};

export type Bot = {
  id: string;
  bot_type: BotType;
  enabled: boolean;
  /** Cross-bot ordering. On a day both fire, the lower number sizes and places first and
   *  the other sizes against what is left. */
  priority: number;
  config: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
};

export type BotRun = {
  id: string;
  bot_type: BotType;
  /** `session` is the scalpers': one row covering a whole trading day, with its round
   *  trips in `bot_cycles` beneath it — a different unit of work, not a fourth way to
   *  start a run. */
  trigger: "schedule" | "manual" | "session_arrival" | "session" | "telegram" | "backtest";
  status: BotRunStatus;
  reason_code: string | null;
  reason_text: string | null;
  detail: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
  /** Audit file covering this run's trading *day*, or null once it ages out of retention.
   *  One per bot per day, so a day fragmented across many interrupted session rows still
   *  opens one continuous record. For a `backtest` row it is that replay's own trail instead,
   *  downloaded from a different route (design-decisions #35). */
  audit_log: string | null;
};

/** Back-to-back runs of one bot sharing a day, trigger and outcome (Activity table). Built in
 *  the browser for ranges up to a week (`runs` present) and by `/bots/runs/bundles` beyond
 *  that (`runs` absent — fetched on expand). */
export type BotRunBundle = {
  bot_type: BotType;
  trigger: BotRun["trigger"];
  status: BotRunStatus;
  date: string;
  count: number;
  first_started_at: string | null;
  last_started_at: string | null;
  latest: BotRun;
  distinct_reasons: number;
  /** The bundle row's download: the bot's full-day trail, or null for several backtests. */
  audit_log: string | null;
  /** Newest first. */
  runs?: BotRun[];
};

/** Display metadata. Blurbs state each bot's *constraint model* — the part a user cannot
 *  infer from the name — in one line, because on a square card every wrapped line is space
 *  taken from the state and controls below it. */
export const BOT_META: Record<BotType, { title: string; blurb: string }> = {
  [BOT_HOLDINGS_WRITER]: {
    title: "Holdings Option Writer",
    blurb: "Calls capped by stock held, puts by delivery cash.",
  },
  [BOT_EXPIRY_INDEX_WRITER]: {
    title: "Expiry-Day Index Writer",
    blurb: "Sizes a short index leg against free margin, arms its stop on fill.",
  },
  [BOT_MOMENTUM_LONG_SCALPER]: {
    title: "Long Scalper",
    blurb: "Buys one ATM option on a NIFTY signal of your choice, holds it for the call.",
  },
  [BOT_IRON_FLY_SCALPER]: {
    title: "Intraday Iron Fly",
    blurb: "Sells an ATM fly under a margin ceiling, books it on credit decay.",
  },
  [BOT_CAS_BINGO]: {
    title: "CAS Bingo",
    blurb: "Expiry-day spreads or a strangle around the closing auction, buy leg first.",
  },
};

export const INDEX_LABEL: Record<string, string> = { NIFTY: "NIFTY", BSESEN: "SENSEX" };

export function useBots() {
  return useQuery({
    queryKey: ["bots"],
    queryFn: ({ signal }) => apiClient.get<Bot[]>("/bots/list", signal),
  });
}

/** A session's round trips. Fetched only when a run is expanded: a scalper can produce
 *  dozens a day, and loading every session's cycles to render a collapsed list would pull
 *  the whole month for nothing. */
export function useBotCycles(runId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["bots", "cycles", runId],
    enabled: Boolean(runId) && enabled,
    queryFn: ({ signal }) =>
      apiClient.get<BotCycle[]>(`/bots/cycles?run_id=${encodeURIComponent(runId!)}`, signal),
  });
}

/** Today's cycles for one bot, for the card's counters.
 *
 *  Polled on `useTodaysRun`'s minute cadence while the bot is armed (`poll`) or still holding
 *  a position. Without it "Cycles today" and Net P&L froze at whatever the page loaded with —
 *  window-focus refetch is off app-wide — so a bot that traded all morning read 0 cycles
 *  beside a verdict that was updating live. */
export function useTodaysCycles(botType: BotType, enabled = true, poll = false) {
  return useQuery({
    queryKey: ["bots", "cycles", "today", botType],
    enabled,
    refetchInterval: (query) =>
      poll || (query.state.data ?? []).some((c) => c.closed_at === null) ? 60_000 : false,
    queryFn: ({ signal }) =>
      apiClient.get<BotCycle[]>(`/bots/cycles?bot_type=${botType}&limit=500`, signal),
    select: (cycles: BotCycle[]) => {
      const today = new Date().toISOString().slice(0, 10);
      return cycles.filter((c) => (c.opened_at ?? "").slice(0, 10) === today);
    },
  });
}

export type PaperEvidenceDay = {
  trading_day: string;
  reason_code: string | null;
  reason_text: string | null;
  cycles: number;
  closed_cycles: number;
  wins: number;
  losses: number;
  net_pnl: number;
  friction: number;
};

/** The latest backtest on the settings being armed. Never part of the gate: it is the other
 *  half of the picture, because one Simulation day says nothing about edge. */
export type BacktestEvidence = {
  run_id: string;
  created_at: string;
  from_date: string | null;
  to_date: string | null;
  price_source: string;
  days_replayed: number;
  days_awaiting_data: number;
  cycles: number;
  win_rate_pct: number | null;
  net_pnl: number;
  friction: number;
  compare_day: string | null;
  compare_median_entry_gap: number | null;
  compare_pairs: number | null;
};

export type LiveEligibility = {
  bot_type: BotType;
  unlocked: boolean;
  config_hash: string;
  days: number;
  cycles: number;
  closed_cycles: number;
  wins: number;
  losses: number;
  net_pnl: number;
  friction: number;
  sessions: PaperEvidenceDay[];
  blocked_reason: string | null;
  backtest: BacktestEvidence | null;
};

/** Whether this scalper may be armed Live, and the paper record behind that answer.
 *
 *  Read from the SAME `evidence.gather` the PATCH path enforces with, so the card can never
 *  offer a control the server would then refuse. `unlocked` is only the gate's half of the
 *  decision -- one completed paper trading day on the current settings; whether that day was
 *  any good is the user's call, which is why every number travels with it for the
 *  confirmation dialog to show.
 *
 *  Invalidated by `useUpdateBot`, because editing a P&L-bearing setting changes the config
 *  fingerprint and therefore this answer.
 */
export function useLiveEligibility(botType: BotType, enabled = true) {
  return useQuery({
    queryKey: ["bots", "live-eligibility", botType],
    enabled,
    queryFn: ({ signal }) =>
      apiClient.get<LiveEligibility>(
        `/bots/live-eligibility?bot_type=${botType}`,
        signal,
      ),
  });
}

/** The shared cost model. Deployment-wide, so it is not keyed by bot. */
export function useTradingCharges() {
  return useQuery({
    queryKey: ["bots", "charges"],
    queryFn: ({ signal }) => apiClient.get<TradingCharges>("/bots/charges", signal),
  });
}

export function useUpdateTradingCharges() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<TradingCharges>) =>
      apiClient.patch<TradingCharges>("/bots/charges", patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bots", "charges"] });
    },
  });
}

export function useBotRuns(botType?: BotType, limit = 50) {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (botType) qs.set("bot_type", botType);
  return useQuery({
    queryKey: ["bots", "runs", botType ?? "all", limit],
    queryFn: ({ signal }) => apiClient.get<BotRun[]>(`/bots/runs?${qs.toString()}`, signal),
  });
}

/** The Activity table for a date range, bundled. A week or less is fetched as raw runs and
 *  bundled here so expanding is instant; anything longer is bundled by the backend so a month
 *  never ships as tens of thousands of rows. */
export function useBotRunBundles(range: DateRange | null) {
  return useQuery({
    queryKey: ["bots", "runs", "bundles", range?.from, range?.to],
    enabled: range !== null,
    queryFn: async ({ signal }): Promise<BotRunBundle[]> => {
      const r = range as DateRange;
      const qs = new URLSearchParams({ date_from: r.from, date_to: r.to });
      if (rangeDays(r) <= CLIENT_BUNDLE_MAX_DAYS) {
        return bundleRuns(await apiClient.get<BotRun[]>(`/bots/runs?${qs.toString()}`, signal));
      }
      return apiClient.get<BotRunBundle[]>(`/bots/runs/bundles?${qs.toString()}`, signal);
    },
    // While a backtest shows as running, keep checking: its row turns into the outcome --
    // finished, failed, or interrupted -- on its own, without anyone opening it.
    refetchInterval: (q) =>
      q.state.data?.some((b) => b.trigger === "backtest" && b.status === "running") ? 5_000 : false,
  });
}

/** A server-built bundle's runs, fetched when it is expanded. A bundle is exactly its bot's
 *  runs with its trigger and status between its first and last timestamps. */
export function useBundleRuns(bundle: BotRunBundle, enabled: boolean) {
  return useQuery({
    queryKey: ["bots", "runs", "bundle", bundle.bot_type, bundle.trigger, bundle.status, bundle.first_started_at, bundle.last_started_at],
    enabled: enabled && !bundle.runs,
    queryFn: ({ signal }) => {
      const qs = new URLSearchParams({
        bot_type: bundle.bot_type,
        trigger: bundle.trigger,
        status: bundle.status,
        started_from: bundle.first_started_at ?? "",
        started_to: bundle.last_started_at ?? "",
      });
      return apiClient.get<BotRun[]>(`/bots/runs?${qs.toString()}`, signal);
    },
  });
}

/** Today's open session run for one scalper — the row carrying the live verdict.
 *
 *  Polled while the bot is enabled: the reason on it is the answer to "why is nothing
 *  happening", and an answer that only refreshes on a page load is not much of an answer.
 *  The backend re-states an unchanged verdict once a minute, so polling faster than that
 *  buys nothing. */
export function useTodaysRun(botType: BotType, enabled = true) {
  return useQuery({
    queryKey: ["bots", "runs", "today", botType],
    enabled,
    refetchInterval: enabled ? 60_000 : false,
    queryFn: ({ signal }) =>
      apiClient.get<BotRun[]>(`/bots/runs?bot_type=${botType}&limit=5`, signal),
    select: (runs: BotRun[]) => {
      const today = new Date().toISOString().slice(0, 10);
      return (
        runs.find(
          (r) => r.trigger === "session" && (r.started_at ?? "").slice(0, 10) === today,
        ) ?? null
      );
    },
  });
}

export type ProposalLeg = {
  stock_code: string;
  exchange_code: string;
  right: "call" | "put";
  expiry_display: string;
  strike_price: number;
  lots: number;
  lot_size: number;
  quantity: number;
  premium_per_share: number;
  premium_total: number;
  premium_basis: "bid" | "ltp_indicative";
  /** Underlying price the strike was picked against — what the distance % is a percentage of. */
  spot: number | null;
  span_margin: number | null;
  elm_margin: number | null;
  delivery_exposure: number | null;
  held_quantity: number | null;
  pledged_quantity: number | null;
  existing_short_lots: number;
  scrip_priority: number;
  selected: boolean;
  note: string | null;
  strategy: IndexStrategy | null;
  /** Both sides of a strangle share this, so selecting one selects both. */
  group_key: string | null;
  margin_yield: number | null;
};

export type ScripPref = {
  stock_code: string;
  ce_enabled: boolean;
  pe_enabled: boolean;
  /** null means "every lot the holding covers" for calls, "one lot" for puts. */
  ce_lots: number | null;
  pe_lots: number | null;
  safety_pct_ce: number | null;
  safety_pct_pe: number | null;
  priority: number;
};

export type HoldingRow = {
  stock_code: string;
  /** A holding splits three ways, exhaustively: available + blocked + pledged = quantity.
   *  Only `blocked` is excluded from call coverage — it is already earmarked elsewhere.
   *  Pledged stock IS coverage; it just has to be unpledged before expiry to deliver. */
  quantity: number;
  available_quantity: number;
  blocked_quantity: number;
  pledged_quantity: number;
  deliverable_quantity: number;
  lot_size: number | null;
  lots_held: number;
  available_lots: number;
  blocked_lots: number;
  pledged_lots: number;
  deliverable_lots: number;
  existing_short_ce_lots: number;
  existing_short_pe_lots: number;
  fno_eligible: boolean;
  ineligible_reason: string | null;
  current_market_price: number | null;
};

/** A user's change to one proposed leg in the manual run. `distance_pct` re-derives the
 *  strike from the current spot the same way the scan does; it is how the UI moves a strike
 *  (the raw `strike_price` is still accepted by the backend but the sheet no longer sends it). */
export type LegEdit = { lots?: number; strike_price?: number; distance_pct?: number };

export type ProposalTotals = {
  premium_total: number;
  span_total: number;
  elm_total: number;
  delivery_exposure_total: number;
  delivery_cash_budget: number;
  delivery_headroom: number;
  leg_count: number;
  selected_count: number;
};

export type Proposal = {
  id: string;
  run_id: string;
  bot_type: BotType;
  status: string;
  legs: ProposalLeg[];
  totals: ProposalTotals | null;
  created_at: string | null;
  expires_at: string | null;
};

export type SkippedScrip = { stock_code: string; reason_code: string; reason: string };

export type ScanResponse = {
  run_id: string;
  proposal: Proposal | null;
  skipped: SkippedScrip[];
  warnings: Record<string, unknown>[];
};

export type PlacedLeg = {
  stock_code: string;
  right: string;
  strike_price: number;
  expiry_display: string;
  quantity: number;
  limit_price: number;
  order_ids: string[];
  /** Placement only — whether this leg reached the exchange. The stop is on `stops`. */
  error: string | null;
  /** What the order feed has confirmed filled so far; null before it reports. */
  filled_quantity?: number | null;
};

/** One index's PB/SL once the approval placed it. "pending" arms itself on the last fill. */
export type ExitStop = {
  stock_code: string;
  expiry_display: string;
  /** `skipped` is final, not a retry: the group already held another open position, so
   *  arming here would have pooled P&L across both and nothing will arm it later. */
  status: "armed" | "pending" | "failed" | "skipped";
  rule_id: string | null;
  pending_exit_id: string | null;
  detail: string | null;
};

export type ApprovalResult = {
  proposal_id: string;
  placed: PlacedLeg[];
  /** Every leg reached the exchange. Says nothing about the stop — see `stops`. */
  all_succeeded: boolean;
  stops?: ExitStop[];
};

export function useProposal(botType: BotType) {
  return useQuery({
    queryKey: ["bots", "proposal", botType],
    queryFn: ({ signal }) =>
      apiClient.get<Proposal | null>(`/bots/proposal?bot_type=${botType}`, signal),
  });
}

export function useScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (botType: BotType) =>
      apiClient.post<ScanResponse>(`/bots/scan?bot_type=${botType}`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });
}

export function useApproveProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      botType: BotType;
      legIndexes: number[];
      edits?: Record<number, LegEdit>;
    }) =>
      apiClient.post<ApprovalResult>(
        `/bots/proposal/approve?bot_type=${vars.botType}`,
        { leg_indexes: vars.legIndexes, edits: vars.edits ?? {} },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });
}

export function useRejectProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (botType: BotType) =>
      apiClient.post<Proposal>(`/bots/proposal/reject?bot_type=${botType}`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });
}

export function useUpdateBot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      botType: BotType;
      enabled?: boolean;
      priority?: number;
      config?: Record<string, unknown>;
    }) =>
      apiClient.patch<Bot>(`/bots/config?bot_type=${vars.botType}`, {
        enabled: vars.enabled,
        priority: vars.priority,
        config: vars.config,
      }),
    onSuccess: () => {
      // `["bots"]` is a prefix, so this also refreshes live-eligibility -- which it must:
      // any material config edit changes the fingerprint the paper evidence is counted
      // against, and a stale "unlocked" would offer a Live switch the server now refuses.
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });
}

/** Live holdings for Bot 1's scrip settings. Fetched when the drawer opens rather than
 *  cached: what the user holds changes without the bot being told, and configuring lots
 *  against a scrip they sold last week is worse than showing nothing. */
export function useBotHoldings(enabled: boolean) {
  return useQuery({
    queryKey: ["bots", "holdings"],
    queryFn: ({ signal }) => apiClient.get<HoldingRow[]>("/bots/holdings", signal),
    enabled,
    staleTime: 0,
  });
}

export function useScripPrefs(enabled: boolean) {
  return useQuery({
    queryKey: ["bots", "scrip-prefs"],
    queryFn: ({ signal }) => apiClient.get<ScripPref[]>("/bots/scrip-prefs", signal),
    enabled,
  });
}

export function useSaveScripPrefs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (prefs: ScripPref[]) =>
      apiClient.put<ScripPref[]>("/bots/scrip-prefs", { prefs }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bots", "scrip-prefs"] });
    },
  });
}

/** Bot 2's manual run: size today's trade without placing it. */
export function usePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (botType: BotType) =>
      apiClient.post<ScanResponse>(`/bots/plan?bot_type=${botType}`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });
}

/** Re-price the proposal with the user's edits applied. Places nothing.
 *  Margin is not linear in lot count — it comes from the broker, not multiplication — so
 *  an edited size has to go back to the source rather than being scaled in the browser. */
export function useReprice() {
  return useMutation({
    mutationFn: (vars: {
      botType: BotType;
      legIndexes: number[];
      edits: Record<number, LegEdit>;
    }) =>
      apiClient.post<Proposal>(`/bots/proposal/reprice?bot_type=${vars.botType}`, {
        leg_indexes: vars.legIndexes,
        edits: vars.edits,
      }),
  });
}
