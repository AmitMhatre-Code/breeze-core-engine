import { describe, expect, it } from "vitest";
import {
  isResetDismissable,
  resetBannerMessage,
  resetChipClassName,
  resetChipLabel,
  resetHazardTier,
  resetMessage,
} from "@/lib/portfolio/reset-warning";
import type {
  SquareOffRuleOrphanOrder,
  SquareOffRuleRecord,
} from "@/lib/portfolio/squareoff-rules";

function orphan(overrides: Partial<SquareOffRuleOrphanOrder> = {}): SquareOffRuleOrphanOrder {
  return {
    order_id: "202607173800017846",
    stock_code: "NIFTY",
    strike_price: "26000",
    right: "Call",
    action: "Buy",
    quantity: "130",
    price: "2.80",
    opens_contra_position: false,
    ...overrides,
  };
}

function rule(overrides: Partial<SquareOffRuleRecord> = {}): SquareOffRuleRecord {
  return {
    id: "r1",
    stock_code: "NIFTY",
    expiry_display: "21-Jul-2026",
    exchange_code: "NFO",
    profit_target_pnl: 100000,
    loss_limit_pnl: 20000,
    target_premium_pct: 10,
    stop_loss_premium_pct: 5,
    status: "reset",
    reset_reason: "you closed NIFTY 26000 CE manually.",
    ...overrides,
  };
}

describe("hazard tiering", () => {
  it("treats a reset with no orphans as settled", () => {
    expect(resetHazardTier(rule({ hazard_tier: "settled" }))).toBe("settled");
  });

  it("defaults to settled when the backend couldn't compute a tier", () => {
    // A broker hiccup leaves hazard_tier absent. Defaulting to the loudest tier would cry
    // wolf on every transient failure; the orphan list is empty anyway.
    expect(resetHazardTier(rule())).toBe("settled");
  });

  it("uses a neutral chip for settled, not amber", () => {
    // Tier 1 has nothing at stake. Colouring a benign reset amber trains the user to
    // discount tiers 2 and 3, which is what actually matters.
    expect(resetChipClassName(rule({ hazard_tier: "settled" }))).toContain("text-faint");
  });

  it("uses amber for orders_live and red for contra_risk", () => {
    expect(resetChipClassName(rule({ hazard_tier: "orders_live" }))).toContain(
      "text-amber-on-tint",
    );
    expect(resetChipClassName(rule({ hazard_tier: "contra_risk" }))).toContain(
      "text-down-on-tint",
    );
  });

  it("never puts base-token text on a tint background", () => {
    // globals.css §2.4 -- the base tokens fail WCAG AA on their own tints in light theme.
    for (const tier of ["settled", "orders_live", "contra_risk"] as const) {
      const cls = resetChipClassName(rule({ hazard_tier: tier }));
      if (cls.includes("-tint")) expect(cls).toMatch(/text-\w+(-\w+)*-on-tint/);
    }
  });
});

describe("chip labels", () => {
  it("counts live orders so the chip carries the stake at a glance", () => {
    expect(
      resetChipLabel(
        rule({ hazard_tier: "orders_live", orphan_orders: [orphan(), orphan()] }),
      ),
    ).toBe("Reset · 2 live");
  });

  it("says action needed for contra risk rather than a count", () => {
    expect(
      resetChipLabel(
        rule({
          hazard_tier: "contra_risk",
          orphan_orders: [orphan({ opens_contra_position: true })],
        }),
      ),
    ).toBe("Reset · action needed");
  });

  it("stays terse when nothing is outstanding", () => {
    expect(resetChipLabel(rule({ hazard_tier: "settled" }))).toBe("Reset");
  });
});

describe("reset message", () => {
  it("names the cause and says nothing is outstanding when settled", () => {
    const msg = resetMessage(rule({ hazard_tier: "settled" }));
    expect(msg).toContain("you closed NIFTY 26000 CE manually.");
    expect(msg).toContain("No exit orders are outstanding");
  });

  it("states plainly that live orders may still execute", () => {
    // "Monitoring stopped" alone reads as "nothing automated will happen to my positions",
    // which is false while an orphan is resting. That has to be said out loud.
    const msg = resetMessage(
      rule({ hazard_tier: "orders_live", orphan_orders: [orphan(), orphan()] }),
    );
    expect(msg).toContain("2 exit orders are still live and may still execute");
    expect(msg).toContain("can't set a new rule");
  });

  it("warns that a contra orphan will OPEN a new position", () => {
    // The sharpest case: the leg is already closed, so filling doesn't close anything.
    const msg = resetMessage(
      rule({
        hazard_tier: "contra_risk",
        orphan_orders: [orphan({ opens_contra_position: true })],
      }),
    );
    expect(msg).toContain("NIFTY 26000 CE");
    expect(msg).toContain("open a new Buy position");
  });

  it("uses a different verb for contra risk than for orders_live", () => {
    const live = resetMessage(
      rule({ hazard_tier: "orders_live", orphan_orders: [orphan()] }),
    );
    const contra = resetMessage(
      rule({
        hazard_tier: "contra_risk",
        orphan_orders: [orphan({ opens_contra_position: true })],
      }),
    );
    expect(live).not.toContain("open a new");
    expect(contra).toContain("open a new");
  });

  it("renders PE legs correctly", () => {
    const msg = resetMessage(
      rule({
        hazard_tier: "contra_risk",
        orphan_orders: [
          orphan({ right: "Put", strike_price: "25800", opens_contra_position: true }),
        ],
      }),
    );
    expect(msg).toContain("NIFTY 25800 PE");
  });
});

describe("app-level banner", () => {
  it("only fires for contra risk", () => {
    // Tier 2 is common enough that a persistent banner would train the user to ignore it,
    // which would blunt tier 3 -- the one that actually needs to interrupt them.
    expect(
      resetBannerMessage(rule({ hazard_tier: "orders_live", orphan_orders: [orphan()] })),
    ).toBeNull();
    expect(resetBannerMessage(rule({ hazard_tier: "settled" }))).toBeNull();
  });

  it("names the leg and the consequence", () => {
    const msg = resetBannerMessage(
      rule({
        hazard_tier: "contra_risk",
        orphan_orders: [orphan({ opens_contra_position: true })],
      }),
    );
    expect(msg).toContain("NIFTY 26000 CE");
    expect(msg).toContain("open a new Buy position");
  });
});

describe("dismissal", () => {
  it("blocks dismissal while an orphan is live", () => {
    // Dismissing would erase the hazard from the UI while the orders keep working -- the
    // UI must not be able to lie about live risk.
    expect(isResetDismissable(rule({ rearm_blocked: true }))).toBe(false);
  });

  it("allows dismissal once everything is terminal", () => {
    expect(isResetDismissable(rule({ rearm_blocked: false }))).toBe(true);
  });
});
