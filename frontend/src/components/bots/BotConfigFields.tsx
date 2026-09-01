"use client";

import { Checkbox } from "@/components/ui/Checkbox";
import type {
  BotType,
  ExpiryIndexWriterConfig,
  HoldingsWriterConfig,
  IndexWriterLeg,
} from "@/lib/use-bots";
import { BOT_EXPIRY_INDEX_WRITER, BOT_HOLDINGS_WRITER } from "@/lib/use-bots";

const INDEX_LABEL: Record<string, string> = { NIFTY: "NIFTY", BSESEN: "SENSEX" };

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
      <span className="block text-xs font-medium">{label}</span>
      {children}
      {hint && <span className="app-text-muted mt-1 block text-[11px]">{hint}</span>}
    </label>
  );
}

export function HoldingsWriterFields({
  config,
  onChange,
}: {
  config: HoldingsWriterConfig;
  onChange: (patch: Partial<HoldingsWriterConfig>) => void;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Call safety %" hint="Distance above spot for written calls.">
        <input
          type="number"
          step="0.5"
          min="0.5"
          max="50"
          className="app-input mt-1 w-full"
          value={config.default_safety_pct_ce}
          onChange={(e) => onChange({ default_safety_pct_ce: Number(e.target.value) })}
        />
      </Field>
      <Field label="Put safety %" hint="Distance below spot for written puts.">
        <input
          type="number"
          step="0.5"
          min="0.5"
          max="50"
          className="app-input mt-1 w-full"
          value={config.default_safety_pct_pe}
          onChange={(e) => onChange({ default_safety_pct_pe: Number(e.target.value) })}
        />
      </Field>
      <Field
        label="Delivery-cash budget (₹)"
        hint="Ceiling on total assignment cost across all written puts. Calls are capped by your holdings instead."
      >
        <input
          type="number"
          step="10000"
          min="0"
          className="app-input mt-1 w-full"
          value={config.delivery_cash_budget}
          onChange={(e) => onChange({ delivery_cash_budget: Number(e.target.value) })}
        />
      </Field>
      <Field label="Expiry" hint="Stock options are monthly only.">
        <select
          className="app-input mt-1 w-full"
          value={config.expiry_preference}
          onChange={(e) =>
            onChange({ expiry_preference: e.target.value as "current" | "next" })
          }
        >
          <option value="current">Current month</option>
          <option value="next">Next month</option>
        </select>
      </Field>
      <Field
        label="Proposal validity (minutes)"
        hint="A proposal is a priced snapshot; after this it must be re-scanned."
      >
        <input
          type="number"
          min="1"
          max="240"
          className="app-input mt-1 w-full"
          value={config.proposal_ttl_minutes}
          onChange={(e) => onChange({ proposal_ttl_minutes: Number(e.target.value) })}
        />
      </Field>
    </div>
  );
}

function IndexRow({
  code,
  leg,
  onChange,
}: {
  code: string;
  leg: IndexWriterLeg;
  onChange: (patch: Partial<IndexWriterLeg>) => void;
}) {
  return (
    <div className="app-card-muted p-3">
      <label className="flex cursor-pointer items-center gap-2">
        <Checkbox
          checked={leg.enabled}
          onChange={(enabled) => onChange({ enabled })}
          aria-label={`Trade ${INDEX_LABEL[code] ?? code}`}
        />
        <span className="text-sm font-medium">{INDEX_LABEL[code] ?? code}</span>
      </label>
      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        <Field label="Side">
          <select
            className="app-input mt-1 w-full"
            value={leg.right}
            onChange={(e) => onChange({ right: e.target.value as "call" | "put" })}
          >
            <option value="put">Short PE</option>
            <option value="call">Short CE</option>
          </select>
        </Field>
        <Field label="Safety %">
          <input
            type="number"
            step="0.25"
            min="0.25"
            max="50"
            className="app-input mt-1 w-full"
            value={leg.safety_pct}
            onChange={(e) => onChange({ safety_pct: Number(e.target.value) })}
          />
        </Field>
        <Field label="Margin cap %">
          <input
            type="number"
            step="5"
            min="1"
            max="100"
            className="app-input mt-1 w-full"
            value={leg.margin_pct_cap}
            onChange={(e) => onChange({ margin_pct_cap: Number(e.target.value) })}
          />
        </Field>
        <Field label="Priority">
          <input
            type="number"
            min="1"
            max="9"
            className="app-input mt-1 w-full"
            value={leg.priority}
            onChange={(e) => onChange({ priority: Number(e.target.value) })}
          />
        </Field>
      </div>
    </div>
  );
}

