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
import { ApiUsageWarningDialog } from "@/components/api-usage/ApiUsageWarningDialog";
import { ChangelogDialog } from "@/components/changelog/ChangelogDialog";
import { useLicenseRestrictions } from "@/components/license/LicenseRestrictionProvider";
import { LicenseStatusBanner } from "@/components/license/LicenseStatusBanner";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { NewFeatureBadge } from "@/components/ui/NewFeatureBadge";
import { Modal } from "@/components/ui/Modal";
import breezeMark from "@/app/android-chrome-192x192.png";
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
  {
    href: "/basket-order",
    label: "Basket Order",
    icon: BasketOrderIcon,
    showNewBadge: true,
  },
  { href: "/strategy-builder", label: "Strategy Builder", icon: StrategyIcon },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

function navItemActive(pathname: string, href: string) {
  if (href === "/dashboard") {
    return pathname === "/" || pathname.startsWith("/dashboard");
  }
  return pathname === href || pathname.startsWith(`${href}/`);
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
  const [apiUsageWarnDismissed, setApiUsageWarnDismissed] = useState(false);
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

  const { licenseStatus, contactSalesMailto } = useLicenseRestrictions();

  const homeQ = useQuery({
    queryKey: ["home", "data"],
    queryFn: () => apiClient.get<HomeDataResponse>("/home/data"),
    staleTime: 30_000,
    enabled: pathname !== "/dashboard" && pathname !== "/",
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
  const apiUsageBand = homeQ.data?.api_usage_band ?? "green";
  const apiUsageWarning = homeQ.data?.api_usage_warning ?? null;

  const apiUsageWarnStorageKey = useMemo(() => {
    const istDay = new Date().toLocaleDateString("en-CA", {
      timeZone: "Asia/Kolkata",
    });
    return `apiUsageWarn:${istDay}`;
  }, []);

  useEffect(() => {
    if (!apiUsageWarning) return;
    try {
      if (sessionStorage.getItem(apiUsageWarnStorageKey) === "1") {
        setApiUsageWarnDismissed(true);
      } else {
        setApiUsageWarnDismissed(false);
      }
    } catch {
      setApiUsageWarnDismissed(false);
    }
  }, [apiUsageWarning, apiUsageWarnStorageKey]);

  const showApiUsageWarning =
    Boolean(apiUsageWarning) && !apiUsageWarnDismissed;

  const dismissApiUsageWarning = useCallback(() => {
    setApiUsageWarnDismissed(true);
    try {
      sessionStorage.setItem(apiUsageWarnStorageKey, "1");
    } catch {
      /* ignore */
    }
  }, [apiUsageWarnStorageKey]);

  const apiCounterClass =
    apiUsageBand === "red"
      ? "text-down"
      : apiUsageBand === "amber"
        ? "text-amber-accent"
        : "text-faint";

  const freeMarginDisplay = useMemo(() => {
    if (freeMargin == null || !Number.isFinite(freeMargin)) return null;
    return {
      text: formatIndianMoneyCompact(freeMargin, { shortSuffix: true }),
      className: moneyToneClass(freeMargin),
    };
  }, [freeMargin]);
  const latestVersionLabel = formatAppVersionLabel(getLatestRelease()?.version);

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[300] focus:rounded-md focus:border focus:border-border focus:bg-panel focus:px-4 focus:py-2.5 focus:text-sm focus:font-medium focus:text-accent-strong focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-accent/45"
      >
        Skip to main content
      </a>
      <aside className="hidden w-[236px] flex-col border-r border-border bg-panel md:flex">
        <div className="mb-4 flex items-center gap-2.5 px-3 pt-1">
          <BrandMark />
          <BrandWordmark />
        </div>
        <nav className="space-y-0.5 px-2 text-sm" aria-label="Primary">
          {navItems.map((item) => {
            const active = navItemActive(pathname, item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-label={
                  item.showNewBadge ? "Strategy Builder, new" : undefined
                }
                className={[
                  "relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] transition",
                  active
                    ? "bg-accent-tint font-semibold text-foreground [&_svg]:text-accent-strong"
                    : "text-muted hover:bg-panel2 hover:text-foreground",
                ].join(" ")}
              >
                {active ? (
                  <span
                    aria-hidden
                    className="absolute inset-y-[9px] left-0 w-[2.5px] rounded-full bg-accent-bar"
                  />
                ) : null}
                <Icon />
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate">{item.label}</span>
                  {item.showNewBadge ? <NewFeatureBadge /> : null}
                </span>
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto space-y-1.5 border-t border-border-soft px-4 py-3">
          <div className="text-[12px] font-semibold uppercase tracking-[0.2em] text-faint">
            Session
          </div>
          <div className="flex items-center gap-2">
            <span className="relative flex size-2 shrink-0">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-up opacity-75" />
              <span className="relative inline-flex size-2 rounded-full bg-up" />
            </span>
            <span className="truncate text-xs text-muted">
              ICICI Breeze · active
            </span>
          </div>
        </div>
      </aside>
      <div className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="flex min-h-[52px] items-center justify-between gap-2 border-b border-border bg-panel px-[max(0.75rem,env(safe-area-inset-left))] py-1 pe-[max(0.75rem,env(safe-area-inset-right))] pt-[max(0.25rem,env(safe-area-inset-top))] sm:gap-3 sm:px-4">
          <button
            type="button"
            ref={menuButtonRef}
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-border-soft hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 md:hidden"
            aria-label="Open menu"
            aria-expanded={mobileNavOpen}
            aria-controls="mobile-app-nav"
            onClick={() => setMobileNavOpen(true)}
          >
            <MenuIcon />
          </button>
          <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-4">
            <span className="hidden truncate text-xs font-medium uppercase tracking-wide text-faint md:inline">
              Trading
            </span>
            {homeQ.isLoading || (homeQ.isFetching && !homeQ.data) ? (
              <span className="text-xs text-muted">Loading account…</span>
            ) : homeQ.isError ? (
              <span className="truncate text-xs text-amber-accent">
                Account info unavailable
              </span>
            ) : (
              <>
                <span
                  className="truncate text-sm font-semibold text-foreground"
                  title={displayName ?? undefined}
                >
                  {displayName ?? "—"}
                </span>
                <span
                  className="flex min-w-0 shrink-0 items-baseline gap-1.5"
                  title="Available margin (cash + limits from ICICI margin API)"
                >
                  <span className="hidden truncate text-xs text-muted md:inline">
                    Free margin
                  </span>
                  <span
                    className={[
                      "truncate font-mono text-sm font-semibold tabular-nums",
                      freeMarginDisplay?.className ?? "text-foreground",
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
                className={[
                  "min-w-0 max-w-[6.25rem] shrink-0 truncate whitespace-nowrap font-mono text-[13px] tabular-nums sm:max-w-none sm:text-xs",
                  apiCounterClass,
                ].join(" ")}
                title="Breeze REST calls from this app today (IST calendar day, ICICI daily cap 5,000)"
              >
                <span className="hidden sm:inline">API </span>
                <span>
                  {apiCallsToday.toLocaleString("en-IN")} /{" "}
                  {apiCallsLimit.toLocaleString("en-IN")}
                </span>
              </span>
            )}
            <span className="hidden text-xs text-faint lg:inline">ICICI</span>
            <span
              className="hidden h-2 w-2 rounded-full bg-up sm:inline"
              title="Session active"
              aria-hidden
            />
            <button
              type="button"
              onClick={() => setChangelogOpen(true)}
              className="inline-flex min-h-11 min-w-9 shrink-0 items-center justify-center truncate rounded-md px-1.5 font-mono text-xs font-semibold text-accent-strong underline-offset-2 hover:underline sm:min-h-9 sm:min-w-0 sm:px-2"
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
              className="inline-flex size-[34px] shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-border-soft hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
            >
              <LogOutIcon />
            </Link>
          </div>
        </header>
        <Modal
          open={mobileNavOpen}
          onClose={closeMobileNav}
          variant="drawer"
          titleId={mobileNavTitleId}
          initialFocusRef={drawerCloseRef}
          className="md:hidden"
          panelClassName="flex w-[min(18rem,85vw)] max-w-[calc(100vw-env(safe-area-inset-left)-0.5rem)] flex-col border-r border-border bg-panel pt-[env(safe-area-inset-top)] ps-[max(0.5rem,env(safe-area-inset-left))] shadow-xl"
        >
            <div
              id="mobile-app-nav"
              className="flex min-h-0 flex-1 flex-col"
            >
              <div className="flex items-center justify-between gap-2 border-b border-border-soft px-3 py-2.5 pe-2">
                <div className="min-w-0">
                  <div
                    id={mobileNavTitleId}
                    className="flex items-center gap-1.5 truncate text-sm font-semibold text-foreground"
                  >
                    Breeze
                    <span className="text-[12px] font-semibold uppercase tracking-[0.2em] text-faint">
                      Terminal
                    </span>
                  </div>
                  <div className="app-text-muted">Menu</div>
                </div>
                <button
                  type="button"
                  ref={drawerCloseRef}
                  className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-border-soft hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
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
                      aria-label={
                        item.showNewBadge ? "Strategy Builder, new" : undefined
                      }
                      className={[
                        "relative flex min-h-11 items-center gap-3 rounded-lg px-3 py-2 transition",
                        active
                          ? "bg-accent-tint font-semibold text-foreground [&_svg]:text-accent-strong"
                          : "text-muted hover:bg-panel2 hover:text-foreground",
                      ].join(" ")}
                      onClick={() => {
                        closeMobileNav();
                        menuButtonRef.current?.focus();
                      }}
                    >
                      {active ? (
                        <span
                          aria-hidden
                          className="absolute inset-y-[9px] left-0 w-[2.5px] rounded-full bg-accent-bar"
                        />
                      ) : null}
                      <Icon />
                      <span className="flex min-w-0 items-center gap-1.5">
                        <span className="truncate">{item.label}</span>
                        {item.showNewBadge ? <NewFeatureBadge /> : null}
                      </span>
                    </Link>
                  );
                })}
              </nav>
            </div>
        </Modal>
        <ChangelogDialog
          open={changelogOpen}
          onClose={() => setChangelogOpen(false)}
        />
        <ApiUsageWarningDialog
          open={showApiUsageWarning}
          message={apiUsageWarning ?? ""}
          onDismiss={dismissApiUsageWarning}
        />
        <LicenseStatusBanner
          status={licenseStatus}
          contactSalesMailto={contactSalesMailto}
        />
        <main
          id="main-content"
          tabIndex={-1}
          className="flex min-h-0 min-w-0 flex-1 flex-col bg-background py-[22px] ps-[max(1.5rem,env(safe-area-inset-left))] pe-[max(1.5rem,env(safe-area-inset-right))] pb-[max(2.75rem,env(safe-area-inset-bottom))]"
        >
          <div
            className={[
              "mx-auto w-full min-w-0",
              contentWidth === "wide" ? "max-w-[min(100%,100rem)]" : "max-w-[1280px]",
            ].join(" ")}
          >
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

function BrandMark() {
  return (
    <Image
      src={breezeMark}
      alt=""
      aria-hidden
      width={34}
      height={34}
      className="size-[34px] shrink-0 rounded-lg object-contain"
      priority
    />
  );
}

function BrandWordmark() {
  return (
    <div className="min-w-0 flex flex-col justify-center leading-tight">
      <div className="text-base font-semibold tracking-tight text-foreground">
        Breeze
      </div>
      <div className="text-[12px] font-semibold uppercase tracking-[0.2em] text-faint">
        Terminal
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

function BasketOrderIcon() {
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
      <path d="M16 10a4 4 0 0 1-8 0" />
      <path d="M3 6h18" />
      <path d="M6 2 3 6v14a2 2 0 0 0 2 2h6" />
      <path d="M19 16v6" />
      <path d="M16 19h6" />
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
