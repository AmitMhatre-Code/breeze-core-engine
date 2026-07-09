"use client";

import { useState, type ComponentType, type ReactNode } from "react";

import { AdvancedPnlEngineScreen } from "./screens/AdvancedPnlEngineScreen";
import { ApiPlaygroundScreen } from "./screens/ApiPlaygroundScreen";
import { ApiUsageScreen } from "./screens/ApiUsageScreen";
import { AuditLogsScreen } from "./screens/AuditLogsScreen";
import { BrokerCredentialsScreen } from "./screens/BrokerCredentialsScreen";
import { DeleteAccountScreen } from "./screens/DeleteAccountScreen";
import { ExchangeCalendarScreen } from "./screens/ExchangeCalendarScreen";
import { QuantityLimitsScreen } from "./screens/QuantityLimitsScreen";
import { ReferenceDataLoadsScreen } from "./screens/ReferenceDataLoadsScreen";

type ScreenKey =
  | "credentials"
  | "quantity-limits"
  | "api-usage"
  | "reference-data-loads"
  | "exchange-calendar"
  | "advanced-pnl-engine"
  | "audit-logs"
  | "api-playground"
  | "delete-account";

type NavItem = {
  key: ScreenKey;
  label: string;
  icon: ComponentType;
};

type NavGroup = {
  label: string | null;
  danger?: boolean;
  items: NavItem[];
};

const SCREENS: Record<ScreenKey, ComponentType> = {
  credentials: BrokerCredentialsScreen,
  "quantity-limits": QuantityLimitsScreen,
  "api-usage": ApiUsageScreen,
  "reference-data-loads": ReferenceDataLoadsScreen,
  "exchange-calendar": ExchangeCalendarScreen,
  "advanced-pnl-engine": AdvancedPnlEngineScreen,
  "audit-logs": AuditLogsScreen,
  "api-playground": ApiPlaygroundScreen,
  "delete-account": DeleteAccountScreen,
};

const NAV_GROUPS: NavGroup[] = [
  {
    label: null,
    items: [
      { key: "credentials", label: "Broker Credentials", icon: LockIcon },
      { key: "quantity-limits", label: "Quantity Limits", icon: GaugeIcon },
      { key: "api-usage", label: "API Usage", icon: ActivityIcon },
    ],
  },
  {
    label: "Automation",
    items: [
      { key: "reference-data-loads", label: "Reference Data Loads", icon: DatabaseIcon },
      { key: "exchange-calendar", label: "Exchange Calendar", icon: CalendarIcon },
    ],
  },
  {
    label: "Advanced",
    items: [{ key: "advanced-pnl-engine", label: "Engine Settings", icon: WarningIcon }],
  },
  {
    label: "Diagnostics",
    items: [{ key: "audit-logs", label: "Audit Logs", icon: DocIcon }],
  },
  {
    label: "Danger zone",
    danger: true,
    items: [
      { key: "api-playground", label: "API Playground", icon: WarningIcon },
      { key: "delete-account", label: "Delete Account", icon: TrashIcon },
    ],
  },
];

const FLAT_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);
const DANGER_KEYS = new Set<ScreenKey>(
  NAV_GROUPS.filter((g) => g.danger).flatMap((g) => g.items.map((i) => i.key)),
);

function navItemClass(active: boolean, danger: boolean): string {
  const base = "group relative flex w-full items-center gap-2.5 rounded-[7px] px-2.5 py-2 text-left text-heading transition";
  if (active) {
    return danger
      ? `${base} bg-down-tint font-semibold text-foreground`
      : `${base} bg-accent-tint font-semibold text-foreground`;
  }
  return danger
    ? `${base} text-down/80 hover:bg-down-tint hover:text-down`
    : `${base} text-muted hover:bg-panel2 hover:text-foreground`;
}

function pillItemClass(active: boolean, danger: boolean): string {
  const base =
    "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium transition";
  if (active) {
    return danger
      ? `${base} border-down bg-down-tint text-down`
      : `${base} border-accent-strong bg-accent-tint text-accent-strong`;
  }
  return danger
    ? `${base} border-down/30 bg-panel text-down/80`
    : `${base} border-border bg-panel text-muted`;
}