export function ExpiryIndexWriterFields({
  config,
  onChange,
}: {
  config: ExpiryIndexWriterConfig;
  onChange: (patch: Partial<ExpiryIndexWriterConfig>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {Object.entries(config.indices).map(([code, leg]) => (
          <IndexRow
            key={code}
            code={code}
            leg={leg}
            onChange={(patch) =>
              onChange({ indices: { ...config.indices, [code]: { ...leg, ...patch } } })
            }
          />
        ))}
        <p className="app-text-muted text-[11px]">
          Each index has its own margin cap, so a same-day expiry cannot over-commit.
          Priority only breaks the tie if NIFTY and SENSEX ever expire on the same day —
          lower fires first.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Entry time (IST)" hint="Fires here if a broker session exists.">
          <input
            type="time"
            className="app-input mt-1 w-full"
            value={config.entry_time_ist}
            onChange={(e) => onChange({ entry_time_ist: e.target.value })}
          />
        </Field>
        <Field label="Reminders from (IST)" hint="Or when the app starts, whichever is later.">
          <input
            type="time"
            className="app-input mt-1 w-full"
            value={config.nag_start_ist}
            onChange={(e) => onChange({ nag_start_ist: e.target.value })}
          />
        </Field>
        <Field
          label="Cut-off (IST)"
          hint="Last reminder and last trade. No session by now means the day is skipped."
        >
          <input
            type="time"
            className="app-input mt-1 w-full"
            value={config.cutoff_ist}
            onChange={(e) => onChange({ cutoff_ist: e.target.value })}
          />
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Reminder interval (min)">
          <input
            type="number"
            min="5"
            max="120"
            className="app-input mt-1 w-full"
            value={config.nag_interval_minutes}
            onChange={(e) => onChange({ nag_interval_minutes: Number(e.target.value) })}
          />
        </Field>
        <Field label="Stop at N × premium" hint="1 = stop at a 100% loss on the premium.">
          <input
            type="number"
            step="0.25"
            min="0.25"
            max="10"
            className="app-input mt-1 w-full"
            value={config.loss_limit_premium_multiple}
            onChange={(e) =>
              onChange({ loss_limit_premium_multiple: Number(e.target.value) })
            }
          />
        </Field>
        <Field
          label="Book at option price (₹)"
          hint="Exit when the option trades at or below this."
        >
          <input
            type="number"
            step="0.05"
            min="0.05"
            className="app-input mt-1 w-full"
            value={config.profit_target_option_price}
            onChange={(e) =>
              onChange({ profit_target_option_price: Number(e.target.value) })
            }
          />
        </Field>
      </div>
    </div>
  );
}

export function BotConfigFields({
  botType,
  config,
  onChange,
}: {
  botType: BotType;
  config: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  if (botType === BOT_HOLDINGS_WRITER) {
    return (
      <HoldingsWriterFields
        config={config as unknown as HoldingsWriterConfig}
        onChange={onChange}
      />
    );
  }
  if (botType === BOT_EXPIRY_INDEX_WRITER) {
    return (
      <ExpiryIndexWriterFields
        config={config as unknown as ExpiryIndexWriterConfig}
        onChange={onChange}
      />
    );
  }
  return null;
}
