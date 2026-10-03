import fs from "fs";
import path from "path";
import { describe, expect, it } from "vitest";
import { buildGreeksModel, optionDelta } from "@/lib/strategy-builder/greeks";
import type { ChainSuccess } from "@/lib/strategy-builder/types";

/**
 * The condor engine (backend `services/condor/pricing.py`) decides rolls on the same delta
 * this model shows in the Portfolio leg column. Both sides are checked against one fixture,
 * `backend/tests/fixtures/condor_delta_parity.json`; `test_condor_pricing.py` is the other
 * half. A change to either model must change both, or one of the two tests fails.
 */
type Case = {
  name: string;
  spot: number;
  nowMs: number;
  rows: ChainSuccess["chain_rows"];
  expected: {
    q: number;
    forwardSource: "parity" | "default";
    deltas: [number, "Call" | "Put", number | null, string | null][];
  };
};

const file = path.resolve(process.cwd(), "../backend/tests/fixtures/condor_delta_parity.json");
const fixture = JSON.parse(fs.readFileSync(file, "utf8")) as { expiry_display: string; cases: Case[] };

describe("delta parity with the condor engine", () => {
  for (const c of fixture.cases) {
    it(c.name, () => {
      const strikes = c.rows.map((r) => r.strike_price);
      const atm = strikes.reduce((a, b) => (Math.abs(b - c.spot) < Math.abs(a - c.spot) ? b : a));
      const chain = {
        spot_price: c.spot,
        atm_strike: atm,
        expiry_display: fixture.expiry_display,
        chain_rows: c.rows,
      } as unknown as ChainSuccess;
      const model = buildGreeksModel(chain, fixture.expiry_display, c.nowMs)!;
      expect(model.forwardSource).toBe(c.expected.forwardSource);
      expect(model.q).toBeCloseTo(c.expected.q, 12);
      for (const [strike, right, delta, source] of c.expected.deltas) {
        const d = optionDelta(model, right, strike);
        expect(d?.perUnit ?? null).toBeCloseTo(delta as number, 12);
        expect(d?.sigmaSource ?? null).toBe(source);
      }
    });
  }
});
