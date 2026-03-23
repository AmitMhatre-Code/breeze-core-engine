"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";

type CredData = {
  user_id: string;
  customer: Record<string, unknown>;
  margin: Record<string, unknown>;
};

const inputCls =
  "mt-1 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100";

export default function CredentialsSettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["settings", "credentials"],
    queryFn: () => apiClient.get<CredData>("/api/settings/credentials/data"),
  });

  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");

  const m = useMutation({
    mutationFn: (body: { user_id: string; api_key: string; secret_fragment: string }) =>
      apiClient.post<{ ok?: boolean; message?: string; redirect?: string }>(
        "/api/settings/credentials",
        body,
      ),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "credentials"] });
      if (data.redirect === "/logout") {
        window.location.href = "/logout";
        return;
      }
      alert(data.message ?? "Saved");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Save failed"),
  });

  if (q.isLoading) {
    return (
      <AppShell>
        <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</div>
      </AppShell>
    );
  }

  if (q.error || !q.data) {
    return (
      <AppShell>
        <div className="text-sm text-red-700 dark:text-red-300">
          {q.error instanceof Error ? q.error.message : "Unable to load"}
        </div>
      </AppShell>
    );
  }

  return (
    <CredentialsFormLoaded
      key={q.data.user_id}
      userId={q.data.user_id}
      apiKey={apiKey}
      secret={secret}
      setApiKey={setApiKey}
      setSecret={setSecret}
      onSubmit={(uid) =>
        m.mutate({ user_id: uid, api_key: apiKey, secret_fragment: secret })
      }
      busy={m.isPending}
    />
  );
}

function CredentialsFormLoaded(props: {
  userId: string;
  apiKey: string;
  secret: string;
  setApiKey: (v: string) => void;
  setSecret: (v: string) => void;
  onSubmit: (uid: string) => void;
  busy: boolean;
}) {
  const [uid, setUid] = useState(props.userId);

  return (
    <AppShell>
      <section className="app-card max-w-lg space-y-4 p-4">
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>
        <h2 className="app-text-heading">Broker credentials</h2>
        <p className="app-text-muted">Session user: {props.userId}</p>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            props.onSubmit(uid);
          }}
        >
          <label className="block text-xs text-zinc-600 dark:text-zinc-400">
            ICICI user id
            <input
              className={inputCls}
              value={uid}
              onChange={(e) => setUid(e.target.value)}
            />
          </label>
          <label className="block text-xs text-zinc-600 dark:text-zinc-400">
            API key
            <input
              required
              className={inputCls}
              value={props.apiKey}
              onChange={(e) => props.setApiKey(e.target.value)}
            />
          </label>
          <label className="block text-xs text-zinc-600 dark:text-zinc-400">
            Secret fragment
            <input
              required
              className={inputCls}
              value={props.secret}
              onChange={(e) => props.setSecret(e.target.value)}
              autoComplete="off"
            />
          </label>
          <button
            type="submit"
            disabled={props.busy}
            className="app-btn-primary"
          >
            {props.busy ? "Saving…" : "Save"}
          </button>
        </form>
      </section>
    </AppShell>
  );
}
