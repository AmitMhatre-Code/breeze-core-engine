import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

/** Dynamic Iron Condors: campaigns, their ledger and checks, and backtests
 *  (docs/dynamic-iron-condor-plan.md). The engine runs on the backend; this file only carries
 *  its answers to the page. */

export type CondorSettings = {
  underlying: "NIFTY";
  expiry_kind: "monthly" | "any";
  entry_dte: number;
  tranche_cutoff_dte: number;
  exit_dte: number;
  short_delta: number;
  /** Each wing this % of spot beyond its short, the same on both sides (#69). */
  wing_width_pct: number;
  tranches: number;
  entry_check: "sod" | "eod";
  sod_check_ist: string;
  eod_check_ist: string;
  leg_rule_delta_floor: number;
  leg_rule_decay_pct: number;
  net_delta_band_per_lot: number;
  min_roll_credit_points: number;
  no_roll_within_days_of_exit: number;
  max_loss_inr: number | null;
  max_loss_pct_of_ceiling: number | null;
  margin_ceiling_inr: number;
  /** Tranche entries only, the next cycle of a time roll included: wait while the cycle's options
   *  price less movement to expiry than the index's history forecasts (#78). */
  premium_gate?: { enabled: boolean; threshold: number };
};

export type CondorCycle = {
  id: number;
  campaign_id: string;
  expiry: string;
  opened_at: string;
  closed_at: string | null;
  close_reason: string | null;
  tranches_entered: number;
};

export type CondorCampaign = {
  id: string;
  underlying: string;
  origin: string;
  /** `paper` campaigns (the bot's Simulation mode) hold nothing at the broker and own no group. */
  mode: "live" | "paper" | string;
  status: "active" | "closed";
  settings: CondorSettings;
  created_at: string;
  closed_at: string | null;
  close_reason: string | null;
  cycle: CondorCycle | null;
  cycles: CondorCycle[];
};

export type CondorOrder = {
  strike: number;
  right: "Call" | "Put";
  action: "Buy" | "Sell";
  quantity: number;
  opening: boolean;
  price: number | null;
};

export type CondorMetrics = {
  dte: number | null;
  call_short_abs_delta: number | null;
  put_short_abs_delta: number | null;
  net_delta_per_lot: number | null;
  tested_side: "Call" | "Put" | null;
  untested_decay_pct: number | null;
  open_value_inr: number | null;
  campaign_pnl_inr: number | null;
  max_loss_limit_inr: number | null;
  lower_breakeven: number | null;
  upper_breakeven: number | null;
  at_cap: boolean;
  worst_loss_at_wings_inr: number | null;
};

export type CondorAction =
  | "no_action"
  | "unavailable"
  | "close_all"
  | "exit_or_roll"
  | "roll_untested"
  | "enter_tranche";

export type CondorDecision = {
  action: CondorAction;
  reason: string;
  text: string;
  roll_credit_points: number | null;
  tranche_strikes: Record<string, number> | null;
  orders: CondorOrder[];
  metrics: Partial<CondorMetrics>;
};

export type CondorDifference = {
  strike: number;
  right: "Call" | "Put";
  ledger_units: number;
  broker_units: number;
  units: number;
  left_out: boolean;
  can_leave_out: boolean;
  broker_avg_price: number | null;
};

export type CondorLeg = {
  strike: number;
  right: "Call" | "Put";
  side: "Buy" | "Sell";
  quantity: number;
  avg_price: number;
};

export type CondorEvaluation = {
  check_kind: string;
  indicative: boolean;
  stand_ins: string[];
  differences: CondorDifference[];
  spot?: number | null;
  spot_source?: string | null;
  ledger?: { cash_inr: number; charges_inr: number; fills: number; legs: CondorLeg[] };
  decision: CondorDecision;
};

export type CondorFill = {
  id: number;
  order_id: string | null;
  expiry: string;
  strike: number;
  right: "Call" | "Put";
  side: "Buy" | "Sell";
  quantity: number;
  price: number;
  charges: number;
  kind: string;
  filled_at: string;
  note: string | null;
  cash: number;
};

export type CondorDecisionRow = {
  id: number;
  at: string;
  check_kind: string;
  action: CondorAction;
  reason: string;
  text: string;
  outcome: string | null;
  snapshot: { metrics?: Partial<CondorMetrics>; orders?: CondorOrder[] } | null;
};

export type CondorCampaignDetail = CondorCampaign & {
  fills: CondorFill[];
  decisions: CondorDecisionRow[];
  last_scheduled: CondorDecisionRow | null;
};

