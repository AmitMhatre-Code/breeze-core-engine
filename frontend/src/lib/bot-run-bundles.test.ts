import { describe, expect, it } from "vitest";
import {
  bundleRuns,
  customRangeError,
  istToday,
  presetRange,
  rangeDays,
} from "@/lib/bot-run-bundles";
import type { BotRun } from "@/lib/use-bots";

const W = "expiry_index_writer" as const;
const S = "momentum_long_scalper" as const;

function run(
  n: number,
  bot: BotRun["bot_type"],
  started: string,
  over: Partial<BotRun> = {},
): BotRun {
  return {
    id: `r${n}`,
    bot_type: bot,
    trigger: "schedule",
    status: "skipped",
    reason_code: "quote_unavailable",
    reason_text: "quote_unavailable",
    detail: null,
    started_at: started,
    finished_at: null,
    audit_log: null,
    ...over,
  };
}

const newestFirst = (runs: BotRun[]) => [...runs].reverse();

// These cases mirror backend/tests/test_bot_run_bundles.py: both sides must bundle alike.
describe("bundleRuns", () => {
  it("bundles back-to-back runs and splits on a different run of the same bot", () => {
    const bundles = bundleRuns(
      newestFirst([
        run(1, W, "2026-09-17 09:42:12"),
        run(2, W, "2026-09-17 10:00:25"),
        run(3, W, "2026-09-17 10:01:11", {
          trigger: "manual",
          status: "proposed",
          reason_code: "proposal_ready",
        }),
        run(4, W, "2026-09-17 10:16:27"),
        run(5, W, "2026-09-17 10:24:33"),
      ]),
    );
    expect(bundles.map((b) => [b.trigger, b.status, b.count])).toEqual([
      ["schedule", "skipped", 2],
      ["manual", "proposed", 1],
      ["schedule", "skipped", 2],
    ]);
    expect(bundles[0].latest.id).toBe("r5");
    expect(bundles[0].first_started_at).toBe("2026-09-17 10:16:27");
    expect(bundles[0].runs?.map((r) => r.id)).toEqual(["r5", "r4"]);
    expect(bundles[2].last_started_at).toBe("2026-09-17 10:00:25");
  });

  it("does not split a bundle on another bot's runs in between", () => {
    const bundles = bundleRuns(
      newestFirst([
        run(1, W, "2026-09-17 09:42:00"),
        run(2, S, "2026-09-17 09:42:30", { trigger: "session", status: "running" }),
        run(3, W, "2026-09-17 09:44:00"),
        run(4, S, "2026-09-17 09:45:00", { trigger: "session", status: "completed" }),
        run(5, W, "2026-09-17 09:46:00"),
      ]),
    );
    expect(bundles.map((b) => [b.bot_type, b.status, b.count])).toEqual([
      [W, "skipped", 3],
      [S, "completed", 1],
      [S, "running", 1],
    ]);
  });

  it("starts a new bundle on a new day", () => {
    const bundles = bundleRuns(
      newestFirst([run(1, W, "2026-09-16 15:20:00"), run(2, W, "2026-09-17 09:16:00")]),
    );
    expect(bundles.map((b) => [b.date, b.count])).toEqual([
      ["2026-09-17", 1],
      ["2026-09-16", 1],
    ]);
  });

  it("counts distinct reasons", () => {
    const [bundle] = bundleRuns(
      newestFirst([
        run(1, W, "2026-09-17 09:42:00"),
        run(2, W, "2026-09-17 09:44:00", { reason_code: "no_signal" }),
        run(3, W, "2026-09-17 09:46:00"),
      ]),
    );
    expect([bundle.count, bundle.distinct_reasons]).toEqual([3, 2]);
  });

  it("puts the day's audit link on a live bundle but keeps backtest links per run", () => {
    const bundles = bundleRuns(
      newestFirst([
        run(1, W, "2026-09-17 09:42:00", { audit_log: "day.jsonl" }),
        run(2, W, "2026-09-17 09:44:00", { audit_log: "day.jsonl" }),
        run(3, S, "2026-09-17 09:50:00", { trigger: "backtest", status: "completed", audit_log: "bt3" }),
        run(4, S, "2026-09-17 09:55:00", { trigger: "backtest", status: "completed", audit_log: "bt4" }),
      ]),
    );
    const byBot = Object.fromEntries(bundles.map((b) => [b.bot_type, b]));
    expect(byBot[W].audit_log).toBe("day.jsonl");
    expect(byBot[S].audit_log).toBeNull();
    const [lone] = bundleRuns([run(4, S, "2026-09-17 09:55:00", { trigger: "backtest", status: "completed", audit_log: "bt4" })]);
    expect(lone.audit_log).toBe("bt4");
  });
});

describe("date ranges", () => {
  it("reads today in IST, not UTC", () => {
    // 20:00 UTC on the 16th is 01:30 IST on the 17th.
    expect(istToday(new Date("2026-09-16T20:00:00Z"))).toBe("2026-09-17");
  });

  it("uses rolling windows ending today", () => {
    expect(presetRange("today", "2026-09-17")).toEqual({ from: "2026-09-17", to: "2026-09-17" });
    expect(presetRange("week", "2026-09-17")).toEqual({ from: "2026-09-11", to: "2026-09-17" });
    expect(presetRange("month", "2026-03-01")).toEqual({ from: "2026-01-31", to: "2026-03-01" });
    expect(rangeDays(presetRange("month", "2026-09-17"))).toBe(30);
  });

  it("validates custom ranges", () => {
    const today = "2026-09-17";
    expect(customRangeError({ from: "2026-08-18", to: today }, today)).toBeNull();
    expect(customRangeError({ from: "2026-08-17", to: today }, today)).toMatch(/31 days/);
    expect(customRangeError({ from: today, to: "2026-09-16" }, today)).toMatch(/before/);
    expect(customRangeError({ from: today, to: "2026-09-18" }, today)).toMatch(/future/);
    expect(customRangeError({ from: "", to: today }, today)).toMatch(/both/);
  });
});
