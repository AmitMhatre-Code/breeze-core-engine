"use client";

import { useState } from "react";
import Link from "next/link";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { apiClient } from "@/lib/api-client";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";

export default function RegisterRecoverCompletePage() {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>(
        "/api/register/recover/complete",
        {
          password,
        },
      );
      window.location.href = res.redirect ?? "/login";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-background px-4 py-8 ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] pb-[max(2rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))] text-foreground">
      <div className="absolute end-[max(1rem,env(safe-area-inset-right))] top-[max(1rem,env(safe-area-inset-top))]">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-md rounded-lg border border-zinc-200 bg-white/90 p-8 shadow-lg dark:border-zinc-800 dark:bg-zinc-900/80 dark:shadow-xl dark:shadow-black/40">
        <h1 className="text-xl font-semibold tracking-tight">Set new app password</h1>
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
          ICICI verification succeeded using your saved broker credentials. Choose a new app
          password (min 8 characters). Your API key and secret on file are unchanged.
        </p>
        {err && (
          <p className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-200">
            {err}
          </p>
        )}
        <form onSubmit={onSubmit} className="mt-6 space-y-3">
          <input
            required
            minLength={8}
            type="password"
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="New app password (min 8 characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
          />
          <button
            type="submit"
            disabled={busy}
            aria-busy={busy}
            className="app-btn-primary w-full py-2.5"
          >
            <AsyncLabelSpan busy={busy} idleLabel="Save and finish" busyLabel="Saving…" />
          </button>
        </form>
        <Link href="/login" className="app-link mt-6 block text-center text-xs">
          Back to login
        </Link>
      </div>
    </div>
  );
}
