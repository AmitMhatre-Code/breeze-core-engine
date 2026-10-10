import { afterEach, describe, expect, it } from "vitest";
import { formatApiDateTime, parseTypedDate } from "./format-iso-date";

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

describe("parseTypedDate", () => {
  it("reads the app's own dd-MMM-yyyy in any case and separator", () => {
    expect(parseTypedDate("01-Jan-2020")).toBe("2020-01-01");
    expect(parseTypedDate("1 jan 2020")).toBe("2020-01-01");
    expect(parseTypedDate("15/MAR/2021")).toBe("2021-03-15");
    expect(parseTypedDate("  7 September 2024 ")).toBe("2024-09-07");
    expect(parseTypedDate("7 Sept 2024")).toBe("2024-09-07");
  });

  it("reads ISO and day-first numeric dates", () => {
    expect(parseTypedDate("2020-01-31")).toBe("2020-01-31");
    expect(parseTypedDate("2020/1/5")).toBe("2020-01-05");
    // Day first, as written in India: 03/04 is 3 April, never 4 March.
    expect(parseTypedDate("03/04/2022")).toBe("2022-04-03");
    expect(parseTypedDate("3.4.2022")).toBe("2022-04-03");
  });

  it("refuses dates that do not exist", () => {
    expect(parseTypedDate("31-Feb-2021")).toBeNull();
    expect(parseTypedDate("29-Feb-2023")).toBeNull();
    expect(parseTypedDate("29-Feb-2024")).toBe("2024-02-29");
    expect(parseTypedDate("13/13/2020")).toBeNull();
    expect(parseTypedDate("0-Jan-2020")).toBeNull();
  });

  it("refuses half-typed and garbled text", () => {
    expect(parseTypedDate("")).toBeNull();
    expect(parseTypedDate("01-Jan-202")).toBeNull();
    expect(parseTypedDate("01-Jan")).toBeNull();
    expect(parseTypedDate("01-Foo-2020")).toBeNull();
    expect(parseTypedDate("Jan 2020")).toBeNull();
  });
});
