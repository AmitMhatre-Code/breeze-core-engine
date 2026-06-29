/** Outlook → template discovery. */
export type Outlook = "bullish" | "bearish" | "neutral" | "volatile";

export type OptionRight = "Call" | "Put";
export type OrderSide = "Buy" | "Sell";

/** One leg in the builder (quantities in lots; premium optional mid at entry). */
export type StrategyLeg = {
  id: string;
  right: OptionRight;
  side: OrderSide;
  strike: number;
  /** Number of lots (scaled by global multiplier). */
  lots: number;
  /** Entry premium per unit (same units as chain LTP). */
  premiumPerUnit?: number;
  /** When true, broker derives limit price from LTP (no user price). */
  aggressiveLimit?: boolean;
};

export type ChainRow = {
  strike_price: number;
  call?: Record<string, unknown> | null;
  put?: Record<string, unknown> | null;
};

export type QuoteSource = "websocket" | "bhavcopy" | "icici_api";

export type QuoteMeta = {
  quote_source: QuoteSource;
  bhavcopy_date?: string | null;
  quote_as_of?: string | null;
};

export type ChainSuccess = {
  chain_rows: ChainRow[];
  spot_price: number | null;
  atm_strike: number | null;
  expiry_display: string;
  stock_code: string;
  exchange_code: string;
  max_call_oi?: number;
  max_put_oi?: number;
  /** Contract lot size for this underlying + expiry (from scrip master). */
  lot_size?: number | null;
  /** Max order quantity per exchange freeze rule (multiple of lot size). */
  freeze_quantity?: number | null;
  quote_source?: QuoteSource;
  bhavcopy_date?: string | null;
  quote_as_of?: string | null;
};

export type ChainApiResponse = {
  Status: number;
  Error?: string | null;
  Success?: ChainSuccess | null;
};

export type UnderlyingEntry = {
  stock_code: string;
  long_name?: string;
  expiry_dates: string[];
};

export type UnderlyingsApiResponse = {
  underlyings: UnderlyingEntry[];
};

export type MarginApiResponse = {
  Status: number;
  Error?: string | null;
  Success?: Record<string, unknown> | null;
};

export type MarginApiRequest = {
  legs: {
    stock_code: string;
    exchange_code: string;
    expiry_date: string;
    product_type: string;
    right: OptionRight;
    strike_price: string;
    quantity: string;
    price: string;
    action: OrderSide;
    aggressive_limit?: boolean;
  }[];
  margin_source?: "breeze_api" | "exchange_baseline";
  baseline_only?: boolean;
};

export type BasketLegMarginEntry = {
  lots: number;
  span: number | null;
  loading?: boolean;
};

export type SpanBaselineContract = {
  margin_per_lot: number;
  lot_size: number;
};

export type SpanBaselineSheet = {
  found: boolean;
  contracts: Record<string, SpanBaselineContract>;
  source_date?: string | null;
  source_file?: string | null;
};

export type ExecuteLegResult = {
  index: number;
  success: boolean;
  idempotency_key?: string | null;
  messages: Record<string, unknown>[];
  error?: string | null;
};

export type ExecuteApiResponse = {
  legs: ExecuteLegResult[];
  placed_count: number;
  failed_count: number;
};

export type ProposedTradeLeg = {
  right: OptionRight;
  side: OrderSide;
  strike: number;
  quantity: number;
  premium_per_unit: number;
  ltp?: number | null;
  best_bid_price?: number | null;
  best_offer_price?: number | null;
  total_buy_qty?: number | null;
  total_sell_qty?: number | null;
  buy_sell_ratio?: number | string | null;
};

export type StrategyCategory = "income" | "bullish" | "bearish";

export type BuilderMode = StrategyCategory | "build_your_own" | null;

export type RiskRewardProfile = "conservative" | "moderate" | "aggressive";

export type TileMetric = {
  label: string;
  value: string;
};

export type ProposedTrade = {
  strategy_id: string;
  strategy_name: string;
  status: "ok" | "skipped";
  skip_reason?: string | null;
  structure_modified?: boolean;
  net_premium?: number | null;
  max_loss?: number | null;
  annualized_return_pct?: number | null;
  risk_reward_ratio?: string | null;
  span_margin?: number | null;
  elm_requirement?: number | null;
  pop_pct?: number | null;
  legs: ProposedTradeLeg[];
  variant_rank?: number | null;
  engine_score?: number | null;
  ranking_summary?: string | null;
  score_breakdown?: Record<string, number> | null;
  conviction_profile?: RiskRewardProfile | null;
  hero_metric?: TileMetric | null;
  secondary_metrics?: TileMetric[];
  badges?: string[];
  compliance?: "recommended" | "relaxed";
  constraint_violations?: string[];
};

