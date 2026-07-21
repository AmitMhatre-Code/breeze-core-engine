import { afterEach, describe, expect, it } from "vitest";
import { formatApiDateTime } from "./format-iso-date";

const ORIGINAL_TZ = process.env.TZ;

afterEach(() => {
  process.env.TZ = ORIGINAL_TZ;
});

describe("formatApiDateTime", () => {
  it("renders a zoneless backend stamp as the IST wall clock it already is", () => {
    // The backend writes this with core.timezone.ist_timestamp() -- 13:07 IST, not UTC.
    expect(formatApiDateTime("2026-07-21 13:07:54")).toBe("21 Jul 2026, 1:07 pm IST");
  });

  it("does not change with the viewer's timezone", () => {
    // The bug this guards: `new Date("2026-07-21 13:07:54")` has no zone to go on, so
    // JS read it as browser-local and the IST conversion then shifted it by the
    // viewer's offset. A trading log that reads differently in London than in Mumbai
    // is worse than one that is simply wrong.
    const rendered: string[] = [];
    for (const tz of ["Asia/Kolkata", "UTC", "America/New_York", "Australia/Sydney"]) {
      process.env.TZ = tz;
      rendered.push(formatApiDateTime("2026-07-21 13:07:54"));
    }
    expect(new Set(rendered).size).toBe(1);
  });

  it("handles midnight and noon without flipping am/pm", () => {
    expect(formatApiDateTime("2026-07-21 00:14:00")).toBe("21 Jul 2026, 12:14 am IST");
    expect(formatApiDateTime("2026-07-21 12:00:00")).toBe("21 Jul 2026, 12:00 pm IST");
    expect(formatApiDateTime("2026-07-21 23:59:59")).toBe("21 Jul 2026, 11:59 pm IST");
  });

  it("accepts the T-separated variant of the same zoneless shape", () => {
    expect(formatApiDateTime("2026-07-21T13:07:54")).toBe("21 Jul 2026, 1:07 pm IST");
  });

  it("converts a stamp that carries its own offset into IST", () => {
    // reference_data_ingest_history.ingested_at is a real instant, not a wall clock.
    expect(formatApiDateTime("2026-07-21T07:37:54+00:00")).toContain("1:07 pm IST");
  });

  it("falls back to the raw value rather than rendering nonsense", () => {
    expect(formatApiDateTime("not a date")).toBe("not a date");
    expect(formatApiDateTime(null)).toBe("—");
    expect(formatApiDateTime(undefined)).toBe("—");
    expect(formatApiDateTime("")).toBe("—");
  });
});
