"use client";

import type { CondorSettings } from "@/lib/condor";

type NumField = {
  key: keyof CondorSettings;
  label: string;
  hint: string;
  step: number;
  nullable?: boolean;
};

type Group = { title: string; fields: (NumField | "expiry_kind" | "entry_check" | "sod" | "eod")[] };

const GROUPS: Group[] = [
  {
    title: "Cycle",
    fields: [
      "expiry_kind",
      { key: "entry_dte", label: "Entry DTE", hint: "First tranche goes in at or below this many days to expiry.", step: 1 },
      { key: "tranche_cutoff_dte", label: "Tranche cut-off DTE", hint: "No new tranche below this.", step: 1 },
      { key: "exit_dte", label: "Exit DTE", hint: "Exit or time-roll at or below this.", step: 1 },
      { key: "tranches", label: "Tranches", hint: "Entries spread evenly between entry and cut-off, same expiry.", step: 1 },
      "entry_check",
    ],
  },
  {
    title: "Strikes",
    fields: [
      { key: "short_delta", label: "Short Δ", hint: "|Δ| of the shorts at entry (0.20 = 20 delta).", step: 0.01 },
      { key: "wing_delta", label: "Wing Δ", hint: "|Δ| of the wings, snapped outward.", step: 0.01 },
    ],
  },
  {
    title: "Rolls",
    fields: [
      { key: "leg_rule_delta_floor", label: "Untested side below Δ", hint: "Roll when the untested short falls under this.", step: 0.01 },
      { key: "leg_rule_decay_pct", label: "…or decayed %", hint: "Roll when the untested short has lost this share of its premium.", step: 1 },
      { key: "net_delta_band_per_lot", label: "Net Δ band per lot", hint: "Roll when net delta per lot is outside ±this.", step: 0.01 },
      { key: "min_roll_credit_points", label: "Minimum roll credit (pts)", hint: "A roll adding less, after charges, is skipped.", step: 1 },
      { key: "no_roll_within_days_of_exit", label: "No rolls within N days of exit", hint: "A roll due this close to the exit DTE is reported, not done. 0 = off.", step: 1 },
    ],
  },
  {
    title: "Checks and risk",
    fields: [
      "sod",
      "eod",
      { key: "margin_ceiling_inr", label: "Margin ceiling ₹", hint: "Campaign margin; the rest of capital is the buffer.", step: 10000 },
      { key: "max_loss_inr", label: "Max loss ₹", hint: "Close everything past this (checked at the two checks). Blank = off.", step: 1000, nullable: true },
      { key: "max_loss_pct_of_ceiling", label: "…or % of ceiling", hint: "The tighter of the two binds. Blank = off.", step: 0.5, nullable: true },
    ],
  },
];

export function CondorSettingsForm({
  value,
  onChange,
  disabled = false,
}: {
  value: CondorSettings;
  onChange: (next: CondorSettings) => void;
  disabled?: boolean;
}) {
  const set = <K extends keyof CondorSettings>(key: K, v: CondorSettings[K]) => onChange({ ...value, [key]: v });

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {GROUPS.map((group) => (
        <fieldset key={group.title} className="space-y-2" disabled={disabled}>
          <legend className="text-xs font-semibold uppercase tracking-wide text-muted">{group.title}</legend>
          {group.fields.map((f) => {
            if (f === "expiry_kind") {
              return (
                <label key={f} className="flex items-center justify-between gap-3 text-sm">
                  <span>Expiries</span>
                  <select
                    className="app-input w-36"
                    value={value.expiry_kind}
                    onChange={(e) => set("expiry_kind", e.target.value as CondorSettings["expiry_kind"])}
                  >
                    <option value="monthly">Monthly only</option>
                    <option value="any">Any (weeklies)</option>
                  </select>
                </label>
              );
            }
            if (f === "entry_check") {
              return (
                <label key={f} className="flex items-center justify-between gap-3 text-sm">
                  <span>Enter tranches at</span>
                  <select
                    className="app-input w-36"
                    value={value.entry_check}
                    onChange={(e) => set("entry_check", e.target.value as CondorSettings["entry_check"])}
                  >
                    <option value="sod">Start-of-day check</option>
                    <option value="eod">End-of-day check</option>
                  </select>
                </label>
              );
            }
            if (f === "sod" || f === "eod") {
              const key = f === "sod" ? "sod_check_ist" : "eod_check_ist";
              return (
                <label key={f} className="flex items-center justify-between gap-3 text-sm" title={
                  f === "sod" ? "Prices are ignored before this; also catches overnight gaps." : "After the closing auction; options trade to 15:40."
                }>
                  <span>{f === "sod" ? "Start-of-day check (IST)" : "End-of-day check (IST)"}</span>
                  <input
                    type="time"
                    className="app-input w-36 font-mono"
                    value={value[key]}
                    onChange={(e) => set(key, e.target.value)}
                  />
                </label>
              );
            }
            const raw = value[f.key] as number | null;
            return (
              <label key={f.key} className="flex items-center justify-between gap-3 text-sm" title={f.hint}>
                <span>{f.label}</span>
                <input
                  type="number"
                  inputMode="decimal"
                  className="app-input w-36 text-right font-mono"
                  step={f.step}
                  value={raw ?? ""}
                  placeholder={f.nullable ? "off" : undefined}
                  onChange={(e) => {
                    const text = e.target.value.trim();
                    if (text === "" && f.nullable) {
                      set(f.key, null as never);
                      return;
                    }
                    const n = Number(text);
                    if (Number.isFinite(n)) set(f.key, n as never);
                  }}
                />
              </label>
            );
          })}
        </fieldset>
      ))}
    </div>
  );
}
