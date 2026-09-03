"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

export const BOT_HOLDINGS_WRITER = "holdings_writer" as const;
export const BOT_EXPIRY_INDEX_WRITER = "expiry_index_writer" as const;

export type BotType = typeof BOT_HOLDINGS_WRITER | typeof BOT_EXPIRY_INDEX_WRITER;

export type BotRunStatus = "running" | "completed" | "proposed" | "skipped" | "failed";

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
  trigger: "schedule" | "manual" | "session_arrival";
  status: BotRunStatus;
  reason_code: string | null;
  reason_text: string | null;
  detail: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
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
};

export const INDEX_LABEL: Record<string, string> = { NIFTY: "NIFTY", BSESEN: "SENSEX" };

export function useBots() {
  return useQuery({
    queryKey: ["bots"],
    queryFn: ({ signal }) => apiClient.get<Bot[]>("/bots/list", signal),
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
  error: string | null;
};

export type ApprovalResult = {
  proposal_id: string;
  placed: PlacedLeg[];
  all_succeeded: boolean;
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
