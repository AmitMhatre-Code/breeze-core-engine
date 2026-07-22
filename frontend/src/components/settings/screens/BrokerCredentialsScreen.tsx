"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { apiClient } from "@/lib/api-client";

type CredData = {
  user_id: string;
  customer: Record<string, unknown>;
  margin: Record<string, unknown>;
};

const inputCls =
  "mt-1.5 w-full rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3 py-2.5 font-mono text-sm text-foreground transition hover:border-accent focus:border-accent-strong focus:bg-panel focus:outline-none";

function LockIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="11" width="18" height="10" rx="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

export function BrokerCredentialsScreen() {
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

  return (
    <div>
      <SettingsScreenHeader
        icon={<LockIcon />}
        title="Broker Credentials"
        description="Update your ICICI API key and secret fragment."
      />
      {q.isLoading ? (
        <div className="text-sm text-muted">Loading…</div>
      ) : q.error || !q.data ? (
        <div className="text-sm text-down">
          {q.error instanceof Error ? q.error.message : "Unable to load"}
        </div>
      ) : (
        <CredentialsFormLoaded
          key={q.data.user_id}
          userId={q.data.user_id}
          apiKey={apiKey}
          secret={secret}
          setApiKey={setApiKey}
          setSecret={setSecret}
          onSubmit={(uid) => m.mutate({ user_id: uid, api_key: apiKey, secret_fragment: secret })}
          busy={m.isPending}
        />
      )}
    </div>
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
  return (
    <section className="app-card max-w-lg space-y-4 p-5">
      <form
        className="space-y-3.5"
        onSubmit={(e) => {
          e.preventDefault();
          props.onSubmit(props.userId);
        }}
      >
        <label className="block text-micro font-semibold uppercase tracking-[.06em] text-faint">
          ICICI User ID
          <input
            className={`${inputCls} cursor-not-allowed border-dashed text-faint`}
            value={props.userId}
            readOnly
          />
        </label>
        <label className="block text-micro font-semibold uppercase tracking-[.06em] text-faint">
          API Key
          <input
            required
            className={inputCls}
            value={props.apiKey}
            onChange={(e) => props.setApiKey(e.target.value)}
          />
        </label>
        <label className="block text-micro font-semibold uppercase tracking-[.06em] text-faint">
          Secret Fragment
          <input
            required
            type="password"
            className={inputCls}
            value={props.secret}
            onChange={(e) => props.setSecret(e.target.value)}
            autoComplete="off"
          />
        </label>
        <button type="submit" disabled={props.busy} aria-busy={props.busy} className="app-btn-primary rounded-[9px] px-5 py-2.5 text-heading">
          <AsyncLabelSpan busy={props.busy} idleLabel="Save" busyLabel="Saving…" />
        </button>
      </form>
    </section>
  );
}
