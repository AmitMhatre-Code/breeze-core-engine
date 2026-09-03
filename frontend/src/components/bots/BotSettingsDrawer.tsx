"use client";

import { useCallback, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Modal } from "@/components/ui/Modal";
import { Checkbox } from "@/components/ui/Checkbox";
import { NumberInput, FieldValidityContext } from "@/components/ui/NumberInput";
import { Select, type SelectOption } from "@/components/ui/Select";
import {
  fetchTelegramStatus,
  TELEGRAM_STATUS_QUERY_KEY,
} from "@/lib/telegram/telegram-alerts";
import {
  BOT_HOLDINGS_WRITER,
  BOT_META,
  INDEX_LABEL,
  STRATEGY_LABEL,
  useBotHoldings,
  useSaveScripPrefs,
  useScripPrefs,
  useUpdateBot,
  type Bot,
  type ExpiryIndexWriterConfig,
  type HoldingRow,
  type HoldingsWriterConfig,
  type IndexStrategy,
  type IndexWriterLeg,
  type ApprovalMode,
  type ScripPref,
} from "@/lib/use-bots";

type Tab = { id: string; label: string };

const HOLDINGS_TABS: Tab[] = [
  { id: "scrips", label: "Scrips" },
  { id: "schedule", label: "Schedule" },
  { id: "limits", label: "Limits" },
];

const INDEX_TABS: Tab[] = [
  { id: "indices", label: "Indices" },
  { id: "schedule", label: "Schedule" },
  { id: "exits", label: "Exits" },
];

const ALL_STRATEGIES: IndexStrategy[] = ["naked_ce", "naked_pe", "short_strangle"];

const EXPIRY_OPTIONS = [
  { value: "current", label: "Current month" },
  { value: "next", label: "Next month" },
] as const satisfies ReadonlyArray<SelectOption<"current" | "next">>;

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">
        {label}
      </span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-hint text-faint">{hint}</span>}
    </label>
  );
}

function SelectField<T extends string>({
  label,
  hint,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string;
  hint?: string;
  value: T;
  options: ReadonlyArray<SelectOption<T>>;
  onChange: (value: T) => void;
  disabled: boolean;
}) {
  const labelId = useId();
  return (
    <div className="block">
      <span
        id={labelId}
        className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint"
      >
        {label}
      </span>
      <div className="mt-1">
        <Select
          value={value}
          options={options}
          onChange={onChange}
          disabled={disabled}
          labelledBy={labelId}
        />
      </div>
      {hint && <span className="mt-1 block text-hint text-faint">{hint}</span>}
    </div>
  );
}

const APPROVAL_OPTIONS = [
  { value: "auto", label: "Trade on its own" },
  { value: "telegram", label: "Ask me on Telegram" },
] as const;

/** The mode selector, plus the one warning that matters.
 *
 *  A bot set to ask on Telegram with no chat linked cannot ask, so it cannot trade — and
 *  it fails as a quiet no-trade day rather than an error. That is the state worth shouting
 *  about here, because by the time it shows up in the run log the expiry has passed. */
function ApprovalModeField({
  value,
  connected,
  disabled,
  onChange,
}: {
  value: ApprovalMode;
  connected: boolean;
  disabled: boolean;
  onChange: (mode: ApprovalMode) => void;
}) {
  return (
    <div className="space-y-2">
      <SelectField
        label="When it fires"
        hint={
          value === "telegram"
            ? "It sizes the trade, sends it to Telegram, and places nothing until you tap Approve."
            : "It sizes the trade and places it unattended."
        }
        value={value}
        options={APPROVAL_OPTIONS}
        disabled={disabled}
        onChange={onChange}
      />
      {value === "telegram" && !connected && (
        <p className="text-hint text-rose-600 dark:text-rose-400">
          No Telegram chat is linked, so this bot cannot ask — and will not trade. Connect
          one in Settings › Telegram Alerts.
        </p>
      )}
    </div>
  );
}