// ---- pure helpers (tested) --------------------------------------------------------------

/** The campaign whose open cycle is on this group's expiry, if any. */
export function campaignForGroup(
  campaigns: ReadonlyArray<CondorCampaign> | undefined,
  stockCode: string,
  expiry: string,
): CondorCampaign | null {
  const want = expiry.trim().toLowerCase();
  return (
    (campaigns ?? []).find(
      (c) =>
        c.status === "active" &&
        c.mode !== "paper" &&
        c.underlying.toUpperCase() === stockCode.trim().toUpperCase() &&
        (c.cycle?.expiry ?? "").trim().toLowerCase() === want,
    ) ?? null
  );
}

const ACTION_LABEL: Record<CondorAction, string> = {
  no_action: "Nothing to do",
  unavailable: "Could not decide",
  close_all: "Close everything",
  exit_or_roll: "Exit or time-roll",
  roll_untested: "Roll the untested side",
  enter_tranche: "Enter a tranche",
};

export function actionLabel(action: CondorAction): string {
  return ACTION_LABEL[action] ?? action;
}

/** How loudly the card shows a decision: actions stand out, a skipped roll is a note. */
export function actionTone(action: CondorAction): "act" | "warn" | "quiet" {
  if (action === "close_all" || action === "exit_or_roll") return "warn";
  if (action === "roll_untested" || action === "enter_tranche") return "act";
  if (action === "unavailable") return "warn";
  return "quiet";
}

export function checkKindLabel(kind: string): string {
  if (kind === "sod") return "Start-of-day check";
  if (kind === "eod") return "End-of-day check";
  if (kind === "manual") return "Manual ticket";
  return "Evaluated now";
}

export function orderLine(o: CondorOrder): string {
  const leg = `${o.strike.toLocaleString("en-IN")} ${o.right === "Call" ? "CE" : "PE"}`;
  const verb = o.action === "Buy" ? (o.opening ? "Buy" : "Buy back") : o.opening ? "Sell" : "Sell to close";
  const price = o.price != null ? ` @ ${o.price.toFixed(2)}` : "";
  return `${verb} ${o.quantity.toLocaleString("en-IN")} × ${leg}${price}`;
}

/** One sentence for a broker/ledger difference. */
export function differenceText(d: CondorDifference): string {
  const leg = `${d.strike.toLocaleString("en-IN")} ${d.right === "Call" ? "CE" : "PE"}`;
  const fmt = (u: number) => (u === 0 ? "nothing" : `${u > 0 ? "long" : "short"} ${Math.abs(u)}`);
  return `${leg}: the broker holds ${fmt(d.broker_units)}, the ledger ${fmt(d.ledger_units)}.`;
}

export function formatInr(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : "";
  return `${sign}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: digits })}`;
}

