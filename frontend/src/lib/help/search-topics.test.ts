import { describe, expect, it } from "vitest";
import { HELP_CATEGORY_LABELS } from "@/lib/help/categories";
import {
  filterHelpTopics,
  groupTopicsByCategoryWithLabels,
} from "@/lib/help/search-topics";
import { helpTopics } from "@/lib/help/topics";

describe("groupTopicsByCategoryWithLabels", () => {
  it("groups filtered topics by category order", () => {
    const filtered = filterHelpTopics("order", helpTopics);
    const groups = groupTopicsByCategoryWithLabels(filtered, HELP_CATEGORY_LABELS);
    expect(groups.length).toBeGreaterThan(0);
    for (const g of groups) {
      expect(g.label).toBe(HELP_CATEGORY_LABELS[g.category]);
      expect(g.topics.every((t) => t.category === g.category)).toBe(true);
    }
  });
});
