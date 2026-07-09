"use client";

import { useState } from "react";
import Link from "next/link";
import { HelpLink } from "@/components/help/HelpLink";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { apiClient } from "@/lib/api-client";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";

export default function RegisterForgotPasswordPage() {
  const [userId, setUserId] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onStartRecover(e: React.FormEvent) {
    e.preventDefault();
    const uid = userId.trim();
    if (!uid) {
      setErr("Enter your ICICI user id.");
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>("/api/register/recover/start", {
        user_id: uid,
      });
      window.location.href = res.redirect ?? "/auth/icici-redirect";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not start recovery");
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
        <h1 className="text-xl font-semibold tracking-tight">Reset app password</h1>
        <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
          Enter your ICICI user id. We use your saved broker API key from our records to send you to
          ICICI for verification. After that, you will set a new app password only.{" "}
          <HelpLink topicId="forgot-password" className="text-xs">
            More help
          </HelpLink>
        </p>
        {err && (
          <p className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-200">
            {err}
          </p>
        )}
        <form onSubmit={onStartRecover} className="mt-6 space-y-3">
          <input
            required
            className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-950"
            placeholder="ICICI user id"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            autoComplete="username"
          />
          <button
            type="submit"
            disabled={busy}
            aria-busy={busy}
            className="app-btn-primary w-full py-2.5"
          >
            <AsyncLabelSpan
              busy={busy}
              idleLabel="Continue to ICICI verification"
              busyLabel="Starting…"
            />
          </button>
        </form>
        <p className="mt-4 text-center text-heading text-zinc-500 dark:text-zinc-400">
          Know your app password and need to update API key or secret?{" "}
          <Link href="/register/correct" className="app-link">
            Update credentials
          </Link>
        </p>
        <Link href="/login" className="app-link mt-6 block text-center text-xs">
          Back to login
        </Link>
      </div>
    </div>
  );
}
