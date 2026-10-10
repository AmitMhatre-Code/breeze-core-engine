"use client";

import { Checkbox } from "@/components/ui/Checkbox";
import { NumberInput } from "@/components/ui/NumberInput";
import { TabIntro, WorkedExample, num } from "@/components/bots/SettingsHelp";
import type { PremiumGateConfig } from "@/lib/use-bots";

/** The premium gate's tab, shared by the Expiry-Day Index Writer, the Iron Fly (sellers) and
 *  the Long Scalper (the buyer). docs/premium-gate-plan.md. */
export function PremiumGateSettings({
  gate,
  side,
  onChange,
  disabled,
}: {
  gate: PremiumGateConfig;
  side: "sell" | "buy";
  onChange: (next: PremiumGateConfig) => void;
  disabled: boolean;
}) {
  const sell = side === "sell";
  // A worked example on round numbers: the options price ±1.00% to expiry.
  const implied = 1.0;
  const fair = implied / gate.threshold;
  return (
    <div className="space-y-4">
      <TabIntro>
        <p>
          Option prices carry a forecast of how far the index will move before they expire. The bot compares it with
          the index&rsquo;s own recent movement &mdash; the last month of one-minute prices, weighting the last week
          more, adjusted for how much of the day is left &mdash; and divides one by the other.
        </p>
        <p>
          {sell ? (
            <>
              Above 1, the options are pricing more movement than the index has been making: premium is{" "}
              <b>rich</b>, and that gap is what a seller is paid for. Below 1, the seller is paid less than the risk
              the index has recently shown.
            </>
          ) : (
            <>
              Below 1, the options are pricing less movement than the index has been making: premium is{" "}
              <b>cheap</b>, so a buyer pays less for each point the index is likely to move. Above 1, the buyer
              overpays.
            </>
          )}
        </p>
      </TabIntro>
      <label className="flex cursor-pointer items-start gap-2">
        <Checkbox
          checked={gate.enabled}
          onChange={(enabled) => onChange({ ...gate, enabled })}
          disabled={disabled}
          aria-label={sell ? "Only sell when premium is rich" : "Only buy when premium is cheap"}
        />
        <span className="text-body">
          <span className="font-semibold">{sell ? "Only sell when premium is rich" : "Only buy when premium is cheap"}</span>
          <span className="block text-hint text-faint">
            If the comparison cannot be made &mdash; fewer than 15 sessions of index history, or no live price on the
            at-the-money call and put &mdash; the bot does not trade.
          </span>
        </span>
      </label>
      <label className="block max-w-xs">
        <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">
          {sell ? "Sell at or above" : "Buy at or below"} (× the forecast move)
        </span>
        <NumberInput
          className="app-input mt-1"
          validityKey="premium_gate_threshold"
          step={0.05}
          min={0.5}
          max={3}
          disabled={disabled || !gate.enabled}
          value={gate.threshold}
          onChange={(threshold) => onChange({ ...gate, threshold })}
        />
        <span className="mt-1 block text-hint text-faint">
          1.00 means &ldquo;{sell ? "at least" : "at most"} what the index has been doing&rdquo;. The bot&rsquo;s
          backtest compares off, 0.90, 1.00 and 1.20 side by side.
        </span>
      </label>
      <WorkedExample title={<>Example: the options price a ±{num(implied)}% move to expiry</>}>
        <li>
          {sell ? (
            <>
              It sells if the index&rsquo;s forecast move is <b>±{num(fair)}%</b> or less (
              {num(implied)} ÷ {num(fair)} = {num(gate.threshold)}×).
            </>
          ) : (
            <>
              It buys if the index&rsquo;s forecast move is <b>±{num(fair)}%</b> or more (
              {num(implied)} ÷ {num(fair)} = {num(gate.threshold)}×).
            </>
          )}
        </li>
        <li>
          The reading is in each run&rsquo;s reason in Activity, for example &ldquo;premium 1.24x the forecast move
          (implied ±0.82%, forecast ±0.66% to expiry)&rdquo;.
        </li>
        {!gate.enabled ? <li>Switched off: the bot trades without looking.</li> : null}
      </WorkedExample>
    </div>
  );
}
