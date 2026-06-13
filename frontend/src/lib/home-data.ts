/** Matches GET /home/data (customer + margin from ICICI). */

export type HomeDataResponse = {
  customer: Record<string, unknown>;
  margin: Record<string, unknown>;
  api_calls_today?: number;
  api_calls_limit?: number;
  api_usage_band?: "green" | "amber" | "red" | string;
  api_usage_warning?: string | null;
  api_usage_blocked?: boolean;
  deployment_license_status?: "active" | "expired" | "revoked";
  deployment_license_read_only?: boolean;
};

function readSuccess(
  block: Record<string, unknown>,
): Record<string, unknown> | null {
  const s = block.Success;
  return s && typeof s === "object" && !Array.isArray(s)
    ? (s as Record<string, unknown>)
    : null;
}

/** Display name from get_customer_details Success payload. */
export function getCustomerDisplayName(
  customer: Record<string, unknown> | undefined,
): string | null {
  if (!customer) return null;
  const o = readSuccess(customer);
  if (!o) return null;
  const candidates = [
    o.idirect_user_name,
    o.Idirect_user_name,
    o.user_name,
    o.name,
  ];
  for (const c of candidates) {
    if (typeof c === "string" && c.trim()) return c.trim();
  }
  return null;
}

/** Latest available margin (cash + MTF / limit list) from get_margin_situation. */
export function getAvailableMargin(
  margin: Record<string, unknown> | undefined,
): number | null {
  if (!margin || margin.Status !== 200) return null;
  const o = readSuccess(margin);
  if (!o) return null;
  const v = o.actual_margin_avl;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** For account overview tiles: funds = total available; margin used ≈ non-cash / limit-list component. */
export function getHomeMarginTiles(margin: Record<string, unknown> | undefined): {
  funds: number | null;
  marginUsed: number | null;
} {
  if (!margin || margin.Status !== 200) return { funds: null, marginUsed: null };
  const o = readSuccess(margin);
  if (!o) return { funds: null, marginUsed: null };
  const avl = num(o.actual_margin_avl);
  const cash = num(o.cash_limit);
  const ute = num(o.actual_margin_ute);
  const funds = avl;
  let marginUsed: number | null = null;
  if (avl != null && cash != null) marginUsed = Math.max(0, avl - cash);
  else if (ute != null) marginUsed = Math.max(0, Math.abs(ute));
  return { funds, marginUsed };
}

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}
