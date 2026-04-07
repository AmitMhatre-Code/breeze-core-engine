import { apiClient } from "@/lib/api-client";

export type BreakOrderChunkResponse = {
  terminal_messages?: Array<{ type?: string; message?: string }>;
  chunk_index: number;
  total_chunks: number;
  contract_label?: string;
  price_f?: number;
  rate_limited: boolean;
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
};

/**
 * Place a split order chunk-by-chunk; on 429, calls onRateLimitWait (countdown UI) and retries the same chunk.
 */
export async function runBreakOrderChunks(
  args: PlaceBreakOrderArgs,
): Promise<{ ok: boolean; terminalError?: string; redirect?: string }> {
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
    ...(args.chunk_qty != null && String(args.chunk_qty).trim() !== ""
      ? { chunk_qty: String(args.chunk_qty).trim() }
      : {}),
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

    totalChunks = res.total_chunks;
    if (totalChunks <= 0) {
      return { ok: false, terminalError: "Nothing to place." };
    }

    if (res.rate_limited) {
      const sec = Math.max(
        1,
        Math.floor(Number(res.rate_limit_pause_seconds) || 5),
      );
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
  });
  return { ok: true, redirect: fin.redirect };
}

export type CancelOneResponse = {
  success: boolean;
  rate_limited: boolean;
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
        const sec = Math.max(
          1,
          Math.floor(Number(res.rate_limit_pause_seconds) || 5),
        );
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
