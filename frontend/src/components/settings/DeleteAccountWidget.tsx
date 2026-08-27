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

  const isStandalone = variant === "standalone";

  const shell = isStandalone ? "flex min-h-screen items-center justify-center bg-zinc-950 px-4 py-10 text-zinc-50" : "";
  const card = isStandalone
    ? "rounded-lg border border-zinc-800 bg-zinc-900/80 p-8"
    : "app-card max-w-[480px] space-y-3.5 border-down/35 p-5";
  const mutedCls = isStandalone ? "text-xs text-zinc-400" : "text-xs leading-relaxed text-muted";
  const inputCls = isStandalone
    ? "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
    : "app-input h-10 w-full";
  const labelCls = "block text-micro font-semibold tracking-wide text-faint uppercase";

  const cancelHref = "/settings";

  return (
    <div className={shell || undefined}>
      <div className={isStandalone ? "w-full max-w-md space-y-8" : "space-y-3"}>
        <div className={card}>
          {isStandalone ? (
            <>
              <h2 className="text-xl font-semibold text-red-300">Delete account</h2>
              <p className={`mt-2 ${mutedCls}`}>
                Enter your ICICI user id and app password to permanently remove your account and stored
                broker credentials.
              </p>
            </>
          ) : (
            <p className={mutedCls}>
              Remove your Breeze Modern account and stored broker API credentials. This removes your
              account from Breeze Modern only — it does not affect your ICICI account or release AWS
              resources. To release AWS resources, log in to breeze-ui.com and follow the license
              console instructions. This cannot be undone.
            </p>
          )}
          {err && (
            <p className={isStandalone ? "mt-4 text-sm text-red-300" : "app-alert-error text-xs"}>{err}</p>
          )}
          <form onSubmit={onDeleteDirect} className={isStandalone ? "mt-6 space-y-3" : "space-y-3.5"}>
            <label className={isStandalone ? "block space-y-1.5" : "block space-y-1"}>
              {!isStandalone ? <span className={labelCls}>ICICI User ID</span> : null}
              <input
                required
                className={inputCls}
                placeholder="ICICI user id"
                value={directUserId}
                onChange={(e) => setDirectUserId(e.target.value)}
                autoComplete="username"
              />
            </label>
            <label className={isStandalone ? "block space-y-1.5" : "block space-y-1"}>
              {!isStandalone ? <span className={labelCls}>Confirm with app password</span> : null}
              <input
                required
                type="password"
                className={inputCls}
                placeholder="App password"
                value={directPassword}
                onChange={(e) => setDirectPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              aria-busy={busy}
              className={
                isStandalone
                  ? "w-full rounded-md border border-red-800 bg-red-950/40 py-2 text-sm font-medium text-red-200 hover:bg-red-950/60 disabled:cursor-not-allowed disabled:border-red-900 disabled:bg-red-900 disabled:text-red-400"
                  : "inline-flex items-center justify-center self-start rounded-lg bg-down-btn px-5 py-2.5 text-sm font-bold text-down-ink transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-down/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
              }
            >
              <AsyncLabelSpan busy={busy} idleLabel="Delete account" busyLabel="Working…" />
            </button>
          </form>
        </div>

        <Link
          href={cancelHref}
          className={
            isStandalone
              ? "block text-center text-xs text-zinc-400"
              : "inline-block text-sm text-muted underline-offset-2 hover:underline"
          }
        >
          Cancel
        </Link>
      </div>
    </div>
  );
}
