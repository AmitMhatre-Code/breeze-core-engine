"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { apiClient } from "@/lib/api-client";

function LoginContent() {
  const sp = useSearchParams();
  const registered = sp.get("registered");
  const corrected = sp.get("corrected");
  const deleted = sp.get("deleted");
  const err = sp.get("error");

  const [directUserId, setDirectUserId] = useState("");
  const [directPassword, setDirectPassword] = useState("");
  const [directErr, setDirectErr] = useState<string | null>(null);
  const [directBusy, setDirectBusy] = useState(false);

  let banner: string | null = null;
  let tone: "ok" | "warn" | "err" = "ok";
  if (registered) {
    banner = "Registration complete. Continue to sign in.";
    tone = "ok";
  } else if (corrected) {
    banner = "Credentials updated. Please sign in again.";
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
    banner = "Sign-in session invalid or expired. Try again.";
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
    <div className="relative flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white/90 p-8 shadow-lg dark:border-zinc-800 dark:bg-zinc-900/80 dark:shadow-xl dark:shadow-black/40">
        <div className="mb-6 space-y-2">
          <h1 className="text-xl font-semibold tracking-tight text-sky-500 dark:text-sky-500">
            Breeze Web
          </h1>
          <p className="text-xs text-zinc-600 dark:text-zinc-400">
            Sign in with Google or your app password, then complete ICICI Direct login.
          </p>
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
          <GoogleSignInButton href="/auth/google?next=/auth/icici-redirect" />
          <p className="text-center text-[11px] text-zinc-500">
            New user?{" "}
            <a href="/register" className="app-link">
              Register
            </a>
          </p>
          <p className="text-center text-[11px] text-zinc-500">
            Wrong credentials?{" "}
            <a href="/register/correct" className="app-link">
              Update credentials
            </a>
          </p>
        </div>

        <div className="relative my-6">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t border-zinc-200 dark:border-zinc-700" />
          </div>
          <div className="relative flex justify-center text-[10px] uppercase tracking-wide text-zinc-500">
            <span className="bg-white px-2 dark:bg-zinc-900/80">Or app password</span>
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
            className="app-btn-primary w-full py-2.5 text-sm"
          >
            {directBusy ? "Signing in…" : "Continue to ICICI login"}
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
