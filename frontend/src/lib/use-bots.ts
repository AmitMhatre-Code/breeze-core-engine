"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

export const BOT_HOLDINGS_WRITER = "holdings_writer" as const;
export const BOT_EXPIRY_INDEX_WRITER = "expiry_index_writer" as const;

export type BotType = typeof BOT_HOLDINGS_WRITER | typeof BOT_EXPIRY_INDEX_WRITER;

export type BotRunStatus = "running" | "completed" | "proposed" | "skipped" | "failed";

export type HoldingsWriterConfig = {
  default_safety_pct_ce: number;
  default_safety_pct_pe: number;
  delivery_cash_budget: number;
  expiry_preference: "current" | "next";
  proposal_ttl_minutes: number;
};

export type IndexWriterLeg = {
  enabled: boolean;
  right: "call" | "put";
  safety_pct: number;
  margin_pct_cap: number;
  priority: number;
};

export type ExpiryIndexWriterConfig = {
  indices: Record<string, IndexWriterLeg>;
  entry_time_ist: string;
  nag_start_ist: string;
  cutoff_ist: string;
  nag_interval_minutes: number;
  loss_limit_premium_multiple: number;
  profit_target_option_price: number;
};

export type Bot = {
  id: string;
  bot_type: BotType;
  enabled: boolean;
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

/** Display metadata. Descriptions state each bot's *constraint model*, because that is the
 *  part a user cannot infer from the name — calls are capped by stock, puts by cash. */
export const BOT_META: Record<BotType, { title: string; blurb: string; mode: string }> = {
  [BOT_HOLDINGS_WRITER]: {
    title: "Holdings Option Writer",
    blurb:
      "Writes options against your NSE holdings. Calls are capped by the stock you hold; puts are opt-in and capped by your delivery-cash budget.",
    mode: "Proposes — you approve before anything is placed",
  },
  [BOT_EXPIRY_INDEX_WRITER]: {
    title: "Expiry-Day Index Writer",
    blurb:
      "On NIFTY and SENSEX expiry days, sizes a short position against available margin and arms a stop on fill.",
    mode: "Trades unattended within your configured caps",
  },
};

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
  span_margin: number | null;
  elm_margin: number | null;
  delivery_exposure: number | null;
  held_quantity: number | null;
  pledged_quantity: number | null;
  existing_short_lots: number;
  selected: boolean;
  note: string | null;
};

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
    mutationFn: (vars: { botType: BotType; legIndexes: number[] }) =>
      apiClient.post<ApprovalResult>(
        `/bots/proposal/approve?bot_type=${vars.botType}`,
        { leg_indexes: vars.legIndexes },
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
      config?: Record<string, unknown>;
    }) =>
      apiClient.patch<Bot>(`/bots/config?bot_type=${vars.botType}`, {
        enabled: vars.enabled,
        config: vars.config,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });
}
