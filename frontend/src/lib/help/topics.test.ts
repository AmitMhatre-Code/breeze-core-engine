import { describe, expect, it } from "vitest";
import { HELP_CATEGORY_ORDER } from "@/lib/help/categories";
import { filterHelpTopics } from "@/lib/help/search-topics";
import { getTopicById, helpTopics } from "@/lib/help/topics";

describe("helpTopics", () => {
  it("has unique ids", () => {
    const ids = helpTopics.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("uses valid categories", () => {
    const allowed = new Set(HELP_CATEGORY_ORDER);
    for (const t of helpTopics) {
      expect(allowed.has(t.category)).toBe(true);
    }
  });

  it("resolves relatedTopicIds", () => {
    for (const t of helpTopics) {
      for (const rel of t.relatedTopicIds ?? []) {
        expect(getTopicById(rel), `${t.id} → ${rel}`).toBeDefined();
      }
    }
  });
});

describe("filterHelpTopics", () => {
  it("returns all topics for empty query", () => {
    expect(filterHelpTopics("", helpTopics)).toHaveLength(helpTopics.length);
  });

  it("matches keywords like AMO", () => {
    const hits = filterHelpTopics("amo", helpTopics);
    expect(hits.some((t) => t.id === "parked-orders")).toBe(true);
  });

  it("matches title fragments", () => {
    const hits = filterHelpTopics("probability", helpTopics);
    expect(hits.some((t) => t.id === "probability-of-profit")).toBe(true);
  });
});
