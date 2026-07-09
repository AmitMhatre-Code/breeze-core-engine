"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { apiClient } from "@/lib/api-client";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";

export default function RegisterCorrectPage() {
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined" && window.location.hash === "#forgot-password") {
      window.location.replace("/register/forgot-password");
    }
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>(
        "/api/register/correct-direct",
        {
          user_id: userId.trim(),
          password,
          api_key: apiKey,
          secret_fragment: secret,
        },
      );
      window.location.href = res.redirect ?? "/login";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update failed");
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
        <h1 className="text-xl font-semibold tracking-tight">Update credentials</h1>
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
          Enter your ICICI user id and app password, then your new API key and secret fragment.
        </p>
        {err && (
          <p className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-200">
            {err}
          </p>
        )}
        <form onSubmit={onSubmit} className="mt-6 space-y-3">
          <input
            required
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="ICICI user id"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            autoComplete="username"
          />
          <input
            required
            type="password"
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="App password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
          <input
            required
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="New API key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <input
            required
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="New secret fragment"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={busy}
            aria-busy={busy}
            className="app-btn-primary w-full py-2.5"
          >
            <AsyncLabelSpan busy={busy} idleLabel="Save" busyLabel="Saving…" />
          </button>
        </form>
        <div className="mt-6 border-t border-zinc-200 pt-4 text-center text-heading text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <p>Forgot your app password?</p>
          <Link href="/register/forgot-password" className="app-link mt-2 inline-block">
            Reset via ICICI (user id only)
          </Link>
        </div>
        <Link href="/login" className="app-link mt-6 block text-center text-xs">
          Back to login
        </Link>
      </div>
    </div>
  );
}
