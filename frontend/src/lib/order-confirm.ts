/** Mirrors legacy `openOrderConfirmModal` / `OrderFormRequest` buy & sell fields. */
export type OrderConfirmPayload = {
  product_type: string;
  stock_code: string;
  exchange_code: string;
  expiry_date: string;
  right: string;
  strike_price: string;
  quantity: string;
  price: string;
  action: "Buy" | "Sell";
  aggressive_limit?: boolean;
};

export type PortfolioLikeRow = Record<string, unknown>;

/** Portfolio `ltp` → order price field (positive finite only; else "0"). */
export function ltpAsOrderPrice(raw: unknown): string {
  if (raw == null || raw === "") return "0";
  if (typeof raw === "number" && Number.isFinite(raw)) {
    if (raw <= 0) return "0";
    return String(Number(raw.toFixed(4)));
  }
  const t = String(raw)
    .trim()
    .replace(/[₹,\s]/g, "");
  if (!t || t === "*" || t === "—" || t === "-" || t.toLowerCase() === "err")
    return "0";
  const n = Number(t);
  if (!Number.isFinite(n) || n <= 0) return "0";
  return String(Number(n.toFixed(4)));
}

function normSide(raw: string): "Buy" | "Sell" | null {
  const t = raw.trim().toLowerCase();
  if (t === "buy") return "Buy";
  if (t === "sell") return "Sell";
  return null;
}

/** Close a long with Sell, close a short with Buy (legacy `route_order.serve_landing` SquareOff). */
export function squareOffToOrderPayload(
  row: PortfolioLikeRow,
): OrderConfirmPayload | null {
  const side = normSide(String(row.action ?? ""));
  if (!side) return null;
  const closeAction: "Buy" | "Sell" = side === "Buy" ? "Sell" : "Buy";
  const stock = String(row.stock_code ?? "").trim();
  const expiry = String(row.expiry_date ?? "").trim();
  const right = String(row.right ?? "").trim();
  const strike = String(row.strike_price ?? "").trim();
  const qty = String(row.quantity ?? "").trim();
  if (!stock || !expiry || !right || !strike || !qty) return null;
  return {
    product_type: String(row.product_type ?? "Options").trim() || "Options",
    stock_code: stock,
    exchange_code: String(row.exchange_code ?? "NFO").trim() || "NFO",
    expiry_date: expiry,
    right,
    strike_price: strike,
    quantity: qty,
    price: ltpAsOrderPrice(row.ltp),
    action: closeAction,
  };
}

/**
 * Parse `/orders?...` after backend maps `/order?...` (SquareOff, or direct Buy/Sell from strategies).
 */
export function prefillFromOrdersSearchParams(sp: {
  get: (name: string) => string | null;
}): OrderConfirmPayload | null {
  const actionRaw = (sp.get("action") ?? "").trim();
  const position = (sp.get("position") ?? "").trim();

  let action: "Buy" | "Sell" | null = null;
  if (actionRaw === "SquareOff") {
    const p = normSide(position);
    if (p === "Buy") action = "Sell";
    else if (p === "Sell") action = "Buy";
  } else if (actionRaw === "Buy" || actionRaw === "Sell") {
    action = actionRaw;
  }
  if (!action) return null;

  const stock = (sp.get("stock_code") ?? "").trim();
  const expiry = (sp.get("expiry_date") ?? "").trim();
  const right = (sp.get("right") ?? "").trim();
  const strike = (sp.get("strike_price") ?? "").trim();
  const qty = (sp.get("quantity") ?? "").trim();
  if (!stock || !expiry || !right || !strike || !qty) return null;

  const priceRaw = sp.get("price");
  const price =
    priceRaw != null && priceRaw !== "" ? priceRaw.trim() : "0";

  return {
    product_type: (sp.get("product_type") ?? "Options").trim() || "Options",
    stock_code: stock,
    exchange_code: (sp.get("exchange_code") ?? "NFO").trim() || "NFO",
    expiry_date: expiry,
    right,
    strike_price: strike,
    quantity: qty,
    price,
    action,
  };
}
