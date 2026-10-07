import { describe, expect, it } from "vitest";
import { casBingoAttention } from "@/lib/bot-attention";
import type { BotRun } from "@/lib/use-bots";

function run(over: Partial<BotRun>): BotRun {
  return {
    id: "r1",
    bot_type: "cas_bingo",
    trigger: "session",
    status: "running",
    reason_code: null,
    reason_text: null,
    detail: null,
    started_at: "2026-10-07 09:15:00",
    finished_at: null,
    audit_log: null,
    ...over,
  } as BotRun;
}

describe("casBingoAttention", () => {
  it("stays quiet on a normal verdict", () => {
    expect(
      casBingoAttention(run({ reason_code: "outside_session_window", reason_text: "NIFTY: before 15:15." })),
    ).toBeNull();
    expect(casBingoAttention(null)).toBeNull();
  });

  it("flags a problem the bot cannot fix as bad", () => {
    expect(casBingoAttention(run({ reason_code: "no_broker_session", reason_text: "Log in to ICICI." }))).toEqual({
      text: "Log in to ICICI.",
      tone: "bad",
    });
  });

  it("lets the worse index set the tone and names only the flagged ones", () => {
    const summary = casBingoAttention(
      run({
        reason_code: "outside_session_window",
        detail: {
          indices: {
            NIFTY: { reason_code: "chain_not_ready", reason_text: "NIFTY: chain loading." },
            BSESEN: { reason_code: "order_rejected", reason_text: "SENSEX: order rejected." },
            X: { reason_code: "signal_no_trade", reason_text: "X: no trade." },
          },
        },
      }),
    );
    expect(summary).toEqual({ text: "SENSEX: order rejected.", tone: "bad" });
  });

  it("flags a working-but-blocked verdict as a warning", () => {
    expect(
      casBingoAttention(
        run({ detail: { indices: { NIFTY: { reason_code: "chain_not_ready", reason_text: "NIFTY: chain loading." } } } }),
      )?.tone,
    ).toBe("warn");
  });
});
