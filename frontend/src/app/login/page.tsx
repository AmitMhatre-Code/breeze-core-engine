"use client";

import Image from "next/image";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import breezeMark from "@/app/android-chrome-192x192.png";
import { ChangelogDialog } from "@/components/changelog/ChangelogDialog";
import { IciciRegistrationGuideLink } from "@/components/auth/IciciRegistrationGuideLink";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";
import { formatAppVersionLabel } from "@/lib/app-version";
import { getLatestRelease } from "@/lib/changelog";

function LoginContent() {
  const sp = useSearchParams();
  const registered = sp.get("registered");
  const corrected = sp.get("corrected");
  const recovered = sp.get("recovered");
  const deleted = sp.get("deleted");
  const err = sp.get("error");
  const reason = sp.get("reason");

  const [directUserId, setDirectUserId] = useState("");
  const [directPassword, setDirectPassword] = useState("");
  const [directErr, setDirectErr] = useState<string | null>(null);
  const [directBusy, setDirectBusy] = useState(false);
  const [changelogOpen, setChangelogOpen] = useState(false);
  const latestVersionLabel = formatAppVersionLabel(getLatestRelease()?.version);

  let banner: string | null = null;
  let tone: "ok" | "warn" | "err" = "ok";
  if (registered) {
    banner = "Registration complete. Continue to sign in.";
    tone = "ok";
  } else if (corrected) {
    banner = "Credentials updated. Please sign in again.";
    tone = "ok";
  } else if (recovered) {
    banner = "App password updated. Please sign in.";
    tone = "ok";
  } else if (deleted) {
    banner = "Account removed. You can register again.";
    tone = "ok";
  } else if (err === "no_account") {
    banner = "No account found. Register first.";
    tone = "warn";
  } else if (err === "no_credentials") {
    banner = "No broker credentials on file. Register or update settings.";
    tone = "warn";
  } else if (err === "oauth_invalid" || err?.startsWith("oauth_")) {
    banner = "Sign-in bootstrap invalid or expired. Try again.";
    tone = "err";
  } else if (reason === "session") {
    banner =
      "Please sign in again. Your session ended because your authentication token was missing, invalid, or expired.";
    tone = "err";
  }

  async function onDirectSubmit(e: React.FormEvent) {
    e.preventDefault();
    setDirectErr(null);
    setDirectBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>("/auth/direct-login", {
        user_id: directUserId.trim(),
        password: directPassword,
      });
      window.location.href = res.redirect ?? "/auth/icici-redirect";
    } catch (e) {
      setDirectErr(e instanceof Error ? e.message : "Sign-in failed");
    } finally {
      setDirectBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-background py-8 ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] pb-[max(2rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))] text-foreground">
      <div className="absolute end-[max(1rem,env(safe-area-inset-right))] top-[max(1rem,env(safe-area-inset-top))] flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setChangelogOpen(true)}
          className="inline-flex min-h-9 shrink-0 items-center justify-center truncate rounded-md px-1.5 text-xs font-medium text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
          aria-haspopup="dialog"
          aria-label="Open changelog"
        >
          {latestVersionLabel || "Version"}
        </button>
        <ThemeToggle />
      </div>
      <ChangelogDialog
        open={changelogOpen}
        onClose={() => setChangelogOpen(false)}
      />
      <div className="w-full max-w-md rounded-lg border border-zinc-200 bg-white/90 p-8 shadow-lg dark:border-zinc-800 dark:bg-zinc-900/80 dark:shadow-xl dark:shadow-black/40">
        <div className="mb-6 flex gap-3">
          <div className="flex h-16 w-16 shrink-0 items-center justify-center">
            <Image
              src={breezeMark}
              alt="Breeze"
              width={48}
              height={48}
              className="h-22 w-22 object-contain"
              priority
            />
          </div>
          <div className="min-w-0 space-y-2">
            <h1 className="text-xl font-semibold tracking-tight text-sky-500 dark:text-sky-500">
              Breeze Core Engine
            </h1>
            <p className="text-xs text-zinc-600 dark:text-zinc-400">
              Sign in with your ICICI user id and app password, then complete ICICI Direct login.
            </p>
          </div>
        </div>
        {banner && (
          <div
            className={[
              "mb-4 rounded-lg border px-3 py-2 text-xs",
              tone === "ok" &&
                "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-950/20 dark:text-emerald-200",
              tone === "warn" &&
                "border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900/50 dark:bg-amber-950/20 dark:text-amber-200",
              tone === "err" &&
                "border-red-200 bg-red-50 text-red-900 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-200",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {banner}
          </div>
        )}
        <div className="space-y-3">
          <p className="text-center text-[11px] text-zinc-500">
            New user?{" "}
            <a href="/register" className="app-link">
              Register
            </a>
            {" · "}
            <IciciRegistrationGuideLink />
          </p>
          <p className="text-center text-[11px] text-zinc-500">
            Wrong credentials?{" "}
            <a href="/register/correct" className="app-link">
              Update credentials
            </a>
          </p>
          <p className="text-center text-[11px] text-zinc-500">
            Forgot password?{" "}
            <a href="/register/forgot-password" className="app-link">
              Reset via ICICI
            </a>
          </p>
        </div>

        <div className="relative my-6">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t border-zinc-200 dark:border-zinc-700" />
          </div>
          <div className="relative flex justify-center text-[10px] uppercase tracking-wide text-zinc-500">
            <span className="bg-white px-2 dark:bg-zinc-900/80">App password</span>
          </div>
        </div>

        {directErr && (
          <p className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-200">
            {directErr}
          </p>
        )}
        <form onSubmit={onDirectSubmit} className="space-y-3">
          <input
            required
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="ICICI user id"
            value={directUserId}
            onChange={(e) => setDirectUserId(e.target.value)}
            autoComplete="username"
          />
          <input
            required
            type="password"
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="App password"
            value={directPassword}
            onChange={(e) => setDirectPassword(e.target.value)}
            autoComplete="current-password"
          />
          <button
            type="submit"
            disabled={directBusy}
            aria-busy={directBusy}
            className="app-btn-primary w-full py-2.5 text-sm"
          >
            <AsyncLabelSpan
              busy={directBusy}
              idleLabel="Continue to ICICI login"
              busyLabel="Signing in…"
            />
          </button>
        </form>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background text-zinc-500 dark:text-zinc-400">
          Loading…
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