/** Unique key for trade card selection (supports conviction variants). */
export function tradeSelectionKey(trade: ProposedTrade): string {
  if (trade.conviction_profile) {
    return `${trade.strategy_id}#${trade.conviction_profile}`;
  }
  if (trade.variant_rank != null && trade.variant_rank > 0) {
    return `${trade.strategy_id}#${trade.variant_rank}`;
  }
  return trade.strategy_id;
}

export type ProposeTradesSuccess = {
  spot_price: number | null;
  lot_size: number;
  expiry_display: string;
  atm_iv?: number | null;
  structure_modified?: boolean;
  trades: ProposedTrade[];
  relaxed_trades?: ProposedTrade[];
  /** Server-side audit session id for downloading the build audit JSON. */
  audit_session_id?: string | null;
  user_report?: UserExplainabilityReport | null;
};

export type UserInputsSummary = {
  strategy_category: string;
  margin_lacs: number;
  max_loss_lacs?: number | null;
  allow_infinite_loss?: boolean;
  min_pop_pct?: number;
  min_ann_return_pct?: number;
};

export type StrategyRef = {
  strategy_id: string;
  strategy_name: string;
};

export type SkippedStrategySummary = {
  strategy_id: string;
  strategy_name: string;
  primary_reason: string;
  summary: string;
};

export type ExecutiveSummary = {
  user_inputs: UserInputsSummary;
  strategies_evaluated: number;
  strategies_recommended: StrategyRef[];
  strategies_skipped: SkippedStrategySummary[];
  generation_duration_seconds?: number | null;
};

export type FunnelStage = {
  stage: string;
  label: string;
  count: number | string;
};

export type BadgeExplanation = {
  badge: string;
  rationale: string;
};

export type TradeMetrics = {
  pop_pct?: number | null;
  net_credit?: number | null;
  annualized_return_pct?: number | null;
  margin?: number | null;
};

export type ReturnedTradeExplanation = {
  strategy_name: string;
  variant_rank?: number | null;
  conviction_profile?: RiskRewardProfile | null;
  badges?: string[];
  badge_explanations?: BadgeExplanation[];
  metrics: TradeMetrics;
  ranking_summary?: string | null;
};

export type WhyThisStrategy = {
  strategy_id: string;
  strategy_name: string;
  funnel: FunnelStage[];
  pop_filter_note?: string | null;
  returned_trades: ReturnedTradeExplanation[];
};

export type WhyNotStrategy = {
  strategy_id: string;
  strategy_name: string;
  primary_reason: string;
  explanation: string;
  funnel: FunnelStage[];
};

export type WhatIfInsight = {
  constraint: string;
  current_value?: number | null;
  suggested_change?: number | null;
  affected_strategies: string[];
  message: string;
};

export type UserExplainabilityReport = {
  user_report_schema_version: string;
  executive_summary: ExecutiveSummary;
  why_this: WhyThisStrategy[];
  why_not: WhyNotStrategy[];
  what_if_insights: WhatIfInsight[];
};

/** Level 1–3 slices returned by audit log explainability API. */
export type AuditExplainabilityLevels = {
  session_id: string;
  level_1: ExecutiveSummary;
  level_2: {
    why_this: WhyThisStrategy[];
    why_not: WhyNotStrategy[];
  };
  level_3: WhatIfInsight[];
};

export type ProposeTradesApiResponse = {
  Status: number;
  Error?: string | null;
  Success?: ProposeTradesSuccess | null;
};

export type ProposeTradesJobStartResponse = {
  Status: number;
  Error?: string | null;
  Success?: { job_id: string } | null;
};

export type ProposeTradesJobStatus = "queued" | "running" | "done" | "error";

export type ProposeTradesJobStatusSuccess = {
  job_id: string;
  status: ProposeTradesJobStatus;
  phase: string;
  message: string;
  progress_pct: number;
  progress_current: number;
  progress_total: number;
  result?: ProposeTradesSuccess | null;
  error?: string | null;
};

export type ProposeTradesJobStatusResponse = {
  Status: number;
  Error?: string | null;
  Success?: ProposeTradesJobStatusSuccess | null;
};
