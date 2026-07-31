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
  /** Fired after each successfully-placed (non-rate-limited) chunk, for progress UI. */
  onChunkPlaced?: (info: {
    chunkIndex: number;
    totalChunks: number;
    placedQuantity: number;
    totalQty: number;
  }) => void;
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

    args.onChunkPlaced?.({
      chunkIndex: res.chunk_index,
      totalChunks,
      placedQuantity: successes.reduce((a, b) => a + b, 0),
      totalQty: Number(args.total_qty) || 0,
    });

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
  /** Fired after each order's cancel call resolves (success or failure), for progress UI. */
  onOrderCancelled?: (info: { orderIndex: number; totalOrders: number }) => void;
}): Promise<{ redirect?: string }> {
  const results: { order_ref: string; success: boolean; error?: string }[] = [];

  for (let i = 0; i < args.orderIds.length; i++) {
    const oid = args.orderIds[i]!;
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
    args.onOrderCancelled?.({ orderIndex: i, totalOrders: args.orderIds.length });
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

export type LegModifyOrderRef = {
  order_id: string;
  exchange_code: string;
  quantity: number;
  pending_quantity: number;
  status: string;
  price?: string | null;
};

export type LegModifyOrderOutcome = {
  order_id: string;
  quantity: number;
  price?: string | null;
};

export type LegModifyFailure = { ref: string; error: string };

export type LegModifyResponse = {
  success: boolean;
  cancelled_order_ids: string[];
  modified: LegModifyOrderOutcome[];
  placed: LegModifyOrderOutcome[];
  failures: LegModifyFailure[];
};

export type LegModifyStepResponse = {
  total_steps: number;
  step_index: number;
  done: boolean;
  op?: "cancel" | "modify" | "place" | null;
  order_id?: string | null;
  quantity?: number | null;
  price?: string | null;
  success: boolean;
  error?: string | null;
  rate_limited: boolean;
  rate_limit_pause_seconds?: number | null;
};

export type ModifyLegArgs = {
  stock_code: string;
  expiry_date: string;
  strike_price: string;
  right: string;
  product_type: string;
  exchange_code: string;
  action: "Buy" | "Sell";
  orders: LegModifyOrderRef[];
  new_quantity: string;
  new_price?: string;
  rule_id?: string;
  scrip_key?: string;
  onRateLimitWait: (seconds: number) => Promise<void>;
  /** Fired after each step completes (success or failure) but not on a rate-limited retry. */
  onStepDone?: (info: { stepIndex: number; totalSteps: number }) => void;
};

/**
 * Modify a leg's open quantity/price by driving the plan one broker-call-sized step (cancel,
 * modify, or place) at a time — the backend recomputes the same deterministic plan on every
 * call, so a step_index only advances once its step has genuinely succeeded. This mirrors
 * `runBreakOrderChunks`'s chunk-by-chunk loop, giving the caller live per-order progress instead
 * of a single opaque round trip. Once every step is done, one finalize call persists messages,
 * audit-adjacent side effects, and exit-rule leg bookkeeping from the accumulated outcome.
 */
export async function runModifyLegWithPacing(
  args: ModifyLegArgs,
): Promise<LegModifyResponse> {
  const { onRateLimitWait, onStepDone, orders, rule_id, scrip_key, ...base } = args;

  const cancelled_order_ids: string[] = [];
  const modified: LegModifyOrderOutcome[] = [];
  const placed: LegModifyOrderOutcome[] = [];
  const failures: LegModifyFailure[] = [];

  let stepIndex = 0;
  let totalSteps = Infinity;
  while (stepIndex < totalSteps) {
    const res = await apiClient.post<LegModifyStepResponse>("/book/modify-leg-step", {
      stock_code: base.stock_code,
      expiry_date: base.expiry_date,
      strike_price: base.strike_price,
      right: base.right,
      product_type: base.product_type,
      exchange_code: base.exchange_code,
      action: base.action,
      orders,
      new_quantity: base.new_quantity,
      new_price: base.new_price,
      step_index: stepIndex,
    });
    totalSteps = res.total_steps;
    if (res.done || stepIndex >= totalSteps) break;

    if (res.rate_limited) {
      const sec = rateLimitWaitSeconds(res.rate_limit_pause_seconds);
      await onRateLimitWait(sec);
      continue;
    }

    if (res.op === "cancel" && res.order_id) {
      if (res.success) {
        cancelled_order_ids.push(res.order_id);
      } else {
        failures.push({ ref: res.order_id, error: res.error ?? "Unknown error" });
      }
    } else if (res.op === "modify" && res.order_id) {
      if (res.success) {
        modified.push({ order_id: res.order_id, quantity: res.quantity ?? 0, price: res.price ?? null });
      } else {
        failures.push({ ref: res.order_id, error: res.error ?? "Unknown error" });
      }
    } else if (res.op === "place") {
      if (res.success && res.order_id) {
        placed.push({ order_id: res.order_id, quantity: res.quantity ?? 0, price: res.price ?? null });
      } else {
        failures.push({
          ref: `new order (qty=${res.quantity ?? "?"})`,
          error: res.error ?? "Unknown error",
        });
      }
    }

    onStepDone?.({ stepIndex, totalSteps });
    stepIndex += 1;
  }

  return apiClient.post<LegModifyResponse>("/book/modify-leg-finalize", {
    stock_code: base.stock_code,
    expiry_date: base.expiry_date,
    strike_price: base.strike_price,
    right: base.right,
    product_type: base.product_type,
    exchange_code: base.exchange_code,
    action: base.action,
    orders,
    new_quantity: Number(base.new_quantity),
    new_price: base.new_price,
    cancelled_order_ids,
    modified,
    placed,
    failures,
    rule_id,
    scrip_key,
  });
}
