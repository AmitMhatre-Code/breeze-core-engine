"use client";

import type { ReactNode } from "react";

import {
  DURATIONS,
  LEGACY_VERSION,
  SIGNAL_VERSIONS,
  versionName,
  type SignalDuration,
  type SignalMechanism,
} from "@/lib/signals";
import { signalKey, useSignalAvailability, type SignalChoice } from "@/lib/use-bots";

const MECHANISMS: SignalMechanism[] = ["expansion", "momentum"];

/** Every signal a bot may pick: one option per running version, newest first (#72). */
const OPTIONS: { mechanism: SignalMechanism; version: number }[] = MECHANISMS.flatMap((mechanism) =>
  SIGNAL_VERSIONS[mechanism].map((version) => ({ mechanism, version })),
);

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">{label}</span>
      {children}
    </label>
  );
}

/**
 * Which signal a bot trades on (docs/signals-streamline-plan.md section 7): a mechanism, its
 * version and a duration from the Signals page's grid, plus — for a bot that has a side — follow
 * or fade. Says plainly when the choice is not yet available to bots (the 30-day backtest gate,
 * per version), since the server refuses to arm a bot on it, and when it does not run on SENSEX
 * for a bot that trades SENSEX.
 */
export function SignalChoicePicker({
  value,
  onChange,
  disabled,
  withDirection = true,
  directionHint,
  tradesSensex = false,
}: {
  value: SignalChoice;
  onChange: (next: SignalChoice) => void;
  disabled: boolean;
  withDirection?: boolean;
  directionHint?: string;
  /** The bot reads its signal on SENSEX too (CAS Bingo with SENSEX on). */
  tradesSensex?: boolean;
}) {
  const availability = useSignalAvailability();
  const version = value.version ?? LEGACY_VERSION[value.mechanism];
  const a = availability.data?.[signalKey(value.mechanism, version)];
  const notOnSensex = tradesSensex && value.mechanism === "momentum" && version === 3;
  return (
    <div className="space-y-2">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Signal">
          <select
            className="app-input mt-1 w-full"
            aria-label="Signal"
            value={signalKey(value.mechanism, version)}
            disabled={disabled}
            onChange={(e) => {
              const picked = OPTIONS.find((o) => signalKey(o.mechanism, o.version) === e.target.value);
              if (picked) onChange({ ...value, mechanism: picked.mechanism, version: picked.version });
            }}
          >
            {OPTIONS.map((o) => (
              <option key={signalKey(o.mechanism, o.version)} value={signalKey(o.mechanism, o.version)}>
                {versionName(o.mechanism, o.version)}
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
      {/* What the choices mean, in one place for every bot that reads a signal. The full rules
          live on the Signals page and in the guide. */}
      <p className="text-hint text-faint">
        <b>Volume expansion</b> calls a move that is both unusually large and unusually heavily traded.{" "}
        <b>Momentum</b>{" "}calls when the index futures close above (or below) their short-term trend and the day&rsquo;s average
        price, on heavy volume. <b>v3</b> is the newest: it judges volume against the same time of day (or, at 1
        minute, against the last three candles) and needs a clear move past the trend. <b>v2</b> and <b>v1</b> are
        kept for comparison. <b>Duration</b> is the length of the bars it reads: shorter fires more often and is
        noisier.
        {withDirection
          ? " Trading with the signal bets the move continues; fading it bets the move reverses."
          : ""}
      </p>
      {withDirection && directionHint ? <p className="text-hint text-faint">{directionHint}</p> : null}
      {notOnSensex ? (
        <p className="text-hint text-down">
          Momentum v3 does not run on SENSEX, whose futures go untraded for about half of all minutes. Switch SENSEX
          off for this bot, or pick another signal; the bot cannot be switched on as it is.
        </p>
      ) : null}
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
