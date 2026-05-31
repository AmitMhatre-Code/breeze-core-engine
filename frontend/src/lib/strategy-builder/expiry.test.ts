import { describe, expect, it } from "vitest";
import {
  formatExpiryChipShort,
  isValidExpiryDisplay,
  sortExpiryDatesAsc,
} from "@/lib/strategy-builder/expiry";

describe("sortExpiryDatesAsc", () => {
  it("orders DD-Mon-YYYY from earliest to latest", () => {
    const input = ["27-Mar-2025", "30-Jan-2025", "27-Feb-2025"];
    expect(sortExpiryDatesAsc(input)).toEqual([
      "30-Jan-2025",
      "27-Feb-2025",
      "27-Mar-2025",
    ]);
  });
});

describe("isValidExpiryDisplay", () => {
  it("accepts valid DD-Mon-YYYY and rejects invalid strings", () => {
    expect(isValidExpiryDisplay("21-Mar-2026")).toBe(true);
    expect(isValidExpiryDisplay(" 30-Jan-2025 ")).toBe(true);
    expect(isValidExpiryDisplay("")).toBe(false);
    expect(isValidExpiryDisplay("2026-03-21")).toBe(false);
    expect(isValidExpiryDisplay("31-Foo-2026")).toBe(false);
  });
});

describe("formatExpiryChipShort", () => {
  it("formats DD-Mon-YYYY to day + month", () => {
    expect(formatExpiryChipShort("21-Mar-2026")).toBe("21 Mar");
    expect(formatExpiryChipShort("09-Jan-2025")).toBe("9 Jan");
  });
});
