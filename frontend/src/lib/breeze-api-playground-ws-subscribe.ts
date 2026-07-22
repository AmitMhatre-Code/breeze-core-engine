export type WsSubscribeMode =
  | "fno_quotes"
  | "fno_ohlcv"
  | "cash_quotes"
  | "token_quotes"
  | "token_ohlcv"
  | "order_notifications";

export const WS_SUBSCRIBE_MODE_OPTIONS: { value: WsSubscribeMode; label: string }[] = [
  { value: "fno_quotes", label: "F&O exchange quotes" },
  { value: "fno_ohlcv", label: "F&O OHLCV (interval)" },
  { value: "cash_quotes", label: "Cash / index exchange quotes" },
  { value: "token_quotes", label: "Stock token exchange quotes" },
  { value: "token_ohlcv", label: "Stock token OHLCV" },
  { value: "order_notifications", label: "Order notifications" },
];

const FNO_CONTRACT_FIELDS = [
  "exchange_code",
  "stock_code",
  "expiry_date",
  "strike_price",
  "right",
  "product_type",
  "get_market_depth",
  "get_exchange_quotes",
] as const;

export const WS_SUBSCRIBE_MODES: Record<WsSubscribeMode, readonly string[]> = {
  fno_quotes: FNO_CONTRACT_FIELDS,
  fno_ohlcv: [...FNO_CONTRACT_FIELDS, "interval"],
  cash_quotes: [
    "exchange_code",
    "stock_code",
    "product_type",
    "get_market_depth",
    "get_exchange_quotes",
  ],
  token_quotes: ["stock_token"],
  token_ohlcv: ["stock_token", "interval"],
  order_notifications: ["get_order_notification"],
};

export const WS_FIELD_PLACEHOLDERS: Record<string, string> = {
  stock_token: "4.1!2885 or ['4.1!3499','4.1!2885']",
  exchange_code: "NFO / BFO",
  stock_code: "NIFTY / BSESEN",
  product_type: "options / cash / futures",
  expiry_date: "13-Feb-2025",
  strike_price: "23550",
  right: "call",
  get_market_depth: "true / false",
  get_exchange_quotes: "true / false",
  interval: "1minute",
  get_order_notification: "true",
};

export const WS_FORM_INITIAL: Record<string, string> = Object.fromEntries(
  [...new Set(Object.values(WS_SUBSCRIBE_MODES).flat())].map((key) => [key, ""]),
);

export function buildWsSubscribeParams(
  form: Record<string, string>,
  holderId: string,
  mode: WsSubscribeMode,
): Record<string, string> {
  const out: Record<string, string> = { holder_id: holderId };
  for (const key of WS_SUBSCRIBE_MODES[mode]) {
    const value = form[key] ?? "";
    if (value.trim()) out[key] = value.trim();
  }
  return out;
}
