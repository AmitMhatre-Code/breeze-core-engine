"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiClient } from "@/lib/api-client";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";

export type DeleteAccountWidgetProps = {
  variant: "standalone" | "settings";
  /** When logged in on Settings, pre-fill ICICI / app user id for direct delete. */
  initialDirectUserId?: string;
};

export function DeleteAccountWidget({ variant, initialDirectUserId }: DeleteAccountWidgetProps) {
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [directUserId, setDirectUserId] = useState("");
  const [directPassword, setDirectPassword] = useState("");

  useEffect(() => {
    const id = initialDirectUserId?.trim();
    if (!id) return;
    setDirectUserId((prev) => (prev.trim() ? prev : id));
  }, [initialDirectUserId]);

  async function onDeleteDirect(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>(
        "/api/register/delete",
        {
          user_id: directUserId.trim(),
          password: directPassword,
        },
      );
      window.location.href = res.redirect ?? "/login";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  const shell =
    variant === "standalone"
      ? "flex min-h-screen items-center justify-center bg-zinc-950 px-4 py-10 text-zinc-50"
      : "";
  const card =
    variant === "standalone"
      ? "rounded-lg border border-zinc-800 bg-zinc-900/80 p-8"
      : "rounded-md border border-red-200 bg-red-50/40 p-5 dark:border-red-900/50 dark:bg-red-950/20";
  const titleCls =
    variant === "standalone"
      ? "text-xl font-semibold text-red-300"
      : "text-lg font-semibold text-red-800 dark:text-red-300";
  const mutedCls =
    variant === "standalone" ? "text-xs text-zinc-400" : "text-xs text-zinc-600 dark:text-zinc-400";
  const inputCls =
    variant === "standalone"
      ? "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
      : "w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100";

  const cancelHref = variant === "settings" ? "/settings" : "/settings";

  return (
    <div className={shell || undefined}>
      <div className={variant === "standalone" ? "w-full max-w-md space-y-8" : "space-y-6"}>
        <div className={card}>
          <h2 className={titleCls}>Delete account</h2>
          <p className={`mt-2 ${mutedCls}`}>
            Enter your ICICI user id and app password to permanently remove your account and stored
            broker credentials.
          </p>
          {err && (
            <p
              className={
                variant === "standalone"
                  ? "mt-4 text-sm text-red-300"
                  : "mt-4 text-sm text-red-700 dark:text-red-300"
              }
            >
              {err}
            </p>
          )}
          <form onSubmit={onDeleteDirect} className="mt-6 space-y-3">
            <input
              required
              className={inputCls}
              placeholder="ICICI user id"
              value={directUserId}
              onChange={(e) => setDirectUserId(e.target.value)}
              autoComplete="username"
            />
            <input
              required
              type="password"
              className={inputCls}
              placeholder="App password"
              value={directPassword}
              onChange={(e) => setDirectPassword(e.target.value)}
              autoComplete="current-password"
            />
            <button
              type="submit"
              disabled={busy}
              aria-busy={busy}
              className={
                variant === "standalone"
                  ? "w-full rounded-md border border-red-800 bg-red-950/40 py-2 text-sm font-medium text-red-200 hover:bg-red-950/60 disabled:cursor-not-allowed disabled:border-red-900 disabled:bg-red-900 disabled:text-red-400"
                  : "w-full rounded-lg border border-red-800 bg-red-600 py-2.5 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-800 disabled:text-white dark:border-red-700 dark:bg-red-900 dark:hover:bg-red-800 dark:disabled:bg-red-950"
              }
            >
              <AsyncLabelSpan busy={busy} idleLabel="Delete account" busyLabel="Working…" />
            </button>
          </form>
        </div>

        <Link
          href={cancelHref}
          className={
            variant === "standalone"
              ? "block text-center text-xs text-zinc-500"
              : "inline-block text-sm text-zinc-600 underline-offset-2 hover:underline dark:text-zinc-400"
          }
        >
          Cancel
        </Link>
      </div>
    </div>
  );
}
