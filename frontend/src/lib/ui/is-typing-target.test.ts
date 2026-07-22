import { describe, expect, it } from "vitest";
import { isTypingTarget } from "@/lib/ui/is-typing-target";

/** Minimal element stub for node test environment (no jsdom). */
function stubEl(
  tagName: string,
  opts?: { contentEditable?: boolean },
): EventTarget {
  return {
    tagName,
    isContentEditable: opts?.contentEditable ?? false,
  } as unknown as EventTarget;
}

describe("isTypingTarget", () => {
  it("returns true for input, textarea, and select", () => {
    expect(isTypingTarget(stubEl("INPUT"))).toBe(true);
    expect(isTypingTarget(stubEl("TEXTAREA"))).toBe(true);
    expect(isTypingTarget(stubEl("SELECT"))).toBe(true);
  });

  it("returns true for contenteditable elements", () => {
    expect(isTypingTarget(stubEl("DIV", { contentEditable: true }))).toBe(true);
  });

  it("returns false for buttons and plain divs", () => {
    expect(isTypingTarget(stubEl("BUTTON"))).toBe(false);
    expect(isTypingTarget(stubEl("DIV"))).toBe(false);
  });

  it("returns false for null and objects without tagName", () => {
    expect(isTypingTarget(null)).toBe(false);
    expect(isTypingTarget({} as EventTarget)).toBe(false);
  });
});
