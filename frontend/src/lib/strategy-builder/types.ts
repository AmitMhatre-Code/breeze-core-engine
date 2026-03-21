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
