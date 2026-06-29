import type { HelpCategory } from "@/lib/help/categories";
import { HELP_CATEGORY_ORDER } from "@/lib/help/categories";
import type { HelpTopic } from "@/lib/help/topics";

export function filterHelpTopics(
  query: string,
  topics: HelpTopic[],
): HelpTopic[] {
  const q = query.trim().toLowerCase();
  if (!q) return topics;

  return topics.filter((t) => {
    if (t.title.toLowerCase().includes(q)) return true;
    if (t.summary.toLowerCase().includes(q)) return true;
    if (t.body.some((p) => p.toLowerCase().includes(q))) return true;
    if (t.keywords?.some((k) => k.toLowerCase().includes(q))) return true;
    return false;
  });
}

export function groupTopicsByCategory(
  topics: HelpTopic[],
): { category: HelpCategory; label: string; topics: HelpTopic[] }[] {
  const byCat = new Map<HelpCategory, HelpTopic[]>();
  for (const t of topics) {
    const list = byCat.get(t.category) ?? [];
    list.push(t);
    byCat.set(t.category, list);
  }
  return HELP_CATEGORY_ORDER.filter((c) => byCat.has(c)).map((category) => ({
    category,
    topics: byCat.get(category)!,
    label: "",
  }));
}

export function groupTopicsByCategoryWithLabels(
  topics: HelpTopic[],
  labels: Record<HelpCategory, string>,
): { category: HelpCategory; label: string; topics: HelpTopic[] }[] {
  return groupTopicsByCategory(topics).map((g) => ({
    ...g,
    label: labels[g.category],
  }));
}
