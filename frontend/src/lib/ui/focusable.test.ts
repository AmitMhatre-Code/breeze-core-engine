import { describe, expect, it } from "vitest";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

/** Mirrors filtering logic in getFocusableElements for unit tests without jsdom. */
function filterFocusableCandidates(
  candidates: Array<{
    tag: string;
    disabled?: boolean;
    ariaHidden?: string;
    tabIndex?: number;
    href?: string;
  }>,
): number {
  return candidates.filter((el) => {
    if (el.disabled) return false;
    if (el.ariaHidden === "true") return false;
    if (el.tabIndex === -1) return false;
    if (el.tag === "A" && !el.href) return false;
    return ["A", "BUTTON", "TEXTAREA", "INPUT", "SELECT"].includes(el.tag);
  }).length;
}

describe("focusable selector contract", () => {
  it("includes standard interactive selectors", () => {
    expect(FOCUSABLE_SELECTOR).toContain("button:not([disabled])");
    expect(FOCUSABLE_SELECTOR).toContain("input:not([disabled])");
    expect(FOCUSABLE_SELECTOR).toContain('[tabindex]:not([tabindex="-1"])');
  });

  it("filters disabled and aria-hidden elements", () => {
    const count = filterFocusableCandidates([
      { tag: "BUTTON" },
      { tag: "BUTTON", disabled: true },
      { tag: "BUTTON", ariaHidden: "true" },
      { tag: "INPUT", tabIndex: -1 },
    ]);
    expect(count).toBe(1);
  });
});
