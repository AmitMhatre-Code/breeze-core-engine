"use client";

import Image from "next/image";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import breezeMark from "@/app/android-chrome-192x192.png";
import { ChangelogDialog } from "@/components/changelog/ChangelogDialog";
import { IciciHandoffHelpLinks } from "@/components/auth/IciciHandoffHelpLinks";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";
import { formatAppVersionLabel } from "@/lib/app-version";
import { getLatestRelease } from "@/lib/changelog";
import { preloadLoginDisclosure } from "@/lib/login-disclosure-preload";

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
    void preloadLoginDisclosure();
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
          className="inline-flex min-h-9 shrink-0 items-center justify-center truncate rounded-md px-1.5 font-mono text-xs font-semibold text-accent-strong underline-offset-2 hover:underline"
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
      <div className="w-full max-w-[400px] rounded-2xl border border-border bg-panel p-8">
        <div className="mb-6 flex gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center">
            <Image
              src={breezeMark}
              alt="Breeze"
              width={44}
              height={44}
              className="size-11 rounded-lg object-contain"
              priority
            />
          </div>
          <div className="min-w-0 space-y-2">
            <h1 className="text-xl font-bold tracking-tight text-accent-strong">
              Breeze Modern
            </h1>
            <p className="text-xs text-muted">
              Sign in with your ICICI user id and app password, then complete ICICI Direct login.
            </p>
          </div>
        </div>
        {banner && (
          <div
            className={[
              "mb-4 rounded-lg border px-3 py-2 text-xs",
              tone === "ok" && "border-up/30 bg-up-tint text-up",
              tone === "warn" && "border-amber-accent/30 bg-amber-tint text-amber-accent",
              tone === "err" && "border-down/30 bg-down-tint text-down",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {banner}
          </div>
        )}
        <div className="space-y-3">
          <p className="text-center text-heading text-muted">
            New user?{" "}
            <a href="/register" className="app-link">
              Register
            </a>
          </p>
          <p className="text-center text-heading text-muted">
            Wrong credentials?{" "}
            <a href="/register/correct" className="app-link">
              Update credentials
            </a>
          </p>
          <p className="text-center text-heading text-muted">
            Forgot password?{" "}
            <a href="/register/forgot-password" className="app-link">
              Reset via ICICI
            </a>
          </p>
        </div>

        <div className="relative my-6">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t border-border" />
          </div>
          <div className="relative flex justify-center text-body font-semibold uppercase tracking-wide text-faint">
            <span className="bg-panel px-2">App password</span>
          </div>
        </div>

        {directErr && (
          <p className="mb-3 rounded-lg border border-down/30 bg-down-tint px-3 py-2 text-xs text-down">
            {directErr}
          </p>
        )}
        <form onSubmit={onDirectSubmit} className="space-y-3">
          <input
            required
            className="w-full rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3 py-2.5 text-sm text-foreground placeholder:text-faint transition hover:border-accent focus:border-accent-strong focus:bg-panel focus:outline-none"
            placeholder="ICICI user id"
            value={directUserId}
            onChange={(e) => setDirectUserId(e.target.value)}
            autoComplete="username"
          />
          <input
            required
            type="password"
            className="w-full rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3 py-2.5 text-sm text-foreground placeholder:text-faint transition hover:border-accent focus:border-accent-strong focus:bg-panel focus:outline-none"
            placeholder="App password"
            value={directPassword}
            onChange={(e) => setDirectPassword(e.target.value)}
            autoComplete="current-password"
          />
          <button
            type="submit"
            disabled={directBusy}
            aria-busy={directBusy}
            className="app-btn-primary w-full rounded-[9px] py-2.5 text-sm"
          >
            <AsyncLabelSpan
              busy={directBusy}
              idleLabel="Continue to ICICI login"
              busyLabel="Signing in…"
            />
          </button>
          <IciciHandoffHelpLinks />
        </form>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background text-muted">
          Loading…
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
