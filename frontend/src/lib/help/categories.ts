export type HelpCategory =
  | "account"
  | "orders"
  | "quotes"
  | "strategy"
  | "settings"
  | "product";

export const HELP_CATEGORY_ORDER: HelpCategory[] = [
  "account",
  "orders",
  "quotes",
  "strategy",
  "settings",
  "product",
];

export const HELP_CATEGORY_LABELS: Record<HelpCategory, string> = {
  account: "Account & login",
  orders: "Orders & execution",
  quotes: "Market data & quotes",
  strategy: "Strategy Builder",
  settings: "Settings & setup",
  product: "Product",
};
