import { describe, expect, it } from "vitest";
import {
  EARLIEST_SESSION_START,
  suggestWindow,
  validateSessions,
  warmupReadyAt,
  warmupWarning,
} from "@/lib/scalper-sessions";
import type { SessionWindow } from "@/lib/use-bots";

const w = (start: string, end: string): SessionWindow => ({ start, end });

describe("validateSessions", () => {
  it("accepts the shipped defaults", () => {
    expect(validateSessions([w("09:35", "11:30"), w("13:30", "15:10")], "15:15")).toBeNull();
  });

  it("accepts four user-chosen windows against a moved square-off", () => {
    const windows = [
      w("09:35", "10:30"),
      w("10:45", "11:30"),
      w("13:30", "14:15"),
      w("14:20", "14:45"),
    ];
    expect(validateSessions(windows, "14:50")).toBeNull();
  });

  it("treats abutting windows as valid, since in_window is start <= now < end", () => {
    expect(validateSessions([w("09:35", "11:30"), w("11:30", "13:00")], "15:15")).toBeNull();
  });

  it("rejects overlaps whichever order they were typed in", () => {
    expect(validateSessions([w("09:35", "11:30"), w("11:00", "13:00")], "15:15")).toMatch(
      /overlap/,
    );
    expect(validateSessions([w("13:00", "14:00"), w("09:35", "13:30")], "15:15")).toMatch(
      /overlap/,
    );
  });

  it("rejects a start before the warm-up floor", () => {
    expect(validateSessions([w("09:20", "11:30")], "15:15")).toMatch(EARLIEST_SESSION_START);
  });

  it("rejects a window that outlives the square-off, and accepts it once that moves", () => {
    expect(validateSessions([w("13:30", "15:25")], "15:15")).toMatch(/square-off/);
    expect(validateSessions([w("13:30", "15:25")], "15:28")).toBeNull();
  });

  it("rejects a window past the market close even with a late square-off", () => {
    expect(validateSessions([w("14:00", "15:45")], "15:50")).toMatch(/market close/);
  });

  it("rejects an empty list, a backwards window, and more than four", () => {
    expect(validateSessions([], "15:15")).toMatch(/at least one/);
    expect(validateSessions([w("11:30", "09:35")], "15:15")).toMatch(/must end after/);
    expect(validateSessions(Array(5).fill(w("09:35", "10:00")), "15:15")).toMatch(/At most 4/);
  });
});

describe("warmupReadyAt", () => {
  it("is 09:30 for volume expansion at any duration: NIFTY's OI window is 15 minutes", () => {
    expect(warmupReadyAt({ mechanism: "expansion", duration: 1 })).toBe("09:30");
    expect(warmupReadyAt({ mechanism: "expansion", duration: 15 })).toBe("09:30");
  });

  it("is nine of today's candles for momentum", () => {
    expect(warmupReadyAt({ mechanism: "momentum", duration: 1 })).toBe("09:24");
    expect(warmupReadyAt({ mechanism: "momentum", duration: 5 })).toBe("10:00");
    expect(warmupReadyAt({ mechanism: "momentum", duration: 15 })).toBe("11:30");
  });
});

describe("warmupWarning", () => {
  it("stays quiet when the first window opens after the signal first reads", () => {
    expect(warmupWarning([w("09:35", "11:30")], { mechanism: "expansion", duration: 15 })).toBeNull();
  });

  it("warns when a slower signal first reads after the first window opens", () => {
    const warning = warmupWarning([w("09:35", "11:30")], { mechanism: "momentum", duration: 15 });
    expect(warning).toMatch(/11:30/);
    expect(warning).toMatch(/15-minute candles/);
  });

  it("says nothing for a bot with no signal, like the iron fly", () => {
    expect(warmupWarning([w("11:30", "13:30")], null)).toBeNull();
  });
});

describe("suggestWindow", () => {
  it("lands a valid row rather than a red one", () => {
    const next = suggestWindow([w("09:35", "11:30")], "15:15");
    expect(next).toEqual({ start: "11:30", end: "12:30" });
    expect(validateSessions([w("09:35", "11:30"), next!], "15:15")).toBeNull();
  });

  it("fills the gap between two windows rather than reporting the day full", () => {
    // The shipped default. Appending would find five unusable minutes after 15:10 and
    // disable the button, with two free hours sitting over lunch.
    const next = suggestWindow([w("09:35", "11:30"), w("13:30", "15:10")], "15:15");
    expect(next).toEqual({ start: "11:30", end: "12:30" });
    expect(
      validateSessions([w("09:35", "11:30"), w("13:30", "15:10"), next!], "15:15"),
    ).toBeNull();
  });

  it("clips the suggestion to the square-off", () => {
    expect(suggestWindow([w("09:35", "14:30")], "15:00")).toEqual({
      start: "14:30",
      end: "15:00",
    });
  });

  it("offers the morning when the only window is later in the day", () => {
    expect(suggestWindow([w("13:30", "15:10")], "15:15")).toEqual({
      start: "09:35",
      end: "10:35",
    });
  });

  it("returns nothing when every gap is too short to be worth offering", () => {
    expect(suggestWindow([w("09:35", "15:10")], "15:15")).toBeNull();
  });
});
