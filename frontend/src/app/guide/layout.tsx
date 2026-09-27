import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import breezeMark from "@/app/android-chrome-192x192.png";
import { GuideNav } from "@/components/guide/GuideNav";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { formatAppVersionLabel } from "@/lib/app-version";
import { getLatestRelease } from "@/lib/changelog";
import { loadGuideManifest } from "@/lib/guide/content";

export const metadata: Metadata = {
  title: { default: "User Guide · Breeze Modern", template: "%s · Breeze Modern User Guide" },
  description: "How to use Breeze Modern, screen by screen.",
};

/**
 * Public (no sign-in) and deliberately outside AppShell: the guide must load before the reader has
 * an account, and it opens in its own tab beside the app. /guide is listed in public-auth-routes so
 * no provider probes the session or license here.
 */
export default function GuideLayout({ children }: { children: ReactNode }) {
  const manifest = loadGuideManifest();
  const version = formatAppVersionLabel(getLatestRelease()?.version);
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-30 flex h-[57px] items-center justify-between gap-3 border-b border-border bg-panel px-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))]">
        <Link href="/guide" className="flex min-w-0 items-center gap-2.5">
          <Image src={breezeMark} alt="" width={30} height={30} className="size-[30px] shrink-0 rounded-sm" priority />
          <span className="truncate text-sm font-bold tracking-tight">Breeze Modern</span>
          <span className="hidden truncate text-sm text-muted sm:inline">User Guide</span>
        </Link>
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          {version ? (
            <span className="hidden font-mono text-xs text-faint sm:inline" title="The app version this guide describes">
              {version}
            </span>
          ) : null}
          <ThemeToggle />
          <Link href="/dashboard" className="app-btn-outline min-h-9">
            Open the app
          </Link>
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-[1440px] flex-col lg:flex-row">
        <GuideNav parts={manifest.parts} />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
