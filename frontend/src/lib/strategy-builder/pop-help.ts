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

export const POP_HELP_TITLE = "Probability of profit (PoP)";

export const POP_HELP_DEFINITION =
  "Estimated chance the strategy is profitable at expiry, based on current spot, implied volatility, and days to expiry (analytic model).";

export const POP_HELP_INCOME =
  "Your minimum PoP filters out trades below that threshold. A higher minimum pushes short strikes further out of the money.";

export const POP_HELP_DIRECTIONAL =
  "PoP is shown for reference only — it does not filter or rank directional variants. Conservative, Moderate, and Aggressive picks are chosen by conviction (delta, cost, liquidity).";

export const POP_HELP_DISCLAIMER =
  "This is a model estimate, not a guarantee of outcome.";
