import type { ParkedOrderListItem } from "@/lib/parked-orders";
import type { OptionRight, OrderSide } from "@/lib/strategy-builder/types";

export type PlaceOrderSegment = "NFO" | "BFO";

export type PlaceOrderClonePayload = {
  segment: PlaceOrderSegment;
  stock_code: string;
  expiry_date: string;
  right: OptionRight;
  strike_price: number;
  quantity: string;
  price: string;
  action: OrderSide;
};

const STORAGE_KEY = "breeze_place_order_clone_v1";

/** Subset of book order JSON used to build a clone payload. */
export type BookOrderRowLike = {
  option?: string;
  exchange_code?: string;
  action?: string;
  quantity?: number | string;
  price?: number | string;
  open_quantity?: number | string;
  stock_code?: string;
  expiry_date?: string;
  strike_price?: number | string;
  right?: string;
};

function normExchange(seg: string | undefined): PlaceOrderSegment {
  const u = String(seg ?? "").trim().toUpperCase();
  if (u === "BFO") return "BFO";
  return "NFO";
}

function normRight(raw: string | undefined): OptionRight | null {
  const t = String(raw ?? "").trim();
  const lower = t.toLowerCase();
  if (t === "Call" || lower === "call") return "Call";
  if (t === "Put" || lower === "put") return "Put";
  const u = t.toUpperCase();
  if (u === "CE") return "Call";
  if (u === "PE") return "Put";
  return null;
}

function normAction(raw: string | undefined): OrderSide | null {
  const t = String(raw ?? "").trim().toLowerCase();
  if (t === "buy" || t.startsWith("buy")) return "Buy";
  if (t === "sell" || t.startsWith("sell")) return "Sell";
  return null;
}

/**
 * Backend: stock_code + "-" + expiry_date + "-" + int(strike) + "-" + right
 * Expiry may contain hyphens; parse strike + right from the end first.
 */
function parseOptionColumn(option: string): {
  stock_code: string;
  expiry_date: string;
  strike_price: number;
  right: OptionRight;
} | null {
  const m = option.match(/^(.+)-(\d+)-(Call|Put)$/i);
  if (!m) return null;
  const rest = m[1]!.trim();
  const strike = Number(m[2]);
  const right = normRight(m[3]);
  if (!Number.isFinite(strike) || !right) return null;
  const dash = rest.indexOf("-");
  if (dash < 1) return null;
  const stock_code = rest.slice(0, dash).trim();
  const expiry_date = rest.slice(dash + 1).trim();
  if (!stock_code || !expiry_date) return null;
  return { stock_code, expiry_date, strike_price: Math.round(strike), right };
}

function quantityFromRow(row: BookOrderRowLike): number | null {
  const raw = row.quantity;
  if (typeof raw === "number" && Number.isFinite(raw))
    return Math.max(0, Math.trunc(raw));
  const n = parseInt(String(raw ?? "").replace(/,/g, ""), 10);
  return Number.isFinite(n) ? Math.max(0, n) : null;
}

function priceStringFromRow(row: BookOrderRowLike): string {
  const p = row.price;
  if (p == null || p === "") return "";
  const pn = typeof p === "number" ? p : parseFloat(String(p).replace(/,/g, ""));
  if (!Number.isFinite(pn) || pn < 0) return "";
  return String(Number(pn.toFixed(4)));
}

/** Clone-to-place from a parked row; optional edit state uses current qty/price inputs. */
export function buildPlaceOrderCloneFromParkedRow(
  row: ParkedOrderListItem,
  edit?: { quantity: string; price: string },
): PlaceOrderClonePayload | null {
  const action = normAction(row.action);
  if (!action) return null;
  const right = normRight(row.right);
  if (!right) return null;
  const strikeNum = parseFloat(String(row.strike_price).replace(/,/g, ""));
  if (!Number.isFinite(strikeNum)) return null;
  const qtyRaw = edit?.quantity ?? row.quantity;
  const q = parseInt(String(qtyRaw).replace(/,/g, "").trim(), 10);
  if (!Number.isFinite(q) || q <= 0) return null;
  const stock = String(row.stock_code ?? "").trim();
  const expiry = String(row.expiry_date ?? "").trim();
  if (!stock || !expiry) return null;
  const pRaw = edit?.price ?? row.price;
  let price = "";
  if (pRaw != null && String(pRaw).trim() !== "") {
    const pn = parseFloat(String(pRaw).replace(/,/g, ""));
    if (Number.isFinite(pn) && pn >= 0)
      price = String(Number(pn.toFixed(4)));
  }
  return {
    segment: normExchange(row.exchange_code),
    stock_code: stock,
    expiry_date: expiry,
    right,
    strike_price: Math.round(strikeNum),
    quantity: String(q),
    price,
    action,
  };
}

export function buildPlaceOrderCloneFromBookRow(
  row: BookOrderRowLike,
): PlaceOrderClonePayload | null {
  const action = normAction(row.action);
  if (!action) return null;

  let stock = String(row.stock_code ?? "").trim();
  let expiry = String(row.expiry_date ?? "").trim();
  let right = row.right != null ? normRight(String(row.right)) : null;
  let strikeNum: number | null = null;
  const sp = row.strike_price;
  if (sp != null && sp !== "") {
    const n =
      typeof sp === "number" ? sp : parseFloat(String(sp).replace(/,/g, ""));
    strikeNum = Number.isFinite(n) ? Math.round(n) : null;
  }

  if (!stock || !expiry || strikeNum == null || !right) {
    const parsed = parseOptionColumn(String(row.option ?? "").trim());
    if (!parsed) return null;
    if (!stock) stock = parsed.stock_code;
    if (!expiry) expiry = parsed.expiry_date;
    if (strikeNum == null) strikeNum = parsed.strike_price;
    if (!right) right = parsed.right;
  }

  if (!stock || !expiry || strikeNum == null || !right) return null;

  const q = quantityFromRow(row);
  if (q == null || q <= 0) return null;

  return {
    segment: normExchange(row.exchange_code),
    stock_code: stock,
    expiry_date: expiry,
    right,
    strike_price: strikeNum,
    quantity: String(q),
    price: priceStringFromRow(row),
    action,
  };
}

export function setPlaceOrderClonePayload(p: PlaceOrderClonePayload): void {
  try {
    if (typeof sessionStorage === "undefined") return;
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    /* quota / private mode */
  }
}

function isPayload(v: unknown): v is PlaceOrderClonePayload {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  const seg = o.segment;
  if (seg !== "NFO" && seg !== "BFO") return false;
  if (typeof o.stock_code !== "string" || !o.stock_code.trim()) return false;
  if (typeof o.expiry_date !== "string" || !o.expiry_date.trim()) return false;
  if (o.right !== "Call" && o.right !== "Put") return false;
  if (typeof o.strike_price !== "number" || !Number.isFinite(o.strike_price))
    return false;
  if (typeof o.quantity !== "string" || !o.quantity.trim()) return false;
  if (typeof o.price !== "string") return false;
  if (o.action !== "Buy" && o.action !== "Sell") return false;
  return true;
}

/** Read and remove stored clone payload (single use). */
export function consumePlaceOrderClonePayload(): PlaceOrderClonePayload | null {
  try {
    if (typeof sessionStorage === "undefined") return null;
    const raw = sessionStorage.getItem(STORAGE_KEY);
    sessionStorage.removeItem(STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as unknown;
    return isPayload(v) ? v : null;
  } catch {
    return null;
  }
}
