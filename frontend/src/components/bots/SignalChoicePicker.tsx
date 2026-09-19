"use client";

import type { ReactNode } from "react";

import { DURATIONS, MECHANISM_LABEL, type SignalDuration, type SignalMechanism } from "@/lib/signals";
import { useSignalAvailability, type SignalChoice } from "@/lib/use-bots";

const MECHANISMS: SignalMechanism[] = ["expansion", "momentum"];

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">{label}</span>
      {children}
    </label>
  );
}

/**
 * Which signal a bot trades on (docs/signals-streamline-plan.md section 7): a mechanism and a
 * duration from the Signals page's grid, plus — for a bot that has a side — follow or fade.
 * Says plainly when the choice is not yet available to bots (the 30-day backtest gate), since
 * the server refuses to arm a bot on it.
 */
export function SignalChoicePicker({
  value,
  onChange,
  disabled,
  withDirection = true,
  directionHint,
}: {
  value: SignalChoice;
  onChange: (next: SignalChoice) => void;
  disabled: boolean;
  withDirection?: boolean;
  directionHint?: string;
}) {
  const availability = useSignalAvailability();
  const a = availability.data?.[value.mechanism];
  return (
    <div className="space-y-2">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Signal">
          <select
            className="app-input mt-1 w-full"
            aria-label="Signal"
            value={value.mechanism}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, mechanism: e.target.value as SignalMechanism })}
          >
            {MECHANISMS.map((m) => (
              <option key={m} value={m}>
                {MECHANISM_LABEL[m]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Duration">
          <select
            className="app-input mt-1 w-full"
            aria-label="Duration"
            value={String(value.duration)}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, duration: Number(e.target.value) as SignalDuration })}
          >
            {DURATIONS.map((d) => (
              <option key={d} value={d}>
                {d} minute{d === 1 ? "" : "s"}
              </option>
            ))}
          </select>
        </Field>
        {withDirection ? (
          <div className="sm:col-span-2">
          <Field label="Direction">
            <select
              className="app-input mt-1 w-full"
              aria-label="Direction"
              value={value.direction}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, direction: e.target.value as SignalChoice["direction"] })}
            >
              <option value="follow">Trade with the signal</option>
              <option value="fade">Trade against it (fade)</option>
            </select>
          </Field>
          </div>
        ) : null}
      </div>
      {withDirection && directionHint ? <p className="text-hint text-faint">{directionHint}</p> : null}
      <p className={`text-hint ${a && !a.available ? "text-down" : "text-faint"}`}>
        {a === undefined
          ? "Checking whether this signal is available to bots…"
          : a.available
            ? `Available to bots: backtested ${a.from} to ${a.to}.`
            : `${a.reason ?? "Not yet available to bots."} The bot cannot be switched on with it until then.`}{" "}
        <a className="app-link" href="/signals">
          Signals
        </a>
      </p>
    </div>
  );
}
