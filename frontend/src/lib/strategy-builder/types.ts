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
};

export type ChainRow = {
  strike_price: number;
  call?: Record<string, unknown> | null;
  put?: Record<string, unknown> | null;
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

export type RiskRewardProfile = "conservative" | "moderate" | "aggressive";

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
};

export type ProposeTradesSuccess = {
  spot_price: number | null;
  lot_size: number;
  expiry_display: string;
  atm_iv?: number | null;
  structure_modified?: boolean;
  trades: ProposedTrade[];
  /** Server-side audit session id for downloading the build audit JSON. */
  audit_session_id?: string | null;
};

export type ProposeTradesApiResponse = {
  Status: number;
  Error?: string | null;
  Success?: ProposeTradesSuccess | null;
};