export function formatDelta(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`;
}

// ---- API --------------------------------------------------------------------------------

export const CONDOR_CAMPAIGNS_KEY = ["condor", "campaigns"] as const;

export function useCondorCampaigns(includeClosed = false) {
  return useQuery({
    queryKey: [...CONDOR_CAMPAIGNS_KEY, includeClosed],
    queryFn: ({ signal }) =>
      apiClient.get<{ campaigns: CondorCampaign[] }>(
        `/api/condor/campaigns${includeClosed ? "?include_closed=true" : ""}`,
        signal,
      ),
    staleTime: 15_000,
  });
}

export function useCondorCampaign(id: string | null) {
  return useQuery({
    queryKey: ["condor", "campaign", id],
    queryFn: ({ signal }) => apiClient.get<CondorCampaignDetail>(`/api/condor/campaigns/${id}`, signal),
    enabled: Boolean(id),
    refetchInterval: 60_000,
  });
}

/** The engine, run now on the card's behalf. Never acted on, never recorded. */
export function useCondorEvaluation(id: string | null) {
  return useQuery({
    queryKey: ["condor", "evaluate", id],
    queryFn: ({ signal }) =>
      apiClient.post<CondorEvaluation>(`/api/condor/campaigns/${id}/evaluate`, {}, { signal }),
    enabled: Boolean(id),
    refetchInterval: 20_000,
  });
}

function useCondorInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["condor"] });
}

export function useCreateCampaign() {
  const done = useCondorInvalidate();
  return useMutation({
    mutationFn: (body: { settings: Partial<CondorSettings>; expiry?: string; adopt?: boolean }) =>
      apiClient.post<CondorCampaign>("/api/condor/campaigns", body),
    onSuccess: done,
  });
}

export function useUpdateCampaignSettings(id: string) {
  const done = useCondorInvalidate();
  return useMutation({
    mutationFn: (settings: CondorSettings) =>
      apiClient.put<CondorCampaign>(`/api/condor/campaigns/${id}/settings`, settings),
    onSuccess: done,
  });
}

export function useAssignDifference(id: string) {
  const done = useCondorInvalidate();
  return useMutation({
    mutationFn: (body: { strike: number; right: "Call" | "Put"; price: number }) =>
      apiClient.post(`/api/condor/campaigns/${id}/assign`, body),
    onSuccess: done,
  });
}

export function useLeaveOutDifference(id: string) {
  const done = useCondorInvalidate();
  return useMutation({
    mutationFn: (body: { strike: number; right: "Call" | "Put" }) =>
      apiClient.post(`/api/condor/campaigns/${id}/leave-out`, body),
    onSuccess: done,
  });
}

export function useCloseCampaign(id: string) {
  const done = useCondorInvalidate();
  return useMutation({
    mutationFn: () => apiClient.post<CondorCampaign>(`/api/condor/campaigns/${id}/close`, {}),
    onSuccess: done,
  });
}

// ---- defaults and the bot's entry (#67) --------------------------------------------------

/** Backtests run from the bot card's clock and land in Activity, like every bot's (#67); only
 *  the default settings a new campaign's form starts from are read from here. */
export type CondorBacktestDefaults = { settings: CondorSettings; history_start: string; live: boolean };

export function useCondorBacktestDefaults() {
  return useQuery({
    queryKey: ["condor", "backtest", "defaults"],
    queryFn: ({ signal }) => apiClient.get<CondorBacktestDefaults>("/api/condor/backtest/defaults", signal),
    staleTime: 5 * 60_000,
  });
}

/** The bot card's play button: a first tranche at the bot's saved settings, which Basket
 *  Orders loads when opened with `?condor=1`. */
export type CondorEntry = {
  underlying: "NIFTY";
  exchange_code: "NFO";
  expiry: string;
  lot_size: number | null;
  legs: { strike: number; right: "Call" | "Put"; side: "Buy" | "Sell" }[];
  tranches: number;
  lots: number;
  sizing: string;
  indicative: boolean;
  /** Set when a wing sits at the furthest listed strike, nearer than the set width (#69). */
  wing_note: string | null;
};

export const fetchCondorEntry = () => apiClient.get<CondorEntry>("/api/condor/bot/entry");

/** Basket Orders' "Manage as a Dynamic Iron Condor campaign": a new campaign on the bot's saved
 *  settings whose first tranche is the basket, placed by the campaign executor. */
export function useOpenCampaign() {
  const done = useCondorInvalidate();
  return useMutation({
    mutationFn: (body: { expiry: string; orders: TicketRow[]; note?: string }) =>
      apiClient.post<{ campaign: CondorCampaign; execution: CondorExecution }>("/api/condor/campaigns/open", body),
    onSuccess: done,
  });
}

/** Handing a manual campaign to the bot (#68): what it would change, and what still stops it. */
export type CondorHandover = {
  allowed: boolean;
  blockers: string[];
  mode: "off" | "paper" | "telegram" | "auto";
  settings_changes: { field: keyof CondorSettings; campaign: unknown; bot: unknown }[];
  tranches_entered: number;
  tranches_remaining: number;
  sizing: string;
  decision: CondorDecision | null;
  indicative: boolean;
};

export function useHandoverPreview(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["condor", "handover", id],
    queryFn: ({ signal }) => apiClient.get<CondorHandover>(`/api/condor/campaigns/${id}/handover`, signal),
    enabled,
  });
}

export function useHandOver(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<CondorCampaign>(`/api/condor/campaigns/${id}/handover`, {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["condor"] });
      void qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });
}

// ---- tickets and execution (plan sections 4 and 6a) -------------------------------------

export type TicketKind =
  | "suggested"
  | "roll_selected"
  | "iron_fly"
  | "add_tranche"
  | "close_side"
  | "close_selected"
  | "close_all"
  | "time_roll"
  | "blank";

export type TicketRow = {
  action: "Buy" | "Sell";
  strike: number;
  right: "Call" | "Put";
  quantity: number;
  /** DD-Mon-YYYY; null for the campaign's open cycle. */
  expiry: string | null;
};

export type TicketPreview = {
  kind: string;
  orders: (TicketRow & { opening: boolean; price: number | null })[];
  cash_inr: number;
  credit_points: number | null;
  after: {
    expiry: string;
    legs: CondorLeg[];
    ledger_cash_inr: number;
    net_delta_per_lot: number | null;
    worst_loss_inr: number | null;
    lower_breakeven: number | null;
    upper_breakeven: number | null;
  };
  margin_before_inr: number | null;
  margin_after_inr: number | null;
  indicative: boolean;
  warnings: string[];
  blocked: string[];
};

export type ExecutionStep = TicketRow & {
  seq: number;
  opening: boolean;
  status: "pending" | "sending" | "filled" | "partial" | "failed" | "not_sent" | "unaccounted" | string;
  order_id: string | null;
  filled: number;
  avg_price: number | null;
  error: string | null;
};

export type CondorExecution = {
  id: string;
  kind: string;
  status: "running" | "completed" | "stopped" | "unaccounted" | "failed" | "interrupted" | string;
  created_at: string;
  finished_at: string | null;
  steps: ExecutionStep[];
  message: string | null;
  warnings: string[];
};

export const TICKET_TEMPLATES: ReadonlyArray<{ kind: TicketKind; label: string; hint: string }> = [
  { kind: "suggested", label: "Suggested action", hint: "What the rules suggest now." },
  { kind: "roll_selected", label: "Roll selected legs", hint: "Move the ticked legs to a strike or a delta." },
  { kind: "iron_fly", label: "Convert to iron fly", hint: "The untested short to the tested strike." },
  { kind: "add_tranche", label: "Add tranche", hint: "A new condor at the entry deltas." },
  { kind: "close_side", label: "Close a side", hint: "Every call, or every put." },
  { kind: "close_selected", label: "Close selected", hint: "The ticked legs." },
  { kind: "close_all", label: "Close all", hint: "Shorts bought back, then wings sold." },
  { kind: "time_roll", label: "Time roll", hint: "Close this cycle and open the next." },
  { kind: "blank", label: "Blank", hint: "Any legs you like." },
];

/** True when the preview was computed for exactly these rows; any edit needs a fresh one. */
export function previewMatches(preview: TicketPreview | null, rows: TicketRow[], previewedFor: string | null): boolean {
  return Boolean(preview) && previewedFor === ticketKey(rows);
}

export function ticketKey(rows: TicketRow[]): string {
  return JSON.stringify(rows.map((r) => [r.action, r.strike, r.right, r.quantity, r.expiry ?? null]));
}

export function executionDone(e: CondorExecution | null | undefined): boolean {
  return Boolean(e) && e!.status !== "running";
}

export function useTicketTemplate(id: string) {
  return useMutation({
    mutationFn: (body: { kind: TicketKind; params?: Record<string, unknown> }) =>
      apiClient.post<{ kind: string; orders: (TicketRow & { opening: boolean })[]; note: string }>(
        `/api/condor/campaigns/${id}/ticket/template`,
        body,
      ),
  });
}

export function useTicketPreview(id: string) {
  return useMutation({
    mutationFn: (body: { kind: TicketKind; orders: TicketRow[] }) =>
      apiClient.post<TicketPreview>(`/api/condor/campaigns/${id}/ticket/preview`, body),
  });
}

export function useExecuteTicket(id: string) {
  const done = useCondorInvalidate();
  return useMutation({
    mutationFn: (body: { kind: TicketKind; orders: TicketRow[]; note?: string }) =>
      apiClient.post<CondorExecution>(`/api/condor/campaigns/${id}/ticket/execute`, body),
    onSuccess: done,
  });
}

export function useExecution(campaignId: string, executionId: string | null) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["condor", "execution", campaignId, executionId],
    queryFn: async ({ signal }) => {
      const e = await apiClient.get<CondorExecution>(
        `/api/condor/campaigns/${campaignId}/executions/${executionId}`,
        signal,
      );
      if (e.status !== "running") void qc.invalidateQueries({ queryKey: ["condor", "campaign", campaignId] });
      return e;
    },
    enabled: Boolean(executionId),
    refetchInterval: (q) => (q.state.data && q.state.data.status !== "running" ? false : 1_500),
  });
}

export function useExecutions(campaignId: string) {
  return useQuery({
    queryKey: ["condor", "executions", campaignId],
    queryFn: ({ signal }) =>
      apiClient.get<{ executions: CondorExecution[] }>(`/api/condor/campaigns/${campaignId}/executions`, signal),
    refetchInterval: 30_000,
  });
}