export function SettingsShell() {
  const [active, setActive] = useState<ScreenKey>("credentials");
  const ActiveScreen = SCREENS[active];

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1 className="app-text-title">Settings</h1>
        <p className="app-text-muted">Broker connection, limits & preferences.</p>
      </header>

      <div className="md:hidden">
        <nav
          aria-label="Settings"
          className="flex gap-2 overflow-x-auto pb-1"
        >
          {FLAT_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setActive(item.key)}
              className={pillItemClass(active === item.key, DANGER_KEYS.has(item.key))}
            >
              <item.icon />
              {item.label}
            </button>
          ))}
        </nav>
      </div>

      <div className="flex flex-col gap-4 md:flex-row md:items-start">
        <nav
          aria-label="Settings"
          className="hidden shrink-0 flex-col md:sticky md:top-0 md:flex md:w-[252px]"
        >
          <div className="space-y-4">
            {NAV_GROUPS.map((group, idx) => (
              <div key={group.label ?? `group-${idx}`} className="space-y-0.5">
                {group.label ? (
                  <div
                    className={[
                      "px-2.5 pb-1 pt-2 text-body font-bold uppercase tracking-[.08em]",
                      group.danger ? "text-down" : "text-faint",
                    ].join(" ")}
                  >
                    {group.label}
                  </div>
                ) : null}
                {group.items.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setActive(item.key)}
                    aria-current={active === item.key ? "page" : undefined}
                    className={navItemClass(active === item.key, Boolean(group.danger))}
                  >
                    {active === item.key ? (
                      <span
                        aria-hidden
                        className={[
                          "absolute inset-y-[8px] left-0 w-[2.5px] rounded-full",
                          group.danger ? "bg-down" : "bg-accent-bar",
                        ].join(" ")}
                      />
                    ) : null}
                    <item.icon />
                    <span className="truncate">{item.label}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
          <div className="mt-auto space-y-1.5 border-t border-border-soft px-2.5 pt-3">
            <div className="text-body font-semibold uppercase tracking-[0.2em] text-faint">
              Session
            </div>
            <div className="flex items-center gap-2">
              <span className="relative flex size-2 shrink-0">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-up opacity-75" />
                <span className="relative inline-flex size-2 rounded-full bg-up" />
              </span>
              <span className="truncate text-xs text-muted">
                ICICI Breeze · connected
              </span>
            </div>
          </div>
        </nav>

        <div className="min-w-0 flex-1">
          <ActiveScreen />
        </div>
      </div>
    </div>
  );
}

function IconBase({ children }: { children: ReactNode }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
      aria-hidden
    >
      {children}
    </svg>
  );
}

function LockIcon() {
  return (
    <IconBase>
      <rect x="3" y="11" width="18" height="10" rx="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </IconBase>
  );
}

function GaugeIcon() {
  return (
    <IconBase>
      <path d="M12 14v-3" />
      <path d="M4.6 19a9 9 0 1 1 14.8 0" />
      <path d="M12 5v0" />
    </IconBase>
  );
}

function ActivityIcon() {
  return (
    <IconBase>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </IconBase>
  );
}

function DatabaseIcon() {
  return (
    <IconBase>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
      <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
    </IconBase>
  );
}

function CalendarIcon() {
  return (
    <IconBase>
      <rect x="3" y="4" width="18" height="17" rx="2" />
      <path d="M3 9h18" />
      <path d="M8 2v4M16 2v4" />
    </IconBase>
  );
}

function DocIcon() {
  return (
    <IconBase>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
      <path d="M14 2v6h6" />
      <path d="M9 13h6M9 17h6" />
    </IconBase>
  );
}

function WarningIcon() {
  return (
    <IconBase>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4M12 17h.01" />
    </IconBase>
  );
}

function TrashIcon() {
  return (
    <IconBase>
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6M14 11v6" />
    </IconBase>
  );
}
