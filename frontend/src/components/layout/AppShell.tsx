"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ReactNode,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import breezeMark from "@/app/android-chrome-192x192.png";
import { ChangelogDialog } from "@/components/changelog/ChangelogDialog";
import { useLicenseRestrictions } from "@/components/license/LicenseRestrictionProvider";
import { LicenseStatusBanner } from "@/components/license/LicenseStatusBanner";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { apiClient } from "@/lib/api-client";
import { formatAppVersionLabel } from "@/lib/app-version";
import { getLatestRelease } from "@/lib/changelog";
import {
  getAvailableMargin,
  getCustomerDisplayName,
  type HomeDataResponse,
} from "@/lib/home-data";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";

// Hidden from nav (route still works): { href: "/trade-options-chain", label: "Trade Options Chain" },
const navItems = [
  { href: "/dashboard", label: "Dashboard", icon: DashboardIcon },
  { href: "/portfolio", label: "Portfolio", icon: PortfolioIcon },
  { href: "/performance", label: "Performance", icon: PerformanceIcon },
  { href: "/orders", label: "Order Book", icon: OrdersIcon },
  { href: "/place-order", label: "Place Order", icon: PlaceOrderIcon },
  { href: "/strategy-builder", label: "Strategy Builder", icon: StrategyIcon },
  {
    href: "/strategy-builder-new",
    label: "Strategy Builder (New)",
    icon: StrategyIcon,
  },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

function navItemActive(pathname: string, href: string) {
  return href === "/dashboard"
    ? pathname === "/" || pathname.startsWith("/dashboard")
    : pathname.startsWith(href);
}

export function AppShell({
  children,
  contentWidth = "default",
}: {
  children: ReactNode;
  /** `wide` uses a larger max width for dense tables (portfolio, dashboard). */
  contentWidth?: "default" | "wide";
}) {
  const pathname = usePathname();
  const [changelogOpen, setChangelogOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [pathnameSeen, setPathnameSeen] = useState(pathname);
  if (pathname !== pathnameSeen) {
    setPathnameSeen(pathname);
    if (mobileNavOpen) setMobileNavOpen(false);
  }
  const mobileNavTitleId = useId();
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);

  const closeMobileNav = useCallback(() => setMobileNavOpen(false), []);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeMobileNav();
        menuButtonRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen, closeMobileNav]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [mobileNavOpen]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const id = window.requestAnimationFrame(() => {
      drawerCloseRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(id);
  }, [mobileNavOpen]);

  const { licenseStatus, contactSalesMailto } = useLicenseRestrictions();

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
    return {
      text: formatIndianMoneyCompact(freeMargin),
      className: moneyToneClass(freeMargin),
    };
  }, [freeMargin]);
  const latestVersionLabel = formatAppVersionLabel(getLatestRelease()?.version);

  return (
    <div className="flex min-h-screen bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-50">
      <aside className="hidden w-60 border-r border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950 md:flex md:flex-col">
        <div className="mb-4 flex items-stretch gap-3 px-3 pt-1">
          <div className="flex w-11 shrink-0 flex-col items-center justify-center">
            <Image
              src={breezeMark}
              alt="Breeze"
              width={192}
              height={192}
              className="max-h-full w-full object-contain object-left"
            />
          </div>
          <div className="min-w-0 flex flex-col justify-center py-0.5">
            <div className="text-base font-semibold tracking-tight text-sky-600 dark:text-sky-500">
              Breeze Modern
            </div>
            <div className="app-text-muted mt-0.5">
              Enabled by Breeze API
            </div>
          </div>
        </div>
        <nav className="space-y-0.5 px-2 text-sm">
          {navItems.map((item) => {
            const active = navItemActive(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={[
                  "flex items-center gap-2 rounded-sm px-2.5 py-2 transition",
                  active
                    ? "bg-sky-100 text-sky-950 dark:bg-sky-950/50 dark:text-sky-50"
                    : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-100",
                ].join(" ")}
              >
                <Icon />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="flex min-h-12 items-center justify-between gap-2 border-b border-zinc-200 bg-white px-[max(0.75rem,env(safe-area-inset-left))] py-1 pe-[max(0.75rem,env(safe-area-inset-right))] pt-[max(0.25rem,env(safe-area-inset-top))] dark:border-zinc-800 dark:bg-zinc-950 sm:gap-3 sm:px-4">
          <button
            type="button"
            ref={menuButtonRef}
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-md border border-zinc-200 bg-zinc-50 text-zinc-700 transition hover:bg-zinc-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 md:hidden dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
            aria-label="Open menu"
            aria-expanded={mobileNavOpen}
            aria-controls="mobile-app-nav"
            onClick={() => setMobileNavOpen(true)}
          >
            <MenuIcon />
          </button>
          <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-4">
            <span className="hidden truncate text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400 md:inline">
              Trading
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
                <span
                  className="flex min-w-0 shrink-0 items-baseline gap-1.5"
                  title="Available margin (cash + limits from ICICI margin API)"
                >
                  <span className="hidden truncate text-xs text-zinc-500 dark:text-zinc-400 md:inline">
                    Free margin
                  </span>
                  <span
                    className={[
                      "truncate text-sm font-semibold tabular-nums",
                      freeMarginDisplay?.className ??
                        "text-zinc-900 dark:text-zinc-100",
                    ].join(" ")}
                  >
                    {freeMarginDisplay?.text ?? "—"}
                  </span>
                </span>
              </>
            )}
          </div>
          <div className="flex min-w-0 shrink-0 items-center gap-1.5 sm:gap-3">
            {homeDataReady && (
              <span
                className="min-w-0 max-w-[6.25rem] shrink-0 truncate whitespace-nowrap text-[11px] tabular-nums text-zinc-500 dark:text-zinc-400 sm:max-w-none sm:text-xs"
                title="Breeze REST calls from this app today (IST calendar day, ICICI daily cap 5,000)"
              >
                <span className="hidden sm:inline">API </span>
                <span>
                  {apiCallsToday.toLocaleString("en-IN")} /{" "}
                  {apiCallsLimit.toLocaleString("en-IN")}
                </span>
              </span>
            )}
            <span className="hidden text-xs text-zinc-400 dark:text-zinc-500 lg:inline">
              ICICI
            </span>
            <span
              className="hidden h-2 w-2 rounded-full bg-sky-500 sm:inline"
              title="Session active"
              aria-hidden
            />
            <button
              type="button"
              onClick={() => setChangelogOpen(true)}
              className="inline-flex min-h-11 min-w-9 shrink-0 items-center justify-center truncate rounded-md px-1.5 text-xs font-medium text-sky-700 underline-offset-2 hover:underline sm:min-h-9 sm:min-w-0 sm:px-2 dark:text-sky-400"
              aria-haspopup="dialog"
              aria-label="Open changelog"
            >
              {latestVersionLabel || "Version"}
            </button>
            <ThemeToggle />
            <Link
              href="/logout"
              title="Log out"
              aria-label="Log out"
              className="inline-flex size-11 shrink-0 items-center justify-center rounded-md border border-zinc-200 bg-zinc-50 text-zinc-600 transition hover:bg-zinc-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
            >
              <LogOutIcon />
            </Link>
          </div>
        </header>
        {mobileNavOpen ? (
          <div
            className="fixed inset-0 z-50 md:hidden"
            role="dialog"
            aria-modal="true"
            aria-labelledby={mobileNavTitleId}
            id="mobile-app-nav"
          >
            <button
              type="button"
              className="absolute inset-0 bg-zinc-950/55 dark:bg-black/60"
              aria-label="Close menu"
              onClick={() => {
                closeMobileNav();
                menuButtonRef.current?.focus();
              }}
            />
            <div className="absolute inset-y-0 left-0 flex w-[min(18rem,85vw)] max-w-[calc(100vw-env(safe-area-inset-left)-0.5rem)] flex-col border-r border-zinc-200 bg-white pt-[env(safe-area-inset-top)] ps-[max(0.5rem,env(safe-area-inset-left))] shadow-xl dark:border-zinc-800 dark:bg-zinc-950">
              <div className="flex items-center justify-between gap-2 border-b border-zinc-200 px-3 py-2.5 pe-2 dark:border-zinc-800">
                <div className="min-w-0">
                  <div
                    id={mobileNavTitleId}
                    className="truncate text-sm font-semibold text-sky-600 dark:text-sky-500"
                  >
                    Breeze Modern
                  </div>
                  <div className="app-text-muted">Menu</div>
                </div>
                <button
                  type="button"
                  ref={drawerCloseRef}
                  className="inline-flex size-11 shrink-0 items-center justify-center rounded-md border border-zinc-200 bg-zinc-50 text-zinc-600 transition hover:bg-zinc-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
                  aria-label="Close menu"
                  onClick={() => {
                    closeMobileNav();
                    menuButtonRef.current?.focus();
                  }}
                >
                  <CloseIcon />
                </button>
              </div>
              <nav
                className="min-h-0 flex-1 space-y-0.5 overflow-y-auto overscroll-contain px-2 py-3 text-sm pe-[env(safe-area-inset-right)] pb-[max(0.75rem,env(safe-area-inset-bottom))]"
                aria-label="Primary"
              >
                {navItems.map((item) => {
                  const active = navItemActive(pathname, item.href);
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={[
                        "flex min-h-11 items-center gap-3 rounded-md px-3 py-2 transition",
                        active
                          ? "bg-sky-100 text-sky-950 dark:bg-sky-950/50 dark:text-sky-50"
                          : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-100",
                      ].join(" ")}
                      onClick={() => {
                        closeMobileNav();
                        menuButtonRef.current?.focus();
                      }}
                    >
                      <Icon />
                      <span>{item.label}</span>
                    </Link>
                  );
                })}
              </nav>
            </div>
          </div>
        ) : null}
        <ChangelogDialog
          open={changelogOpen}
          onClose={() => setChangelogOpen(false)}
        />
        <LicenseStatusBanner
          status={licenseStatus}
          contactSalesMailto={contactSalesMailto}
        />
        <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-zinc-50 py-4 ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] pb-[max(1rem,env(safe-area-inset-bottom))] dark:bg-zinc-950 md:py-5 md:ps-5 md:pe-5">
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

function MenuIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M18 6 6 18" />
      <path d="M6 6l12 12" />
    </svg>
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

function DashboardIcon() {
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
      <rect x="3" y="3" width="7" height="9" rx="1" />
      <rect x="14" y="3" width="7" height="9" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}

function PortfolioIcon() {
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
      <path d="M10 6h4" />
      <path d="M8 6H7a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-1" />
      <path d="M8 6V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v1" />
      <rect x="4" y="13" width="16" height="8" rx="1" />
    </svg>
  );
}

function PerformanceIcon() {
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
      <path d="M3 3v18h18" />
      <path d="M7 14l3-3 3 2 4-6" />
      <path d="M7 14v3h3" />
    </svg>
  );
}

function OrdersIcon() {
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
      <path d="M8 6h13" />
      <path d="M8 12h13" />
      <path d="M8 18h13" />
      <path d="M3 6h.01" />
      <path d="M3 12h.01" />
      <path d="M3 18h.01" />
    </svg>
  );
}

function PlaceOrderIcon() {
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
      <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z" />
      <path d="M3 6h18" />
      <path d="M16 10a4 4 0 0 1-8 0" />
    </svg>
  );
}

function StrategyIcon() {
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
      <circle cx="6" cy="6" r="2" />
      <circle cx="18" cy="6" r="2" />
      <circle cx="12" cy="18" r="2" />
      <path d="M8 8l4 8" />
      <path d="M16 8l-4 8" />
    </svg>
  );
}

function SettingsIcon() {
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
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 8 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82 1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}
