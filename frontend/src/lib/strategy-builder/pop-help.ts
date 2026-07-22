export type PopLabelVariant = "field" | "inline" | "metric" | "sort";

export const POP_LABELS: Record<PopLabelVariant, string> = {
  field: "Min. probability of profit (PoP)",
  inline: "Prob. of profit",
  metric: "Est. prob. of profit",
  sort: "Probability of profit (high → low)",
};

export const POP_SORT_LABEL = POP_LABELS.sort;

const POP_API_LABELS = new Set([
  "PoP",
  "Est. PoP",
  "Min PoP",
  "Passed PoP",
  "Minimum PoP",
  "Minimum PoP (%)",
]);

export function isPopMetricLabel(label: string): boolean {
  return POP_API_LABELS.has(label.trim());
}

export function displayPopLabel(
  apiLabel: string,
  variant: PopLabelVariant = "inline",
): string {
  const trimmed = apiLabel.trim();
  if (trimmed === "Passed PoP") {
    return "Passed probability of profit";
  }
  if (isPopMetricLabel(trimmed)) {
    return POP_LABELS[variant];
  }
  return apiLabel;
}

export {
  POP_HELP_DEFINITION,
  POP_HELP_DIRECTIONAL,
  POP_HELP_DISCLAIMER,
  POP_HELP_INCOME,
  POP_HELP_TITLE,
} from "@/lib/help/topic-content";
