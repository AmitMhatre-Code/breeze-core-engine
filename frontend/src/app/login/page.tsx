"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ThemeToggle } from "@/components/theme/ThemeToggle";

function LoginContent() {
  const sp = useSearchParams();
  const registered = sp.get("registered");
  const corrected = sp.get("corrected");
  const deleted = sp.get("deleted");
  const err = sp.get("error");

  let banner: string | null = null;
  let tone: "ok" | "warn" | "err" = "ok";
  if (registered) {
    banner = "Registration complete. Continue to sign in.";
    tone = "ok";
  } else if (corrected) {
    banner = "Credentials updated. Please sign in again.";
    tone = "ok";
  } else if (deleted) {
    banner = "Registration removed. You can register again.";
    tone = "ok";
  } else if (err === "no_account") {
    banner = "No account for this Google user. Register first.";
    tone = "warn";
  } else if (err === "no_credentials") {
    banner = "No broker credentials on file. Register or update settings.";
    tone = "warn";
  } else if (err === "oauth_invalid" || err?.startsWith("oauth_")) {
    banner = "Sign-in failed. Try again.";
    tone = "err";
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white/90 p-8 shadow-lg dark:border-zinc-800 dark:bg-zinc-900/80 dark:shadow-xl dark:shadow-black/40">
        <div className="mb-6 space-y-2">
          <h1 className="text-xl font-semibold tracking-tight">
            Sign in to ICICI Breeze
          </h1>
          <p className="text-xs text-zinc-600 dark:text-zinc-400">
            Sign in with Google, then complete ICICI broker login.
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
          <a
            href="/auth/google?next=/auth/icici-redirect"
            className="btn-google inline-flex w-full items-center justify-center gap-2 rounded-xl border border-zinc-300 bg-zinc-50 px-4 py-3 text-sm font-medium text-zinc-900 transition hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950/40 dark:text-zinc-100 dark:hover:border-zinc-500"
          >
            Sign in with Google
          </a>
          <p className="text-center text-[11px] text-zinc-500">
            New user?{" "}
            <a
              href="/register"
              className="font-medium text-emerald-700 underline-offset-2 hover:underline dark:text-emerald-400"
            >
              Register with Google
            </a>
          </p>
        </div>
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
