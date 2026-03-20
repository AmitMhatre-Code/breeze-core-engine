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
  { href: "/orders", label: "Orders" },
  { href: "/strategies", label: "Strategies" },
  { href: "/settings", label: "Settings" },
];

export function AppShell({ children }: { children: ReactNode }) {
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

  return (
    <div className="flex min-h-screen bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-50">
      <aside className="hidden w-64 border-r border-zinc-200 bg-white/90 dark:border-zinc-800 dark:bg-zinc-950/80 md:flex md:flex-col">
        <div className="mb-6 px-2">
          <div className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
            ICICI Breeze
          </div>
          <div className="app-text-muted">Local trading workspace</div>
        </div>
        <nav className="space-y-1 text-sm">
          {navItems.map((item) => {
            const active =
              item.href === "/dashboard"
                ? pathname === "/" || pathname.startsWith("/dashboard")
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={[
                  "flex items-center gap-2 rounded-lg px-3 py-2 transition",
                  active
                    ? "bg-zinc-200 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50"
                    : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100",
                ].join(" ")}
              >
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="flex min-h-screen flex-1 flex-col">
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
                  className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100"
                  title={displayName ?? undefined}
                >
                  {displayName ?? "—"}
                </span>
                <span className="hidden text-zinc-300 dark:text-zinc-600 sm:inline">
                  ·
                </span>
                <span
                  className="shrink-0 text-xs tabular-nums text-zinc-600 dark:text-zinc-400"
                  title="Available margin (cash + limits from ICICI margin API)"
                >
                  Free margin{" "}
                  <span className="font-medium text-zinc-900 dark:text-zinc-100">
                    {freeMargin != null
                      ? formatIndianMoneyCompact(freeMargin)
                      : "—"}
                  </span>
                </span>
              </>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2 sm:gap-3">
            <span className="hidden text-xs text-zinc-500 lg:inline">
              ICICI
            </span>
            <span
              className="hidden h-2 w-2 rounded-full bg-emerald-500 sm:inline"
              title="Session active"
              aria-hidden
            />
            <ThemeToggle />
          </div>
        </header>
        <main className="flex-1 bg-gradient-to-b from-zinc-100 via-zinc-50 to-white dark:from-zinc-950 dark:via-zinc-950 dark:to-zinc-900/80 px-4 py-4 md:px-6 md:py-6">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