function NumberCell({
  value,
  onChange,
  disabled,
  min = 0,
  max = 999,
  step = 1,
  placeholder,
  label,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
  disabled: boolean;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  label: string;
}) {
  return (
    <input
      type="number"
      inputMode="decimal"
      aria-label={label}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      placeholder={placeholder}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      className="w-12 rounded-t-[3px] border-0 border-b border-muted bg-panel2 px-1.5 py-1 text-right font-mono text-table font-semibold text-text transition placeholder:text-faint hover:border-accent focus:border-accent-strong focus:outline-none disabled:opacity-50 [-moz-appearance:textfield] [appearance:textfield] [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none"
    />
  );
}

// --- Bot 1 -----------------------------------------------------------------------------

/** Merge the live holdings with the stored prefs.
 *
 *  Prefs for a scrip the user no longer holds are kept, not dropped: selling and rebuying a
 *  name should not silently wipe its configuration. They simply have no row to render until
 *  the holding comes back.
 */
function prefFor(prefs: ScripPref[], code: string): ScripPref {
  return (
    prefs.find((p) => p.stock_code === code) ?? {
      stock_code: code,
      ce_enabled: true,
      pe_enabled: false,
      ce_lots: null,
      pe_lots: null,
      safety_pct_ce: null,
      safety_pct_pe: null,
      priority: 1,
    }
  );
}

/** Total lots, split into what can actually back a call and what cannot.
 *
 *  Three categories, exhaustive by construction (available + blocked + pledged = total):
 *  **free** is unencumbered; **pledged** is collateral, which still counts as coverage but
 *  has to be unpledged before expiry to deliver; **blocked** is earmarked for something
 *  else and is not coverage at all. Only non-zero categories render, so an ordinary
 *  holding stays a single quiet line.
 */
