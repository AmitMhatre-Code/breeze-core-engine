import { describe, expect, it } from "vitest";

import {
  buildWsSubscribeParams,
  WS_SUBSCRIBE_MODES,
  type WsSubscribeMode,
} from "./breeze-api-playground-ws-subscribe";

const FULL_FORM = {
  stock_token: "4.1!2885",
  exchange_code: "NFO",
  stock_code: "NIFTY",
  product_type: "options",
  expiry_date: "13-Feb-2025",
  strike_price: "23550",
  right: "call",
  get_market_depth: "false",
  get_exchange_quotes: "true",
  interval: "1minute",
  get_order_notification: "true",
};

describe("buildWsSubscribeParams", () => {
  it.each<[WsSubscribeMode, string[]]>([
    ["fno_quotes", [...WS_SUBSCRIBE_MODES.fno_quotes]],
    ["fno_ohlcv", [...WS_SUBSCRIBE_MODES.fno_ohlcv]],
    ["cash_quotes", [...WS_SUBSCRIBE_MODES.cash_quotes]],
    ["token_quotes", [...WS_SUBSCRIBE_MODES.token_quotes]],
    ["token_ohlcv", [...WS_SUBSCRIBE_MODES.token_ohlcv]],
    ["order_notifications", [...WS_SUBSCRIBE_MODES.order_notifications]],
  ])("mode %s sends only its fields", (mode, allowedKeys) => {
    const params = buildWsSubscribeParams(FULL_FORM, "holder-1", mode);
    expect(params.holder_id).toBe("holder-1");
    const sentKeys = Object.keys(params).filter((k) => k !== "holder_id");
    expect(sentKeys.sort()).toEqual(allowedKeys.sort());
    if (mode !== "token_quotes" && mode !== "token_ohlcv") {
      expect(sentKeys).not.toContain("stock_token");
    }
    if (mode !== "fno_ohlcv" && mode !== "token_ohlcv") {
      expect(sentKeys).not.toContain("interval");
    }
    if (mode !== "order_notifications") {
      expect(sentKeys).not.toContain("get_order_notification");
    }
  });

  it("fno_quotes omits empty fields", () => {
    const params = buildWsSubscribeParams(
      {
        exchange_code: "NFO",
        stock_code: "NIFTY",
        expiry_date: "13-Feb-2025",
        strike_price: "23550",
        right: "call",
        product_type: "options",
        get_market_depth: "",
        get_exchange_quotes: "true",
      },
      "h1",
      "fno_quotes",
    );
    expect(params).toEqual({
      holder_id: "h1",
      exchange_code: "NFO",
      stock_code: "NIFTY",
      expiry_date: "13-Feb-2025",
      strike_price: "23550",
      right: "call",
      product_type: "options",
      get_exchange_quotes: "true",
    });
  });
});
