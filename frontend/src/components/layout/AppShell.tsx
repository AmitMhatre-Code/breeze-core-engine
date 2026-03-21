"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { apiClient } from "@/lib/api-client";
import {
  getAvailableMargin,
  getCustomerDisplayName,
  type HomeDataResponse,
} from "@/lib/home-data";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

const navItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/performance", label: "Performance" },
  { href: "/orders", label: "Orders" },
  { href: "/trade-options-chain", label: "Trade Options Chain" },
  { href: "/strategies", label: "Strategies" },
  { href: "/strategy-builder", label: "Strategy Builder" },
  { href: "/settings", label: "Settings" },
];

export function AppShell({
  children,
  contentWidth = "default",
}: {
  children: ReactNode;
  /** `wide` uses a larger max width for dense tables (portfolio, dashboard). */
  contentWidth?: "default" | "wide";
}) {
  const pathname = usePathname();

  const homeQ = useQuery({
    queryKey: ["home", "data"],
    queryFn: () => apiClient.get<HomeDataResponse>("/home/data"),
    staleTime: 30_000,
  });

  const displayName = useMemo(
    () => getCustomerDisplayName(homeQ.data?.customer),
    [homeQ.data?.customer],
  );
  const freeMargin = useMemo(
    () => getAvailableMargin(homeQ.data?.margin),
    [homeQ.data?.margin],
  );

  const homeDataReady = Boolean(
    !homeQ.isLoading && !homeQ.isError && homeQ.data,
  );
  // Backend may omit keys (older server or cached payload); `undefined != null` is false in JS, so coalesce.
  const apiCallsToday = homeQ.data?.api_calls_today ?? 0;
  const apiCallsLimit = homeQ.data?.api_calls_limit ?? 5000;

  const freeMarginDisplay = useMemo(() => {
    if (freeMargin == null || !Number.isFinite(freeMargin)) return null;
    if (freeMargin < 0) {
      return {
        text: `(${formatIndianMoneyCompact(Math.abs(freeMargin))})`,
        tone: "negative" as const,
      };
    }
    return {
      text: formatIndianMoneyCompact(freeMargin),
      tone: "positive" as const,
    };
  }, [freeMargin]);

  return (
    <div className="flex min-h-screen bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-50">
      <aside className="hidden w-64 border-r border-zinc-200 bg-white/90 dark:border-zinc-800 dark:bg-zinc-950/80 md:flex md:flex-col">
        <div className="mb-6 px-2">
          <div className="text-lg font-semibold tracking-tight text-sky-500 dark:text-sky-500">
            Breeze Web
          </div>
          <div className="app-text-muted">Enabled by ICICI Direct Breeze API</div>
        </div>
        <nav className="space-y-1 text-sm">
          {navItems.map((item) => {
            const active =
              item.href === "/dashboard"
                ? pathname === "/" || pathname.startsWith("/dashboard")
                : item.href === "/strategies"
                  ? pathname === "/strategies"
                  : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={[
                  "flex items-center gap-2 rounded-lg px-3 py-2 transition",
                  active
                    ? "bg-sky-100 text-sky-950 dark:bg-sky-950/45 dark:text-sky-50"
                    : "text-zinc-600 hover:bg-sky-50 hover:text-sky-900 dark:text-zinc-400 dark:hover:bg-sky-950/35 dark:hover:text-sky-100",
                ].join(" ")}
              >
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="flex h-14 min-h-14 items-center justify-between gap-3 border-b border-zinc-200 bg-white/95 px-3 dark:border-zinc-800 dark:bg-zinc-950/90 sm:px-4">
          <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-3">
            <span className="hidden truncate text-sm text-zinc-600 dark:text-zinc-400 md:inline">
              Trading dashboard
            </span>
            {homeQ.isLoading ? (
              <span className="text-xs text-zinc-500">Loading account…</span>
            ) : homeQ.isError ? (
              <span className="truncate text-xs text-amber-700 dark:text-amber-200/90">
                Account info unavailable
              </span>
            ) : (
              <>
                <span
                  className="truncate text-sm font-medium text-emerald-600 dark:text-emerald-400"
                  title={displayName ?? undefined}
                >
                  {displayName ?? "—"}
                </span>
                <span className="hidden text-zinc-300 dark:text-zinc-600 sm:inline">
                  ·
                </span>
                <span
                  className="flex min-w-0 shrink-0 items-baseline gap-1"
                  title="Available margin (cash + limits from ICICI margin API)"
                >
                  <span className="hidden truncate text-sm text-zinc-600 dark:text-zinc-400 md:inline">
                    Free margin
                  </span>
                  <span
                    className={[
                      "truncate text-sm font-medium tabular-nums",
                      freeMarginDisplay?.tone === "negative"
                        ? "text-red-600 dark:text-red-400"
                        : freeMarginDisplay?.tone === "positive"
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-zinc-900 dark:text-zinc-100",
                    ].join(" ")}
                  >
                    {freeMarginDisplay?.text ?? "—"}
                  </span>
                </span>
              </>
            )}
          </div>
          <div className="flex min-w-0 shrink-0 items-center gap-2 sm:gap-3">
            {homeDataReady && (
              <span
                className="min-w-0 max-w-[9rem] shrink-0 whitespace-nowrap text-[10px] font-normal tabular-nums leading-tight text-zinc-400 dark:text-zinc-500 sm:max-w-none sm:text-[11px]"
                title="Breeze REST calls from this app today (IST calendar day, ICICI daily cap 5,000)"
              >
                <span className="hidden sm:inline">API calls </span>
                <span>
                  {apiCallsToday.toLocaleString("en-IN")} /{" "}
                  {apiCallsLimit.toLocaleString("en-IN")}
                </span>
              </span>
            )}
            <span className="hidden text-[10px] font-normal text-zinc-400 dark:text-zinc-500 lg:inline">
              ICICI
            </span>
            <span
              className="hidden h-2 w-2 rounded-full bg-sky-500 sm:inline"
              title="Session active"
              aria-hidden
            />
            <ThemeToggle />
            <Link
              href="/logout"
              title="Log out"
              aria-label="Log out"
              className="inline-flex rounded-lg border border-zinc-200 bg-zinc-100 p-1.5 text-zinc-700 transition hover:bg-zinc-200 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
            >
              <LogOutIcon />
            </Link>            
          </div>
        </header>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-gradient-to-b from-zinc-100 via-zinc-50 to-white dark:from-zinc-950 dark:via-zinc-950 dark:to-zinc-900/80 px-4 py-4 md:px-6 md:py-6">
          <div
            className={[
              "mx-auto w-full min-w-0",
              contentWidth === "wide"
                ? "max-w-[min(100%,100rem)]"
                : "max-w-6xl",
            ].join(" ")}
          >
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

function LogOutIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}