function LotBreakdown({ holding }: { holding: HoldingRow }) {
  const shares = (n: number) => `${n.toLocaleString("en-IN")} shares`;
  const parts: Array<{ key: string; label: string; className: string; title: string }> = [];

  if (holding.available_lots > 0) {
    parts.push({
      key: "free",
      label: `${holding.available_lots} free`,
      className: "text-muted",
      title: `Unencumbered and deliverable — ${shares(holding.available_quantity)}`,
    });
  }
  if (holding.pledged_lots > 0) {
    parts.push({
      key: "pledged",
      label: `${holding.pledged_lots} pledged`,
      className: "text-amber-on-tint",
      title: `Counts as coverage, but must be unpledged before expiry to deliver — ${shares(
        holding.pledged_quantity,
      )}`,
    });
  }
  if (holding.blocked_lots > 0) {
    parts.push({
      key: "blocked",
      label: `${holding.blocked_lots} blocked`,
      className: "text-down-on-tint",
      title: `Blocked for trade, so not counted as coverage — ${shares(
        holding.blocked_quantity,
      )}`,
    });
  }

  return (
    <>
      <div className="font-mono text-micro text-faint">
        <span className="whitespace-nowrap" title={shares(holding.quantity)}>
          {holding.lots_held} lot{holding.lots_held === 1 ? "" : "s"}
        </span>
        {holding.existing_short_ce_lots > 0 && (
          <span
            className="whitespace-nowrap"
            title="Short calls already written against this holding"
          >
            {" "}
            · {holding.existing_short_ce_lots} written
          </span>
        )}
      </div>
      {parts.length > 0 && (
        <div className="font-mono text-micro">
          {parts.map((part, i) => (
            <span
              key={part.key}
              className={`whitespace-nowrap ${part.className}`}
              title={part.title}
            >
              {i > 0 && <span className="text-faint"> · </span>}
              {part.label}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

function ScripTable({
  holdings,
  prefs,
  config,
  disabled,
  onChange,
}: {
  holdings: HoldingRow[];
  prefs: ScripPref[];
  config: HoldingsWriterConfig;
  disabled: boolean;
  onChange: (code: string, patch: Partial<ScripPref>) => void;
}) {
  return (
    <>
      <div className="app-table-wrap">
        <table className="w-full text-left">
          <thead className="app-table-head">
            <tr>
              <th className="min-w-[8.5rem] px-2 py-2 text-micro font-bold uppercase tracking-[0.07em]">
                Scrip
              </th>
              <th className="px-2 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                CE lots
              </th>
              <th className="px-2 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                PE lots
              </th>
              <th className="px-2 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                CE %
              </th>
              <th className="px-2 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                PE %
              </th>
              <th className="px-2 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                Priority
              </th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((holding) => {
              const pref = prefFor(prefs, holding.stock_code);
              if (!holding.fno_eligible) {
                return (
                  <tr key={holding.stock_code} className="app-table-row opacity-50">
                    <td className="px-2 py-2 text-body font-semibold">
                      {holding.stock_code}
                    </td>
                    <td colSpan={5} className="px-2 py-2 text-hint text-faint">
                      {holding.ineligible_reason}
                    </td>
                  </tr>
                );
              }
              // Deliverable, not held: blocked stock is already earmarked and cannot be
              // delivered against a call, so it is not coverage. Pledged stock is.
              const covered =
                holding.deliverable_lots - holding.existing_short_ce_lots;
              return (
                <tr key={holding.stock_code} className="app-table-row align-top">
                  <td className="px-2 py-2">
                    <div className="text-body font-semibold">{holding.stock_code}</div>
                    {holding.current_market_price != null && (
                      <div className="font-mono text-micro text-faint">
                        spot{" "}
                        {holding.current_market_price.toLocaleString("en-IN", {
                          maximumFractionDigits: 2,
                        })}
                      </div>
                    )}
                    {/* Why the cap lands where it does. Typing a number into the CE column
                        is only defensible if the coverage behind it is visible — and a
                        total alone hides that some of it cannot be delivered. Spot is shown
                        because the CE/PE % columns are distances from it. */}
                    <LotBreakdown holding={holding} />
                  </td>
                  <td className="px-2 py-2 text-right">
                    <NumberCell
                      label={`${holding.stock_code} call lots`}
                      value={pref.ce_lots}
                      placeholder={String(Math.max(0, covered))}
                      disabled={disabled}
                      onChange={(next) =>
                        onChange(holding.stock_code, {
                          ce_lots: next,
                          ce_enabled: next === null || next > 0,
                        })
                      }
                    />
                  </td>
                  <td className="px-2 py-2 text-right">
                    <NumberCell
                      label={`${holding.stock_code} put lots`}
                      value={pref.pe_lots}
                      placeholder="0"
                      disabled={disabled}
                      onChange={(next) =>
                        onChange(holding.stock_code, {
                          pe_lots: next,
                          pe_enabled: next !== null && next > 0,
                        })
                      }
                    />
                  </td>
                  <td className="px-2 py-2 text-right">
                    <NumberCell
                      label={`${holding.stock_code} call distance`}
                      value={pref.safety_pct_ce}
                      placeholder={String(config.default_safety_pct_ce)}
                      step={0.5}
                      max={50}
                      disabled={disabled}
                      onChange={(next) =>
                        onChange(holding.stock_code, { safety_pct_ce: next })
                      }
                    />
                  </td>
                  <td className="px-2 py-2 text-right">
                    <NumberCell
                      label={`${holding.stock_code} put distance`}
                      value={pref.safety_pct_pe}
                      placeholder={String(config.default_safety_pct_pe)}
                      step={0.5}
                      max={50}
                      disabled={disabled}
                      onChange={(next) =>
                        onChange(holding.stock_code, { safety_pct_pe: next })
                      }
                    />
                  </td>
                  <td className="px-2 py-2 text-right">
                    <NumberCell
                      label={`${holding.stock_code} priority`}
                      value={pref.priority}
                      min={1}
                      disabled={disabled}
                      onChange={(next) =>
                        onChange(holding.stock_code, { priority: next ?? 1 })
                      }
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-hint text-faint">
        <span className="text-muted">Free</span> lots are unencumbered.{" "}
        <span className="text-amber-on-tint">Pledged</span> lots still count as coverage —
        unpledge them before expiry to deliver.{" "}
        <span className="text-down-on-tint">Blocked</span> lots are earmarked for trade and
        are not coverage, so they are excluded from the call cap.
      </p>
      <p className="mt-2 text-hint text-faint">
        Blank means the default: every covered lot for calls, none for puts, and the
        distances on the Limits tab. Priority decides who gets funded first when free
        margin or the delivery-cash budget cannot cover everything — lower goes first.
      </p>
      <p className="mt-2 text-hint text-faint">
        Calls are capped by stock you can deliver, so asking for more lots than that writes
        what is covered. Puts are not covered by stock at all — assignment means buying
        shares, funded from the delivery-cash budget.
      </p>
    </>
  );
}

function HoldingsSettings({
  telegramConnected,
  tab,
  config,
  onConfig,
  prefs,
  onPref,
  disabled,
}: {
  tab: string;
  config: HoldingsWriterConfig;
  onConfig: (patch: Partial<HoldingsWriterConfig>) => void;
  prefs: ScripPref[];
  onPref: (code: string, patch: Partial<ScripPref>) => void;
  disabled: boolean;
  telegramConnected: boolean;
}) {
  const holdings = useBotHoldings(tab === "scrips");

  if (tab === "scrips") {
    if (holdings.isLoading) {
      return <p className="app-text-muted text-body">Reading your holdings…</p>;
    }
    if (holdings.isError) {
      return (
        <p className="text-body text-down">
          Could not read your holdings: {(holdings.error as Error)?.message ?? "unknown error"}
        </p>
      );
    }
    if (!holdings.data?.length) {
      return <p className="app-text-muted text-body">No equity holdings found.</p>;
    }
    return (
      <ScripTable
        holdings={holdings.data}
        prefs={prefs}
        config={config}
        disabled={disabled}
        onChange={onPref}
      />
    );
  }

  if (tab === "schedule") {
    return (
      <div className="space-y-4">
        <ApprovalModeField
          value={config.approval_mode}
          connected={telegramConnected}
          disabled={disabled}
          onChange={(approval_mode) => onConfig({ approval_mode })}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Days before expiry"
            hint="Trading days, so it never lands on a weekend or a holiday. 0 is expiry day itself."
          >
            <NumberInput
              className="app-input"
              validityKey="fire_days_before_expiry"
              min={0}
              max={30}
              disabled={disabled}
              value={config.fire_days_before_expiry}
              onChange={(v) => onConfig({ fire_days_before_expiry: v })}
            />
          </Field>
          <SelectField
            label="Expiry"
            hint="Stock options are monthly only."
            value={config.expiry_preference}
            options={EXPIRY_OPTIONS}
            disabled={disabled}
            onChange={(expiry_preference) => onConfig({ expiry_preference })}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Start (IST)" hint="Fires here if you are logged in.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.nag_start_ist}
              onChange={(e) => onConfig({ nag_start_ist: e.target.value })}
            />
          </Field>
          <Field label="Until (IST)" hint="Last reminder and last trade.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.cutoff_ist}
              onChange={(e) => onConfig({ cutoff_ist: e.target.value })}
            />
          </Field>
          <Field label="Remind every (min)">
            <NumberInput
              className="app-input"
              validityKey="hw_nag_interval_minutes"
              min={5}
              max={120}
              disabled={disabled}
              value={config.nag_interval_minutes}
              onChange={(v) => onConfig({ nag_interval_minutes: v })}
            />
          </Field>
        </div>
        <p className="text-hint text-faint">
          Reminders only go out when your ICICI session has lapsed, and stop the moment you
          log in. No session by the cut-off means the month is skipped, with the reason in
          the run log.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Default call distance %" hint="Above spot. Per-scrip values override it.">
          <NumberInput
            className="app-input"
            validityKey="default_safety_pct_ce"
            step={0.5}
            min={0.5}
            max={50}
            disabled={disabled}
            value={config.default_safety_pct_ce}
            onChange={(v) => onConfig({ default_safety_pct_ce: v })}
          />
        </Field>
        <Field label="Default put distance %" hint="Below spot. Per-scrip values override it.">
          <NumberInput
            className="app-input"
            validityKey="default_safety_pct_pe"
            step={0.5}
            min={0.5}
            max={50}
            disabled={disabled}
            value={config.default_safety_pct_pe}
            onChange={(v) => onConfig({ default_safety_pct_pe: v })}
          />
        </Field>
      </div>
      <Field
        label="Delivery-cash budget (₹)"
        hint="Ceiling on what every written put would cost if all were assigned. Spent in scrip-priority order."
      >
        <NumberInput
          className="app-input"
          validityKey="delivery_cash_budget"
          step={10000}
          min={0}
          disabled={disabled}
          value={config.delivery_cash_budget}
          onChange={(v) => onConfig({ delivery_cash_budget: v })}
        />
      </Field>
      <Field
        label="Proposal validity (minutes)"
        hint="A manual run is a priced snapshot; after this it must be re-run."
      >
        <NumberInput
          className="app-input"
          validityKey="proposal_ttl_minutes"
          min={1}
          max={240}
          disabled={disabled}
          value={config.proposal_ttl_minutes}
          onChange={(v) => onConfig({ proposal_ttl_minutes: v })}
        />
      </Field>
    </div>
  );
}

// --- Bot 2 -----------------------------------------------------------------------------

function IndexPanel({
  code,
  leg,
  disabled,
  onChange,
}: {
  code: string;
  leg: IndexWriterLeg;
  disabled: boolean;
  onChange: (patch: Partial<IndexWriterLeg>) => void;
}) {
  const strategies = leg.strategies ?? [];
  const showCe = strategies.some((s) => s === "naked_ce" || s === "short_strangle");
  const showPe = strategies.some((s) => s === "naked_pe" || s === "short_strangle");

  function toggleStrategy(strategy: IndexStrategy) {
    const next = strategies.includes(strategy)
      ? strategies.filter((s) => s !== strategy)
      : [...strategies, strategy];
    // At least one has to stand: an index with an empty shortlist is enabled but mute,
    // which reads as a bug rather than as a choice.
    if (next.length === 0) return;
    onChange({ strategies: next });
  }

  return (
    <div className="app-card-muted p-3">
      <label className="flex cursor-pointer items-center gap-2">
        <Checkbox
          checked={leg.enabled}
          onChange={(enabled) => onChange({ enabled })}
          disabled={disabled}
          aria-label={`Trade ${INDEX_LABEL[code] ?? code}`}
        />
        <span className="text-body font-semibold">{INDEX_LABEL[code] ?? code}</span>
      </label>

      <div className="mt-3">
        <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">
          Strategies
        </span>
        <div className="mt-2 flex flex-wrap gap-2">
          {ALL_STRATEGIES.map((strategy) => {
            const on = strategies.includes(strategy);
            return (
              <button
                key={strategy}
                type="button"
                aria-pressed={on}
                disabled={disabled}
                onClick={() => toggleStrategy(strategy)}
                className={[
                  "rounded-lg border px-3 py-1.5 text-hint font-semibold transition",
                  "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45",
                  "disabled:pointer-events-none disabled:opacity-50",
                  on
                    ? "border-accent/45 bg-accent-tint text-accent-on-tint"
                    : "border-border bg-panel2 text-muted hover:text-text",
                ].join(" ")}
              >
                {STRATEGY_LABEL[strategy]}
              </button>
            );
          })}
        </div>
        {strategies.length > 1 && (
          <p className="mt-2 text-hint text-faint">
            With more than one picked, the bot trades whichever earns the most premium per
            rupee of margin. A strangle collects both premiums but ties up more margin, so
            it only wins when the extra premium pays for it.
          </p>
        )}
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        {showCe && (
          <Field label="CE distance %">
            <NumberInput
              className="app-input"
              validityKey={`${code}_safety_pct_ce`}
              step={0.25}
              min={0.25}
              max={50}
              disabled={disabled}
              value={leg.safety_pct_ce}
              onChange={(v) => onChange({ safety_pct_ce: v })}
            />
          </Field>
        )}
        {showPe && (
          <Field label="PE distance %">
            <NumberInput
              className="app-input"
              validityKey={`${code}_safety_pct_pe`}
              step={0.25}
              min={0.25}
              max={50}
              disabled={disabled}
              value={leg.safety_pct_pe}
              onChange={(v) => onChange({ safety_pct_pe: v })}
            />
          </Field>
        )}
        <Field label="Margin cap %">
          <NumberInput
            className="app-input"
            validityKey={`${code}_margin_pct_cap`}
            step={5}
            min={1}
            max={100}
            disabled={disabled}
            value={leg.margin_pct_cap}
            onChange={(v) => onChange({ margin_pct_cap: v })}
          />
        </Field>
        <Field label="Priority">
          <NumberInput
            className="app-input"
            validityKey={`${code}_priority`}
            min={1}
            max={9}
            disabled={disabled}
            value={leg.priority}
            onChange={(v) => onChange({ priority: v })}
          />
        </Field>
      </div>
    </div>
  );
}

function IndexSettings({
  telegramConnected,
  tab,
  config,
  onConfig,
  disabled,
}: {
  tab: string;
  config: ExpiryIndexWriterConfig;
  onConfig: (patch: Partial<ExpiryIndexWriterConfig>) => void;
  disabled: boolean;
  telegramConnected: boolean;
}) {
  if (tab === "indices") {
    return (
      <div className="space-y-3">
        {Object.entries(config.indices ?? {}).map(([code, leg]) => (
          <IndexPanel
            key={code}
            code={code}
            leg={leg}
            disabled={disabled}
            onChange={(patch) =>
              onConfig({ indices: { ...config.indices, [code]: { ...leg, ...patch } } })
            }
          />
        ))}
        <p className="text-hint text-faint">
          Each index has its own margin cap, so a same-day expiry cannot over-commit.
          Priority only breaks the tie if NIFTY and SENSEX ever expire on the same day —
          lower sizes first.
        </p>
      </div>
    );
  }

  if (tab === "schedule") {
    return (
      <div className="space-y-4">
        <ApprovalModeField
          value={config.approval_mode}
          connected={telegramConnected}
          disabled={disabled}
          onChange={(approval_mode) => onConfig({ approval_mode })}
        />
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Entry (IST)" hint="Fires here if a broker session exists.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.entry_time_ist}
              onChange={(e) => onConfig({ entry_time_ist: e.target.value })}
            />
          </Field>
          <Field label="Remind from (IST)" hint="Or when the app starts, whichever is later.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.nag_start_ist}
              onChange={(e) => onConfig({ nag_start_ist: e.target.value })}
            />
          </Field>
          <Field label="Until (IST)" hint="Last reminder and last trade.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.cutoff_ist}
              onChange={(e) => onConfig({ cutoff_ist: e.target.value })}
            />
          </Field>
        </div>
        <Field label="Remind every (min)">
          <NumberInput
            className="app-input"
            validityKey="idx_nag_interval_minutes"
            min={5}
            max={120}
            disabled={disabled}
            value={config.nag_interval_minutes}
            onChange={(v) => onConfig({ nag_interval_minutes: v })}
          />
        </Field>
        <p className="text-hint text-faint">
          A session arriving late still trades — right up to the cut-off. No session by then
          means the day is skipped, with the reason in the run log.
        </p>
      </div>
    );
  }

  const bookAll = config.profit_book_premium_pct >= 100;
  return (
    <div className="space-y-4">
      <Field
        label="Book at % of premium"
        hint="How much of the premium to capture before buying the position back."
      >
        <NumberInput
          className="app-input"
          validityKey="profit_book_premium_pct"
          step={5}
          min={5}
          max={100}
          disabled={disabled}
          value={config.profit_book_premium_pct}
          onChange={(v) => onConfig({ profit_book_premium_pct: v })}
        />
      </Field>
      {bookAll ? (
        <p className="rounded-lg border border-amber/30 bg-amber-tint p-3 text-hint text-text">
          <span className="font-semibold text-amber-on-tint">Set to let it expire.</span>{" "}
          At 100% there is nothing left to buy back, so no profit exit is armed and the
          position runs to expiry. Your stop-loss stays live throughout.
        </p>
      ) : (
        <p className="text-hint text-faint">
          Exits when the option can be bought back at {100 - config.profit_book_premium_pct}%
          of what you sold it for. On a strangle both legs must reach it — booking one side
          alone would leave the other naked.
        </p>
      )}
      <Field
        label="Stop at N × premium"
        hint="1 means stop once the loss equals the premium collected."
      >
        <NumberInput
          className="app-input"
          validityKey="loss_limit_premium_multiple"
          step={0.25}
          min={0.25}
          max={10}
          disabled={disabled}
          value={config.loss_limit_premium_multiple}
          onChange={(v) => onConfig({ loss_limit_premium_multiple: v })}
        />
      </Field>
    </div>
  );
}

// --- shell -----------------------------------------------------------------------------

export function BotSettingsDrawer({
  bot,
  open,
  readOnly,
  onClose,
}: {
  bot: Bot;
  open: boolean;
  readOnly: boolean;
  onClose: () => void;
}) {
  const meta = BOT_META[bot.bot_type];
  const isHoldings = bot.bot_type === BOT_HOLDINGS_WRITER;
  const tabs = isHoldings ? HOLDINGS_TABS : INDEX_TABS;
  const titleId = useId();

  const update = useUpdateBot();
  const savePrefs = useSaveScripPrefs();
  const storedPrefs = useScripPrefs(open && isHoldings);

  // Only while the drawer is open: nothing outside it depends on the link status, and the
  // approval-mode warning is the only thing that reads it.
  const telegram = useQuery({
    queryKey: TELEGRAM_STATUS_QUERY_KEY,
    queryFn: fetchTelegramStatus,
    enabled: open,
  });
  const telegramConnected = Boolean(telegram.data?.connected && telegram.data?.alerts_enabled);

  const [tab, setTab] = useState(tabs[0].id);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>(bot.config);
  const [prefDraft, setPrefDraft] = useState<Record<string, ScripPref>>({});
  const [invalidFields, setInvalidFields] = useState<Record<string, boolean>>({});

  const reportValidity = useCallback((key: string, valid: boolean) => {
    setInvalidFields((current) => {
      if (Boolean(current[key]) === !valid) return current;
      return { ...current, [key]: !valid };
    });
  }, []);
  const anyInvalid = Object.values(invalidFields).some(Boolean);

  // The server normalizes config on save, so the draft resyncs from the response rather
  // than keeping what was typed. Keyed on the serialized *values*: react-query hands back a
  // fresh object on every refetch, so an identity check would wipe a half-finished edit
  // each time the query revalidated.
  const serverConfig = JSON.stringify(bot.config);
  const [syncedConfig, setSyncedConfig] = useState(serverConfig);
  if (serverConfig !== syncedConfig) {
    setSyncedConfig(serverConfig);
    setDraft(bot.config);
  }

  const prefs: ScripPref[] = Object.values({
    ...Object.fromEntries((storedPrefs.data ?? []).map((p) => [p.stock_code, p])),
    ...prefDraft,
  });

  const configDirty = JSON.stringify(draft) !== serverConfig;
  const prefsDirty = Object.keys(prefDraft).length > 0;
  const dirty = configDirty || prefsDirty;
  const pending = update.isPending || savePrefs.isPending;

  function patchPref(code: string, patch: Partial<ScripPref>) {
    setPrefDraft((current) => {
      const base =
        current[code] ??
        (storedPrefs.data ?? []).find((p) => p.stock_code === code) ??
        prefFor([], code);
      return { ...current, [code]: { ...base, ...patch, stock_code: code } };
    });
  }

  async function save() {
    setError(null);
    try {
      if (configDirty) {
        await update.mutateAsync({ botType: bot.bot_type, config: draft });
      }
      if (prefsDirty) {
        await savePrefs.mutateAsync(Object.values(prefDraft));
        setPrefDraft({});
      }
      // Close on success: the drawer's own copy is "changes apply to the next run", so
      // there is nothing more to do here, and a drawer that stays open with everything
      // greyed out reads as "did that work?".
      onClose();
    } catch (e) {
      setError((e as Error)?.message ?? "Could not save.");
    }
  }

  function discard() {
    setDraft(bot.config);
    setPrefDraft({});
    setError(null);
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      variant="drawer"
      drawerSide="right"
      drawerWidthClass="w-[min(100%,34rem)]"
      titleId={titleId}
      pending={pending}
    >
      <div className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div>
          <h2 id={titleId} className="text-subtitle font-bold">
            {meta.title}
          </h2>
          <p className="app-text-muted mt-1 text-hint">
            Settings — changes apply to the next run.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close settings"
          className="rounded p-1 text-faint transition hover:text-text focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-4">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex gap-1 border-b border-border px-4" role="tablist">
        {tabs.map((entry) => {
          const active = entry.id === tab;
          // An unsaved edit on a tab you have navigated away from is invisible otherwise,
          // and the footer count alone does not say *where* it is.
          const hasEdits =
            (entry.id === "scrips" && prefsDirty) ||
            (entry.id !== "scrips" && configDirty);
          return (
            <button
              key={entry.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(entry.id)}
              className={[
                "-mb-px border-b-2 px-3 py-2.5 text-hint font-semibold transition",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45",
                active
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:text-text",
              ].join(" ")}
            >
              {entry.label}
              {hasEdits && (
                <span className="ms-1.5 inline-block size-1.5 rounded-full bg-amber align-middle" aria-label="unsaved" />
              )}
            </button>
          );
        })}
      </div>

      <FieldValidityContext.Provider value={reportValidity}>
        <div className="flex-1 overflow-auto p-4">
          {isHoldings ? (
            <HoldingsSettings
              tab={tab}
              config={draft as unknown as HoldingsWriterConfig}
              onConfig={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              prefs={prefs}
              onPref={patchPref}
              disabled={readOnly || pending}
              telegramConnected={telegramConnected}
            />
          ) : (
            <IndexSettings
              tab={tab}
              config={draft as unknown as ExpiryIndexWriterConfig}
              onConfig={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              disabled={readOnly || pending}
              telegramConnected={telegramConnected}
            />
          )}
          {error && <p className="mt-4 text-body text-down">{error}</p>}
        </div>
      </FieldValidityContext.Provider>

      <div className="flex items-center justify-between gap-3 border-t border-border bg-panel p-3">
        <span className={`text-hint ${anyInvalid && !readOnly ? "text-down" : "text-faint"}`}>
          {readOnly
            ? "Read-only mode — settings cannot be changed."
            : anyInvalid
              ? "A highlighted field is empty or out of range."
              : dirty
                ? "Unsaved changes"
                : "All changes saved"}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            className="app-btn-secondary"
            onClick={discard}
            disabled={!dirty || pending}
          >
            Discard
          </button>
          <button
            type="button"
            className="app-btn-primary"
            onClick={() => void save()}
            disabled={readOnly || !dirty || pending || anyInvalid}
          >
            {pending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
