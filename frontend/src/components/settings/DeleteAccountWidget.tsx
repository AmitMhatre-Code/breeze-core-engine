"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiClient } from "@/lib/api-client";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";

type Session = {
  google_authenticated: boolean;
  has_account?: boolean | null;
  user_id?: string | null;
  direct_delete_available?: boolean;
};

export type DeleteAccountWidgetProps = {
  /** Where Google OAuth returns after consent (must match a route that hosts this widget). */
  oauthNextPath: string;
  variant: "standalone" | "settings";
  /** When logged in on Settings, pre-fill ICICI / app user id for direct delete. */
  initialDirectUserId?: string;
};

export function DeleteAccountWidget({
  oauthNextPath,
  variant,
  initialDirectUserId,
}: DeleteAccountWidgetProps) {
  const [session, setSession] = useState<Session | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [directUserId, setDirectUserId] = useState("");
  const [directPassword, setDirectPassword] = useState("");

  useEffect(() => {
    void apiClient
      .get<Session>("/api/register/delete/session")
      .then(setSession)
      .catch(() => setSession({ google_authenticated: false }));
  }, []);

  useEffect(() => {
    const id = initialDirectUserId?.trim();
    if (!id) return;
    setDirectUserId((prev) => (prev.trim() ? prev : id));
  }, [initialDirectUserId]);

  const googleHref = `/auth/google?next=${encodeURIComponent(oauthNextPath)}`;

  async function onDeleteGoogle() {
    setErr(null);
    setBusy(true);
    try {
      const res = await apiClient.post<{ redirect?: string }>(
        "/api/register/delete",
        {},
      );
      window.location.href = res.redirect ?? "/login";
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

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
  const subTitleCls =
    variant === "standalone"
      ? "text-lg font-semibold text-red-300"
      : "text-base font-semibold text-red-800 dark:text-red-300";
  const bodyCls =
    variant === "standalone" ? "text-sm text-zinc-400" : "text-sm text-zinc-600 dark:text-zinc-400";
  const mutedCls =
    variant === "standalone" ? "text-xs text-zinc-400" : "text-xs text-zinc-600 dark:text-zinc-400";
  const inputCls =
    variant === "standalone"
      ? "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm"
      : "w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100";
  const dividerBorder = variant === "standalone" ? "border-zinc-700" : "border-zinc-200 dark:border-zinc-700";
  const dividerBg = variant === "standalone" ? "bg-zinc-900/80" : "bg-red-50/40 dark:bg-red-950/20";

  if (!session) {
    if (variant === "settings") {
      return (
        <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</div>
      );
    }
    return (
      <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-400">
        Loading…
      </div>
    );
  }

  const showGoogleDelete =
    session.google_authenticated && session.has_account === true;
  const showDirectSection = session.direct_delete_available !== false;

  const cancelHref = variant === "settings" ? "/settings" : "/settings";

  return (
    <div className={shell || undefined}>
      <div className={variant === "standalone" ? "w-full max-w-md space-y-8" : "space-y-6"}>
        {showGoogleDelete && (
          <div className={card}>
            <h2 className={titleCls}>Delete account (Google-linked)</h2>
            <p className={`mt-2 ${bodyCls}`}>
              Permanently remove your app account and stored broker credentials for{" "}
              <span
                className={
                  variant === "standalone" ? "text-zinc-200" : "font-medium text-zinc-900 dark:text-zinc-100"
                }
              >
                {session.user_id}
              </span>
              . You will need to register again to use the app.
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
            <button
              type="button"
              disabled={busy}
              aria-busy={busy}
              onClick={onDeleteGoogle}
              className={
                variant === "standalone"
                  ? "mt-6 w-full rounded-md border border-red-800 bg-red-950/40 py-2 text-sm font-medium text-red-200 hover:bg-red-950/60 disabled:cursor-not-allowed disabled:border-red-900 disabled:bg-red-900 disabled:text-red-400"
                  : "mt-4 w-full rounded-lg border border-red-800 bg-red-600 py-2.5 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-800 disabled:text-white dark:border-red-700 dark:bg-red-900 dark:hover:bg-red-800 dark:disabled:bg-red-950"
              }
            >
              <AsyncLabelSpan
                busy={busy}
                idleLabel="Confirm delete account"
                busyLabel="Working…"
              />
            </button>
          </div>
        )}

        {session.google_authenticated && session.has_account === false && (
          <p className={`text-center text-sm ${bodyCls}`}>
            No ICICI account linked to this Google sign-in. You can still delete a direct
            account below.
          </p>
        )}

        {(!session.google_authenticated || session.has_account === false) && (
          <div className={card}>
            <h2 className={subTitleCls}>
              {session.google_authenticated
                ? "Delete direct account"
                : "Delete account"}
            </h2>
            <p className={`mt-2 ${mutedCls}`}>
              {!session.google_authenticated
                ? "Google-linked: sign in with Google to verify ownership, then confirm delete above. Direct: enter your ICICI user id and app password below."
                : "Enter your ICICI user id and app password to remove a direct (non-Google) account."}
            </p>
            {!session.google_authenticated && (
              <div className="mt-4">
                <button
                  type="button"
                  onClick={() => {
                    window.location.href = googleHref;
                  }}
                  className={
                    variant === "standalone"
                      ? "app-btn-primary w-full py-2.5 text-sm"
                      : "app-btn-primary w-full py-2.5 text-sm"
                  }
                >
                  Sign in with Google to delete Google-linked account
                </button>
              </div>
            )}
            {showDirectSection && (
              <>
                <div className="relative my-6">
                  <div className="absolute inset-0 flex items-center">
                    <span className={`w-full border-t ${dividerBorder}`} />
                  </div>
                  <div className="relative flex justify-center text-[10px] uppercase text-zinc-500">
                    <span className={`px-2 ${dividerBg}`}>App password account</span>
                  </div>
                </div>
                {err && !showGoogleDelete && (
                  <p
                    className={
                      variant === "standalone"
                        ? "mb-3 text-sm text-red-300"
                        : "mb-3 text-sm text-red-700 dark:text-red-300"
                    }
                  >
                    {err}
                  </p>
                )}
                <form onSubmit={onDeleteDirect} className="space-y-3">
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
                        : "w-full rounded-lg border border-red-800 py-2 text-sm font-medium text-red-900 hover:bg-red-100 disabled:cursor-not-allowed disabled:bg-red-50 disabled:text-red-400 dark:border-red-700 dark:text-red-200 dark:hover:bg-red-950/40 dark:disabled:bg-red-950 dark:disabled:text-red-500"
                    }
                  >
                    <AsyncLabelSpan
                      busy={busy}
                      idleLabel="Delete direct account"
                      busyLabel="Working…"
                    />
                  </button>
                </form>
              </>
            )}
          </div>
        )}

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
