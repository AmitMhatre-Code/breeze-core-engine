"use client";

import { useCallback, useId, useState } from "react";
import { BACKTEST_SLUG } from "@/lib/bots-backtest";
import { Modal } from "@/components/ui/Modal";
import { Checkbox } from "@/components/ui/Checkbox";
import { NumberInput, FieldValidityContext } from "@/components/ui/NumberInput";
import { Select, type SelectOption } from "@/components/ui/Select";
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
  isScalper,
  BOT_IRON_FLY_SCALPER,
  BOT_DYNAMIC_CONDOR,
  type HoldingsWriterConfig,
  type MomentumLongScalperConfig,
  type IndexStrategy,
  type IndexWriterLeg,
  type ScripPref,
} from "@/lib/use-bots";

import {
  IRON_FLY_TABS,
  MOMENTUM_TABS,
  ScalperSettings,
  type Tab,
} from "@/components/bots/ScalperSettings";
import { CAS_BINGO_TABS, CasBingoSettings } from "@/components/bots/CasBingoSettings";
import { TabIntro, WorkedExample, rupees } from "@/components/bots/SettingsHelp";
import { CONDOR_TABS, CondorSettings, type CondorBotConfig } from "@/components/bots/CondorSettings";
import type { CasBingoConfig } from "@/lib/use-bots";


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

const STRATEGY_HINT: Record<IndexStrategy, string> = {
  naked_ce: "sells a call above the index; it profits unless the index rises past the strike.",
  naked_pe: "sells a put below the index; it profits unless the index falls past the strike.",
  short_strangle: "sells both; it profits while the index stays between the two strikes.",
};

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
      <div className="mb-4">
        <TabIntro>
          <p>
            Each month the bot sells (&ldquo;writes&rdquo;) options against the stocks below and keeps the premium.
            One row per holding that has NSE options. Leave a cell blank to use the default.
          </p>
          <ul className="list-disc space-y-1 pl-4">
            <li>
              <b>CE lots</b> — call lots to sell. Always covered: never more than the shares you can deliver.
              Blank sells every covered lot.
            </li>
            <li>
              <b>PE lots</b> — put lots to sell. If assigned you buy the shares, paid from the delivery-cash
              budget on the Limits tab. Blank sells none.
            </li>
            <li>
              <b>CE %</b> / <b>PE %</b>{" "}— how far above (call) or below (put) the stock&rsquo;s current price the
              strike sits. Further is safer but earns less. Blank uses the Limits tab&rsquo;s defaults.
            </li>
            <li>
              <b>Priority</b> — who is funded first when free margin or delivery cash cannot cover every row. Lower
              goes first.
            </li>
          </ul>
        </TabIntro>
      </div>
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
        Asking for more call lots than you can deliver writes only what is covered.
      </p>
    </>
  );
}

