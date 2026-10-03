import { describe, expect, it } from "vitest";
import {
  actionTone,
  backtestCashPnl,
  campaignForGroup,
  differenceText,
  executionDone,
  formatDelta,
  formatInr,
  orderLine,
  previewMatches,
  ticketKey,
  type CondorCampaign,
  type TicketPreview,
  type TicketRow,
} from "@/lib/condor";

const campaign = (expiry: string, status: "active" | "closed" = "active"): CondorCampaign =>
  ({
    id: expiry,
    underlying: "NIFTY",
    status,
    cycle: status === "active" ? { expiry } : null,
  }) as unknown as CondorCampaign;

describe("campaignForGroup", () => {
  it("matches the group's underlying and expiry, case-insensitively", () => {
    const list = [campaign("29-Sep-2026"), campaign("27-Oct-2026")];
    expect(campaignForGroup(list, "nifty", "27-oct-2026")?.id).toBe("27-Oct-2026");
  });
  it("never attaches a paper campaign to a real group", () => {
    const paper = { ...campaign("29-Sep-2026"), mode: "paper" } as CondorCampaign;
    expect(campaignForGroup([paper], "NIFTY", "29-Sep-2026")).toBeNull();
  });
  it("ignores closed campaigns and other underlyings", () => {
    expect(campaignForGroup([campaign("29-Sep-2026", "closed")], "NIFTY", "29-Sep-2026")).toBeNull();
    expect(campaignForGroup([campaign("29-Sep-2026")], "CNXBAN", "29-Sep-2026")).toBeNull();
  });
});

describe("labels", () => {
  it("names each order the way a trader reads it", () => {
    expect(orderLine({ strike: 23650, right: "Put", action: "Buy", quantity: 65, opening: true, price: 73.13 })).toBe(
      "Buy 65 × 23,650 PE @ 73.13",
    );
    expect(orderLine({ strike: 23350, right: "Put", action: "Buy", quantity: 65, opening: false, price: null })).toBe(
      "Buy back 65 × 23,350 PE",
    );
    expect(orderLine({ strike: 21550, right: "Put", action: "Sell", quantity: 65, opening: false, price: 8 })).toBe(
      "Sell to close 65 × 21,550 PE @ 8.00",
    );
  });
  it("describes a difference from both sides", () => {
    expect(
      differenceText({
        strike: 25850, right: "Call", ledger_units: 65, broker_units: 0, units: -65,
        left_out: false, can_leave_out: false, broker_avg_price: null,
      }),
    ).toBe("25,850 CE: the broker holds nothing, the ledger long 65.");
  });
  it("tones actions", () => {
    expect(actionTone("roll_untested")).toBe("act");
    expect(actionTone("close_all")).toBe("warn");
    expect(actionTone("no_action")).toBe("quiet");
  });
  it("formats money and delta with a real minus sign", () => {
    expect(formatInr(-12345.6)).toBe("−₹12,346");
    expect(formatInr(null)).toBe("—");
    expect(formatDelta(-0.156)).toBe("−0.16");
    expect(formatDelta(0.1)).toBe("+0.10");
  });
  it("adds the open campaign's cash to the closed P&L", () => {
    expect(backtestCashPnl({ closed_pnl: 1000, open_campaign_cash: -250 })).toBe(750);
    expect(backtestCashPnl(null)).toBeNull();
  });
});


describe("ticket preview freshness", () => {
  const rows: TicketRow[] = [{ action: "Sell", strike: 24300, right: "Put", quantity: 65, expiry: null }];
  it("matches only the rows it was computed for", () => {
    const p = {} as TicketPreview;
    expect(previewMatches(p, rows, ticketKey(rows))).toBe(true);
    expect(previewMatches(p, [{ ...rows[0], quantity: 130 }], ticketKey(rows))).toBe(false);
    expect(previewMatches(null, rows, ticketKey(rows))).toBe(false);
  });
  it("knows when an execution has finished", () => {
    expect(executionDone({ status: "running" } as never)).toBe(false);
    expect(executionDone({ status: "stopped" } as never)).toBe(true);
  });
});
