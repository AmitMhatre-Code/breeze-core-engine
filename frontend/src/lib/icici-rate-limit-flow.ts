import { apiClient } from "@/lib/api-client";

function rateLimitWaitSeconds(raw: unknown, fallback = 0): number {
  const n = Number(raw);
  return Math.max(0, Math.floor(Number.isFinite(n) ? n : fallback));
}

export type BreakOrderChunkResponse = {
  terminal_messages?: Array<{ type?: string; message?: string }>;
  chunk_index: number;
  total_chunks: number;
  contract_label?: string;
  price_f?: number;
  rate_limited: boolean;
  daily_limit_exhausted?: boolean;
  parked_for_execution?: boolean;
  parked_order_ids?: string[];
  market_closed_reason?: string;
  success: boolean;
  placed_quantity: number;
  danger_line: string | null;
  rate_limit_pause_seconds: number;
  default_chunk_qty?: number;
  effective_chunk_qty?: number;
};

export type PlaceBreakOrderArgs = {
  product_type: string;
  stock_code: string;
  exchange_code: string;
  expiry_date: string;
  right: string;
  strike_price: string;
  total_qty: string;
  price: string;
  action: "Buy" | "Sell";
  onRateLimitWait: (seconds: number) => Promise<void>;
  /** Max units per exchange order; server lot-rounds and caps at freeze. Omit for backend default. */
  chunk_qty?: string;
  aggressive_limit?: boolean;
  from_parked_execution?: boolean;
  batch_group_id?: string;
  /** When true, caller stores the parked info message after all legs (multi-leg). */
  defer_parked_finalize?: boolean;
};

export type RunBreakOrderResult = {
  ok: boolean;
  parked?: boolean;
  marketClosedReason?: string;
  terminalError?: string;
  redirect?: string;
};

/**
 * Place a split order chunk-by-chunk; on 429, calls onRateLimitWait (countdown UI) and retries the same chunk.
 */
export async function runBreakOrderChunks(
  args: PlaceBreakOrderArgs,
): Promise<RunBreakOrderResult> {
  const successes: number[] = [];
  const dangers: string[] = [];
  let chunk = 0;
  let totalChunks = 1;

  const base = {
    product_type: args.product_type,
    stock_code: args.stock_code,
    exchange_code: args.exchange_code,
    expiry_date: args.expiry_date,
    right: args.right,
    strike_price: args.strike_price,
    total_qty: args.total_qty,
    price: args.price,
    action: args.action,
    ...(args.aggressive_limit ? { aggressive_limit: true } : {}),
    ...(args.chunk_qty != null && String(args.chunk_qty).trim() !== ""
      ? { chunk_qty: String(args.chunk_qty).trim() }
      : {}),
    ...(args.from_parked_execution ? { from_parked_execution: true } : {}),
    ...(args.batch_group_id ? { batch_group_id: args.batch_group_id } : {}),
  };

  for (;;) {
    const res = await apiClient.post<BreakOrderChunkResponse>("/order/break-chunk", {
      ...base,
      chunk_index: chunk,
    });

    const terminal = res.terminal_messages?.filter((m) => (m.message ?? "").trim()) ?? [];
    if (terminal.length > 0) {
      return {
        ok: false,
        terminalError: terminal.map((m) => String(m.message ?? "")).join(" "),
      };
    }

    if (res.parked_for_execution) {
      if (!args.defer_parked_finalize) {
        const fin = await apiClient.post<{ redirect: string }>("/order/break-finalize", {
          stock_code: args.stock_code,
          expiry_date: args.expiry_date,
          right: args.right,
          strike_price: args.strike_price,
          product_type: args.product_type,
          exchange_code: args.exchange_code,
          price: args.price,
          action: args.action,
          parked_only: true,
          market_closed_reason: res.market_closed_reason ?? undefined,
        });
        return {
          ok: true,
          parked: true,
          marketClosedReason: res.market_closed_reason,
          redirect: fin.redirect,
        };
      }
      return {
        ok: true,
        parked: true,
        marketClosedReason: res.market_closed_reason,
      };
    }

    totalChunks = res.total_chunks;
    if (totalChunks <= 0) {
      return { ok: false, terminalError: "Nothing to place." };
    }

    if (res.rate_limited) {
      if (res.daily_limit_exhausted) {
        return {
          ok: false,
          terminalError:
            res.danger_line ??
            "You have been throttled by ICICI and have reached the daily API limit.",
        };
      }
      const sec = rateLimitWaitSeconds(res.rate_limit_pause_seconds);
      await args.onRateLimitWait(sec);
      continue;
    }

    if (res.success && res.placed_quantity > 0) {
      successes.push(res.placed_quantity);
    }
    if (res.danger_line) {
      dangers.push(res.danger_line);
    }

    if (chunk >= totalChunks - 1) {
      break;
    }
    chunk += 1;
  }

  const fin = await apiClient.post<{ redirect: string }>("/order/break-finalize", {
    stock_code: args.stock_code,
    expiry_date: args.expiry_date,
    right: args.right,
    strike_price: args.strike_price,
    product_type: args.product_type,
    exchange_code: args.exchange_code,
    price: args.price,
    action: args.action,
    success_quantities: successes,
    danger_lines: dangers,
    ...(args.aggressive_limit ? { aggressive_limit: true } : {}),
  });
  return { ok: true, redirect: fin.redirect };
}

export type CancelOneResponse = {
  success: boolean;
  rate_limited: boolean;
  daily_limit_exhausted?: boolean;
  error: string | null;
  rate_limit_pause_seconds: number;
};

export async function runCancelOrdersWithPacing(args: {
  orderIds: string[];
  cancel_details: { option: string; open_quantity: number }[];
  onRateLimitWait: (seconds: number) => Promise<void>;
}): Promise<{ redirect?: string }> {
  const results: { order_ref: string; success: boolean; error?: string }[] = [];

  for (const oid of args.orderIds) {
    for (;;) {
      const res = await apiClient.post<CancelOneResponse>("/book/cancel-one", {
        order_id: oid,
      });
      if (res.rate_limited) {
        if (res.daily_limit_exhausted) {
          results.push({
            order_ref: oid,
            success: false,
            error:
              res.error ??
              "You have been throttled by ICICI and have reached the daily API limit.",
          });
          break;
        }
        const sec = rateLimitWaitSeconds(res.rate_limit_pause_seconds);
        await args.onRateLimitWait(sec);
        continue;
      }
      results.push({
        order_ref: oid,
        success: res.success,
        error: res.error ?? undefined,
      });
      break;
    }
  }

  const metaOk =
    args.cancel_details.length > 0 &&
    args.cancel_details.length === args.orderIds.length;
  const fin = await apiClient.post<{ redirect: string }>("/book/cancel-commit", {
    results,
    cancel_details: metaOk ? args.cancel_details : undefined,
  });
  return { redirect: fin.redirect };
}
