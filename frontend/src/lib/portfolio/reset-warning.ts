/**
 * Copy + styling for the PB/SL Reset warning, in one place so the four surfaces that show
 * it (Portfolio group row, Orders table, the rule modal, the app-level banner) cannot
 * drift apart.
 *
 * The warning has to carry five facts — stopped / why / N orders live / one may open a
 * contra position / what to do — across surfaces ranging from a ~10-character chip to a
 * full modal. One message can't say all of it without becoming a paragraph in which the
 * sentence that matters most gets skimmed. So it's tiered by *hazard*, not by state:
 *
 *   settled     → neutral. Nothing at stake; PB/SL simply stopped.
 *   orders_live → amber.   Automation the user thinks is off will still act.
 *   contra_risk → red.     A resting order will OPEN a position they never asked for.
 *
 * Tier 1 is neutral rather than amber on purpose: colouring a benign Reset amber trains
 * the user to discount tiers 2 and 3. The gradient is neutral → amber → red.
 *
 * Colour rule: text on a `-tint` background uses the matching `-on-tint` token, never the
 * base token (see globals.css §2.4 / DESIGN_LANGUAGE.md) — the base tokens fail WCAG AA
 * on their own tints in light theme.
 */
import type {
  ResetHazardTier,
  SquareOffRuleRecord,
} from "@/lib/portfolio/squareoff-rules";

export function resetHazardTier(rule: SquareOffRuleRecord): ResetHazardTier {
  return rule.hazard_tier ?? "settled";
}

/** Short chip label. Kept terse enough for a Portfolio group header. */
export function resetChipLabel(rule: SquareOffRuleRecord): string {
  const tier = resetHazardTier(rule);
  if (tier === "contra_risk") return "Reset · action needed";
  const n = rule.orphan_orders?.length ?? 0;
  if (tier === "orders_live") return n > 0 ? `Reset · ${n} live` : "Reset";
  return "Reset";
}

export function resetChipClassName(rule: SquareOffRuleRecord): string {
  switch (resetHazardTier(rule)) {
    case "contra_risk":
      return "bg-down-tint text-down-on-tint";
    case "orders_live":
      return "bg-amber-tint text-amber-on-tint";
    default:
      return "bg-panel2 text-faint";
  }
}

/** Full-width callout styling (globals.css `.app-note-*` equivalents). */
export function resetNoteClassName(rule: SquareOffRuleRecord): string {
  switch (resetHazardTier(rule)) {
    case "contra_risk":
      return "border-down/40 bg-down-tint text-down-on-tint";
    case "orders_live":
      return "border-amber-accent/40 bg-amber-tint text-amber-on-tint";
    default:
      return "border-border bg-panel2 text-muted";
  }
}

function legLabel(o: { stock_code: string; strike_price: string; right: string }): string {
  const right = o.right.trim().toLowerCase().startsWith("c") ? "CE" : "PE";
  return `${o.stock_code} ${o.strike_price} ${right}`.trim();
}

/**
 * The user-facing sentence(s). Convention inherited from the license banners
 * (`lib/deployment-license.ts`): **state — consequence. action.**
 *
 * The tier-3 wording is the whole point of the tiering. "Monitoring stopped" on its own
 * actively misleads — it reads as "nothing automated will happen to my positions" — while
 * an orphan is still working and may open a brand-new trade. That has to be said plainly.
 */
export function resetMessage(rule: SquareOffRuleRecord): string {
  const reason = rule.reset_reason?.trim() || "this group changed.";
  const orphans = rule.orphan_orders ?? [];
  const head = `Profit Booking / Stop Loss stopped — ${reason}`;

  if (orphans.length === 0) {
    return `${head} No exit orders are outstanding. Set it again to resume.`;
  }

  const contra = orphans.filter((o) => o.opens_contra_position);
  if (contra.length > 0) {
    const label = legLabel(contra[0]);
    const side = contra[0].action || "new";
    const more =
      contra.length > 1 ? ` (and ${contra.length - 1} more like it)` : "";
    return (
      `${head} Its exit order for ${label} is still live: if it fills, it will open a ` +
      `new ${side} position${more}. Cancel it, or let it fill and accept the position.`
    );
  }

  const n = orphans.length;
  return (
    `${head} ${n} exit order${n === 1 ? "" : "s"} ${n === 1 ? "is" : "are"} still live ` +
    `and may still execute. You can't set a new rule until they fill, expire, or you ` +
    `cancel them.`
  );
}

/** One-line form for the app-level banner (tier 3 only). */
export function resetBannerMessage(rule: SquareOffRuleRecord): string | null {
  if (resetHazardTier(rule) !== "contra_risk") return null;
  const contra = (rule.orphan_orders ?? []).filter((o) => o.opens_contra_position);
  if (contra.length === 0) return null;
  const label = legLabel(contra[0]);
  const side = contra[0].action || "new";
  const more = contra.length > 1 ? ` (+${contra.length - 1} more)` : "";
  return `A live exit order on ${label} no longer has a position to close — if it fills it will open a new ${side} position${more}.`;
}

export function isResetDismissable(rule: SquareOffRuleRecord): boolean {
  return !rule.rearm_blocked;
}