function HoldingsSettings({
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
        <TabIntro>
          <p>
            When the bot writes each month, in Semi-auto or Auto. In Manual it waits for you to start a run. It needs
            a live ICICI session to trade, and ICICI sessions end every night, so if you are not logged in it reminds
            you on Telegram until you are.
          </p>
        </TabIntro>
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
            hint="Which month's options to write. Stock options expire monthly only."
            value={config.expiry_preference}
            options={EXPIRY_OPTIONS}
            disabled={disabled}
            onChange={(expiry_preference) => onConfig({ expiry_preference })}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Start (IST)" hint="Writes at this time if you are logged in; otherwise reminders start.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.nag_start_ist}
              onChange={(e) => onConfig({ nag_start_ist: e.target.value })}
            />
          </Field>
          <Field label="Until (IST)" hint="Last reminder and last chance to trade that day.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.cutoff_ist}
              onChange={(e) => onConfig({ cutoff_ist: e.target.value })}
            />
          </Field>
          <Field label="Remind every (min)" hint="Gap between Telegram reminders.">
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
        <WorkedExample title="With these settings">
          <li>
            It writes {config.expiry_preference === "next" ? "next month's" : "this month's"} options at{" "}
            <b>{config.nag_start_ist}</b>,{" "}
            {config.fire_days_before_expiry === 0 ? (
              <b>on the day they expire</b>
            ) : (
              <b>
                {config.fire_days_before_expiry} trading day{config.fire_days_before_expiry === 1 ? "" : "s"} before
                they expire
              </b>
            )}
            , if your ICICI session is live.
          </li>
          <li>
            If not, it reminds you every <b>{config.nag_interval_minutes} minutes</b> and writes as soon as you log
            in. No session by <b>{config.cutoff_ist}</b> and that month is skipped, with the reason in Activity.
          </li>
        </WorkedExample>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <TabIntro>
        <p>
          Defaults for every stock on the Scrips tab, and the limits on what the bot may commit.
        </p>
      </TabIntro>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Default call distance %" hint="How far above the stock's current price a written call's strike sits. A row on the Scrips tab can set its own.">
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
        <Field label="Default put distance %" hint="How far below the stock's current price a written put's strike sits. A row on the Scrips tab can set its own.">
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
        hint="A written put can be assigned: you then buy the shares at its strike. This caps what buying the shares for every written put would cost, all at once. Spent in Priority order; ₹0 writes no puts."
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
        hint="How long a priced proposal (a manual run, or a Telegram request in Semi-auto) can be placed. After this it must be re-priced, so nothing is placed on stale prices."
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
      <WorkedExample title="Example: a stock trading at ₹1,000">
        <li>
          A call is written at <b>{rupees(1000 * (1 + config.default_safety_pct_ce / 100), 0)}</b> or the next listed
          strike above — strikes are always rounded further from the price, never closer.
        </li>
        <li>
          A put is written at <b>{rupees(1000 * (1 - config.default_safety_pct_pe / 100), 0)}</b> or the next listed
          strike below.
        </li>
      </WorkedExample>
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
        <ul className="mt-2 space-y-0.5 text-hint text-faint">
          {ALL_STRATEGIES.map((strategy) => (
            <li key={strategy}>
              <b>{STRATEGY_LABEL[strategy]}</b> {STRATEGY_HINT[strategy]}
            </li>
          ))}
        </ul>
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
          <Field label="CE distance %" hint="Call strike, this far above the index.">
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
          <Field label="PE distance %" hint="Put strike, this far below the index.">
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
        <Field label="Margin cap %" hint="Most of your free margin this index may use.">
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
        <Field label="Priority" hint="Lower is sized first on a shared expiry day.">
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
  tab,
  config,
  onConfig,
  disabled,
}: {
  tab: string;
  config: ExpiryIndexWriterConfig;
  onConfig: (patch: Partial<ExpiryIndexWriterConfig>) => void;
  disabled: boolean;
}) {
  if (tab === "indices") {
    return (
      <div className="space-y-3">
        <TabIntro>
          <p>
            On a NIFTY or SENSEX expiry morning, the bot sells options on that index which are out of the money and
            should lose most of their value by the close. It sells as many lots as fit the margin cap. These are{" "}
            <b>naked</b> options — unhedged — so the stop on the Exits tab, armed the moment the sale fills, is what
            limits a loss.
          </p>
          <p>
            Distances are measured from the index level when it fires, and strikes are rounded further out, never
            closer.
          </p>
        </TabIntro>
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
        <TabIntro>
          <p>
            When the bot sells on an expiry day, in Semi-auto or Auto. In Manual it waits for you to start a run. It
            needs a live ICICI session, and ICICI sessions end every night, so if you are not logged in it reminds you
            on Telegram until you are.
          </p>
        </TabIntro>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Entry (IST)" hint="Sells at this time if you are logged in.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.entry_time_ist}
              onChange={(e) => onConfig({ entry_time_ist: e.target.value })}
            />
          </Field>
          <Field label="Remind from (IST)" hint="When reminders start if you are not logged in (or when the app starts, if later).">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.nag_start_ist}
              onChange={(e) => onConfig({ nag_start_ist: e.target.value })}
            />
          </Field>
          <Field label="Until (IST)" hint="Last reminder and last chance to trade that day.">
            <input
              type="time"
              className="app-input"
              disabled={disabled}
              value={config.cutoff_ist}
              onChange={(e) => onConfig({ cutoff_ist: e.target.value })}
            />
          </Field>
        </div>
        <Field label="Remind every (min)" hint="Gap between Telegram reminders.">
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
        <WorkedExample title="With these settings, on an expiry day">
          <li>
            It sells at <b>{config.entry_time_ist}</b> if your ICICI session is live.
          </li>
          <li>
            If not, it reminds you from <b>{config.nag_start_ist}</b> every{" "}
            <b>{config.nag_interval_minutes} minutes</b>, and sells as soon as you log in. No session by{" "}
            <b>{config.cutoff_ist}</b> and the day is skipped, with the reason in Activity.
          </li>
        </WorkedExample>
      </div>
    );
  }

  const bookAll = config.profit_book_premium_pct >= 100;
  const premium = 10_000;
  const kept = (premium * config.profit_book_premium_pct) / 100;
  const stopLoss = premium * config.loss_limit_premium_multiple;
  return (
    <div className="space-y-4">
      <TabIntro>
        <p>
          The <b>premium</b> is what the bot collects when it sells. Both exits are armed as a Profit Booking / Stop
          Loss rule the moment the sale fills, and show on Portfolio like one you set yourself.
        </p>
      </TabIntro>
      <Field
        label="Book at % of premium"
        hint="How much of the premium to keep before buying the position back."
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
        hint="Closes once the loss reaches this many times the premium collected. 1 means the loss equals the premium."
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
      <WorkedExample
        title={<>Example with these settings: {rupees(premium, 0)} of premium collected</>}
        footer="Before charges. On a strangle the stop is on the two legs together."
      >
        <li>
          {bookAll ? (
            <>No profit exit: the position is left to expire, keeping all {rupees(premium, 0)} if it expires worthless.</>
          ) : (
            <>
              Books once it can be bought back for <b>{rupees(premium - kept, 0)}</b>, keeping{" "}
              <b>{rupees(kept, 0)}</b>.
            </>
          )}
        </li>
        <li>
          Stops out once the loss reaches <b>{rupees(stopLoss, 0)}</b> — buying back would then cost{" "}
          {rupees(premium + stopLoss, 0)}.
        </li>
      </WorkedExample>
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
  const scalper = isScalper(bot.bot_type);
  const casBingo = bot.bot_type === "cas_bingo";
  const condor = bot.bot_type === BOT_DYNAMIC_CONDOR;
  const tabs = condor
    ? CONDOR_TABS
    : casBingo
    ? CAS_BINGO_TABS
    : scalper
      ? bot.bot_type === BOT_IRON_FLY_SCALPER
        ? IRON_FLY_TABS
        : MOMENTUM_TABS
      : isHoldings
        ? HOLDINGS_TABS
        : INDEX_TABS;
  const titleId = useId();

  const update = useUpdateBot();
  const savePrefs = useSaveScripPrefs();
  const storedPrefs = useScripPrefs(open && isHoldings);

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
          {condor ? (
            <CondorSettings
              tab={tab}
              config={draft as unknown as CondorBotConfig}
              onConfig={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              disabled={readOnly || pending}
            />
          ) : casBingo ? (
            <CasBingoSettings
              tab={tab}
              config={draft as unknown as CasBingoConfig}
              onConfig={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              disabled={readOnly || pending}
            />
          ) : scalper ? (
            <ScalperSettings
              bot={bot}
              tab={tab}
              config={draft as unknown as MomentumLongScalperConfig}
              onConfig={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              disabled={readOnly || pending}
            />
          ) : isHoldings ? (
            <HoldingsSettings
              tab={tab}
              config={draft as unknown as HoldingsWriterConfig}
              onConfig={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              prefs={prefs}
              onPref={patchPref}
              disabled={readOnly || pending}
            />
          ) : (
            <IndexSettings
              tab={tab}
              config={draft as unknown as ExpiryIndexWriterConfig}
              onConfig={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              disabled={readOnly || pending}
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
              ? "A setting needs fixing before saving — look for the field or message in red."
              : dirty
                ? "Unsaved changes"
                : "All changes saved"}
          {/* A backtest replays the SAVED settings, so the loop is change → save → backtest.
              Pointed at only once there is nothing unsaved, because a run started now would
              silently test the stored numbers rather than the ones on screen. */}
          {!dirty && !anyInvalid && BACKTEST_SLUG[bot.bot_type] ? (
            <> · Backtest these settings with the clock on the card</>
          ) : null}
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
