"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import {
  getAiProviderState,
  getOutlookConfig,
  type OutlookFeedConfig,
  resetOutlookConfig,
  revokeAiProviderSettings,
  saveOutlookConfig,
  saveAiProviderSettings,
  testAiProviderSettings,
} from "@/lib/outlook-api";

const fieldCls =
  "mt-1 h-10 w-full rounded-xl border border-zinc-300/80 bg-white/95 px-3 text-sm text-zinc-900 shadow-sm outline-none transition-all hover:border-zinc-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus:border-blue-400 dark:focus:ring-blue-400/20";

const modelOptions: Record<"gemini" | "openai", string[]> = {
  gemini: ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest"],
  openai: ["gpt-4o-mini", "gpt-4o"],
};

function ProviderRow({
  title,
  subtitle,
  configuredText,
  onClick,
  revokeDisabled,
  onRevoke,
}: {
  title: string;
  subtitle: string;
  configuredText?: string | null;
  onClick: () => void;
  revokeDisabled: boolean;
  onRevoke: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      className={[
        "group w-full rounded-xl px-1 py-2 text-left transition-all",
        "hover:bg-zinc-50 dark:hover:bg-zinc-900/40",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {title}
            </div>
            {configuredText ? (
              <span className="inline-flex shrink-0 items-center rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-200">
                Configured
              </span>
            ) : (
              <span className="inline-flex shrink-0 items-center rounded-full bg-zinc-500/10 px-2 py-0.5 text-[11px] font-medium text-zinc-700 dark:text-zinc-300">
                Not configured
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
            {subtitle}
          </div>
          {configuredText ? (
            <div className="mt-2 text-xs text-zinc-700 dark:text-zinc-300">
              {configuredText}
            </div>
          ) : null}
        </div>

        <button
          type="button"
          disabled={revokeDisabled}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onRevoke();
          }}
          className={[
            "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-all",
            revokeDisabled
              ? "cursor-not-allowed text-zinc-400 dark:text-zinc-600"
              : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-100",
          ].join(" ")}
          aria-label="Revoke key"
          title="Revoke key"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M3 6h18" />
            <path d="M8 6V4h8v2" />
            <path d="M19 6l-1 14H6L5 6" />
            <path d="M10 11v6" />
            <path d="M14 11v6" />
          </svg>
        </button>
      </div>
    </div>
  );
}

function KeyModal({
  open,
  title,
  providerLabel,
  apiKey,
  model,
  modelChoices,
  canSave,
  isTesting,
  isSaving,
  onClose,
  onChangeApiKey,
  onChangeModel,
  onTest,
  onSave,
}: {
  open: boolean;
  title: string;
  providerLabel: string;
  apiKey: string;
  model: string;
  modelChoices: string[];
  canSave: boolean;
  isTesting: boolean;
  isSaving: boolean;
  onClose: () => void;
  onChangeApiKey: (v: string) => void;
  onChangeModel: (v: string) => void;
  onTest: () => void;
  onSave: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/50"
        aria-label="Close modal"
        onClick={onClose}
      />
      <div className="relative w-full max-w-lg rounded-2xl border border-zinc-200 bg-white p-5 shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
              {title}
            </div>
            <div className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              {providerLabel}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center text-zinc-700 transition-all hover:bg-zinc-100 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
            aria-label="Close"
            title="Close"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M18 6 6 18" />
              <path d="M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="mt-4 space-y-3">
          <label className="block text-xs text-zinc-600 dark:text-zinc-400">
            API key
            <input
              required
              className={fieldCls}
              type="password"
              value={apiKey}
              onChange={(e) => onChangeApiKey(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label className="block text-xs text-zinc-600 dark:text-zinc-400">
            Model
            <div className="relative">
              <select
                className={`${fieldCls} appearance-none pr-9`}
                value={model}
                onChange={(e) => onChangeModel(e.target.value)}
              >
                {modelChoices.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                fill="none"
                className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500 dark:text-zinc-400"
              >
                <path
                  d="M6 8l4 4 4-4"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
          </label>

          <div className="flex flex-wrap gap-2 pt-1">
            <button
              type="button"
              disabled={isTesting || !apiKey.trim()}
              className="app-btn-outline"
              onClick={onTest}
            >
              {isTesting ? "Testing…" : "Test key"}
            </button>
            <button
              type="button"
              disabled={isSaving || !canSave}
              className="app-btn-primary"
              onClick={onSave}
            >
              {isSaving ? "Saving…" : "Save Key"}
            </button>
          </div>

          <p className="text-[11px] app-text-muted">
            Save is enabled only after a successful test.
          </p>
        </div>
      </div>
    </div>
  );
}

export default function AiProviderSettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["settings", "ai-provider"],
    queryFn: () => getAiProviderState(),
  });

  const [modalOpen, setModalOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<"gemini" | "openai">(
    "gemini",
  );
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(modelOptions.gemini[0]);
  const [testedOk, setTestedOk] = useState(false);
  const [feeds, setFeeds] = useState<OutlookFeedConfig[]>([]);
  const [promptTemplate, setPromptTemplate] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [editingFeedIdx, setEditingFeedIdx] = useState<number | null>(null);

  const outlookQ = useQuery({
    queryKey: ["settings", "outlook-config"],
    queryFn: () => getOutlookConfig(),
  });

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!outlookQ.data) return;
    setFeeds(outlookQ.data.feeds ?? []);
    setPromptTemplate(outlookQ.data.prompt_template ?? "");
    setSystemPrompt(outlookQ.data.system_prompt ?? "");
  }, [outlookQ.data]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const saveM = useMutation({
    mutationFn: () =>
      saveAiProviderSettings({
        provider: editingProvider,
        api_key: apiKey.trim(),
        model: model.trim() || undefined,
        enabled: true,
      }),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider"] });
      alert(data.message ?? "Saved");
      setApiKey("");
      setTestedOk(false);
      setModalOpen(false);
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Save failed"),
  });

  const testM = useMutation({
    mutationFn: () =>
      testAiProviderSettings({
        provider: editingProvider,
        api_key: apiKey.trim(),
        model: model.trim() || undefined,
      }),
    onSuccess: (data) => {
      setTestedOk(true);
      alert(data.message ?? "Provider key valid.");
    },
    onError: (e) => {
      setTestedOk(false);
      alert(e instanceof Error ? e.message : "Test failed");
    },
  });

  const revokeM = useMutation({
    mutationFn: () => revokeAiProviderSettings(),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider"] });
      alert(data.message ?? "Revoked");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Revoke failed"),
  });

  const saveOutlookM = useMutation({
    mutationFn: () =>
      saveOutlookConfig({
        feeds,
        prompt_template: promptTemplate,
        system_prompt: systemPrompt,
      }),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "outlook-config"] });
      alert(data.message ?? "Outlook configuration saved.");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Save failed"),
  });

  const resetFeedsM = useMutation({
    mutationFn: () => resetOutlookConfig({ reset_feeds: true, reset_prompt: false }),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "outlook-config"] });
      setFeeds(data.feeds ?? []);
      alert(data.message ?? "Feeds reset to default.");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Reset failed"),
  });

  const resetPromptM = useMutation({
    mutationFn: () => resetOutlookConfig({ reset_feeds: false, reset_prompt: true }),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "outlook-config"] });
      setPromptTemplate(data.prompt_template ?? "");
      alert(data.message ?? "Prompt reset to default.");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Reset failed"),
  });

  const resetSystemPromptM = useMutation({
    mutationFn: () => resetOutlookConfig({ reset_feeds: false, reset_prompt: false, reset_system_prompt: true }),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "outlook-config"] });
      setSystemPrompt(data.system_prompt ?? "");
      alert(data.message ?? "System prompt reset to default.");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Reset failed"),
  });

  const cur = q.data;
  const configuredProvider = cur?.configured ? (cur.provider ?? null) : null;

  return (
    <AppShell>
      <section className="app-card max-w-2xl space-y-5 p-5">
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>
        <h2 className="text-xl app-text-heading">Gen AI Settings</h2>
        {q.isLoading ? <div className="text-sm app-text-muted">Loading…</div> : null}
        {q.error ? (
          <div className="app-alert-error text-sm">
            {q.error instanceof Error ? q.error.message : "Unable to load"}
          </div>
        ) : null}
        <section className="space-y-3">
          <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
            AI Provider Keys (BYOK)
          </h3>
          <p className="text-xs app-text-muted">
            English-only generation. AI-generated outlook is informational only and not
            investment advice.
          </p>
          <div className="space-y-1">
          <ProviderRow
            title="Google"
            subtitle="Gemini API"
            configuredText={
              configuredProvider === "gemini"
                ? `Model: ${cur?.model ?? "default"} • API key: ${cur?.masked_api_key ?? "—"}`
                : null
            }
            revokeDisabled={
              revokeM.isPending || !cur?.configured || configuredProvider !== "gemini"
            }
            onRevoke={() => revokeM.mutate()}
            onClick={() => {
              setEditingProvider("gemini");
              setApiKey("");
              setModel(
                configuredProvider === "gemini" && cur?.model
                  ? cur.model
                  : modelOptions.gemini[0],
              );
              setTestedOk(false);
              setModalOpen(true);
            }}
          />
          <ProviderRow
            title="OpenAI"
            subtitle="Chat Completions API"
            configuredText={
              configuredProvider === "openai"
                ? `Model: ${cur?.model ?? "default"} • API key: ${cur?.masked_api_key ?? "—"}`
                : null
            }
            revokeDisabled={
              revokeM.isPending || !cur?.configured || configuredProvider !== "openai"
            }
            onRevoke={() => revokeM.mutate()}
            onClick={() => {
              setEditingProvider("openai");
              setApiKey("");
              setModel(
                configuredProvider === "openai" && cur?.model
                  ? cur.model
                  : modelOptions.openai[0],
              );
              setTestedOk(false);
              setModalOpen(true);
            }}
          />
          </div>
        </section>

        <KeyModal
          open={modalOpen}
          title="Configure API key"
          providerLabel={editingProvider === "gemini" ? "Google (Gemini)" : "OpenAI"}
          apiKey={apiKey}
          model={model}
          modelChoices={modelOptions[editingProvider]}
          canSave={testedOk && !!apiKey.trim()}
          isTesting={testM.isPending}
          isSaving={saveM.isPending}
          onClose={() => {
            setModalOpen(false);
            setApiKey("");
            setTestedOk(false);
          }}
          onChangeApiKey={(v) => {
            setApiKey(v);
            setTestedOk(false);
          }}
          onChangeModel={(v) => {
            setModel(v);
            setTestedOk(false);
          }}
          onTest={() => testM.mutate()}
          onSave={() => saveM.mutate()}
        />

        <div className="h-px bg-zinc-200 dark:bg-zinc-800" />

        <section className="space-y-4">
          <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
            Market Outlook Sources & Prompt
          </h3>
          {outlookQ.isLoading ? <div className="text-sm app-text-muted">Loading outlook config…</div> : null}
          {outlookQ.error ? (
            <div className="app-alert-error text-sm">
              {outlookQ.error instanceof Error ? outlookQ.error.message : "Unable to load outlook config"}
            </div>
          ) : null}

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-xs text-zinc-600 dark:text-zinc-400">RSS / News Sources</label>
            </div>
            <div className="space-y-2 rounded-xl bg-transparent">
              {feeds.length ? (
                feeds.map((feed, idx) => {
                  const isEditing = editingFeedIdx === idx || !feed.url.trim();
                  const safeUrl = (feed.url || "").trim();
                  return (
                    <div
                      key={`${idx}-${feed.url}`}
                      className="grid items-center gap-3 px-1 py-1 md:grid-cols-[1fr_1.4fr_auto]"
                    >
                      {isEditing ? (
                        <input
                          className={`${fieldCls} md:col-span-2`}
                          placeholder="https://..."
                          value={feed.url}
                          onChange={(e) =>
                            setFeeds((prev) =>
                              prev.map((f, i) => (i === idx ? { ...f, url: e.target.value } : f)),
                            )
                          }
                        />
                      ) : (
                        <a
                          className="truncate text-sm font-medium text-zinc-900 hover:underline dark:text-zinc-100 md:col-span-2"
                          href={safeUrl}
                          target="_blank"
                          rel="noreferrer"
                          title={safeUrl}
                        >
                          {safeUrl}
                        </a>
                      )}

                      <div className="flex items-center justify-end gap-2">
                        <button
                          type="button"
                          className="inline-flex h-10 w-10 items-center justify-center rounded-xl text-zinc-600 transition-all hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
                          onClick={() => setEditingFeedIdx((curIdx) => (curIdx === idx ? null : idx))}
                          aria-label={isEditing ? "Done editing" : "Edit source"}
                          title={isEditing ? "Done" : "Edit"}
                        >
                          {isEditing ? (
                            <svg
                              width="16"
                              height="16"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="1.8"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              aria-hidden
                            >
                              <path d="M20 6 9 17l-5-5" />
                            </svg>
                          ) : (
                            <svg
                              width="16"
                              height="16"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="1.8"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              aria-hidden
                            >
                              <path d="M12 20h9" />
                              <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                            </svg>
                          )}
                        </button>
                        <button
                          type="button"
                          className="inline-flex h-10 w-10 items-center justify-center rounded-xl text-zinc-600 transition-all hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
                          onClick={() => {
                            setFeeds((prev) => prev.filter((_, i) => i !== idx));
                            setEditingFeedIdx((curIdx) => (curIdx === idx ? null : curIdx));
                          }}
                          aria-label="Delete source"
                          title="Delete source"
                        >
                          <svg
                            width="16"
                            height="16"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.8"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden
                          >
                            <path d="M3 6h18" />
                            <path d="M8 6V4h8v2" />
                            <path d="M19 6l-1 14H6L5 6" />
                            <path d="M10 11v6" />
                            <path d="M14 11v6" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="px-1 py-1 text-sm text-zinc-500 dark:text-zinc-400">
                  No sources yet.
                </div>
              )}

              <div className="grid items-center gap-3 px-1 py-1 md:grid-cols-[1fr_1.4fr_auto]">
                <div className="text-xs text-zinc-500 dark:text-zinc-400">
                  Add a new source
                </div>
                <div />
                <div className="flex items-center justify-end">
                  <button
                    type="button"
                    className="inline-flex h-10 w-10 items-center justify-center rounded-xl text-base font-semibold text-zinc-700 transition-all hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-900"
                    onClick={() => {
                      setFeeds((prev) => [...prev, { name: "", url: "" }]);
                      setEditingFeedIdx(feeds.length);
                    }}
                    aria-label="Add source"
                    title="Add source"
                  >
                    +
                  </button>
                </div>
              </div>
            </div>
            <p className="text-[11px] app-text-muted">
              {outlookQ.data?.using_default_feeds ? "Using default feeds." : "Using custom feeds."}
            </p>
            <button
              type="button"
              className="app-link text-xs"
              disabled={resetFeedsM.isPending}
              onClick={() => resetFeedsM.mutate()}
            >
              {resetFeedsM.isPending ? "Resetting feeds..." : "Reset feeds to default"}
            </button>
          </div>

          <div className="space-y-2">
            <label className="text-xs text-zinc-600 dark:text-zinc-400">System prompt</label>
            <textarea
              className={`${fieldCls} min-h-24 font-mono text-xs`}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="You are a macro-strategist. Analyze geopolitical, business, and economic news specifically to identify 'Transmission Channels' to the market. For every news item, explain the impact on: 1) Cost of Capital (Interest rates), 2) Input Costs (Commodities), and 3) Investor Sentiment (Risk-on/Risk-off)."
            />
            <p className="text-[11px] app-text-muted">
              {outlookQ.data?.using_default_system_prompt
                ? "Using default system prompt."
                : "Using custom system prompt."}
            </p>
            <button
              type="button"
              className="app-link text-xs"
              disabled={resetSystemPromptM.isPending}
              onClick={() => resetSystemPromptM.mutate()}
            >
              {resetSystemPromptM.isPending ? "Resetting system prompt..." : "Reset system prompt to default"}
            </button>
          </div>

          <div className="space-y-2">
            <label className="text-xs text-zinc-600 dark:text-zinc-400">
              Prompt template (supports placeholders: {"{scope}"}, {"{symbol}"}, {"{sources_json}"}, {"{required_schema_json}"})
            </label>
            <textarea
              className={`${fieldCls} min-h-48 font-mono text-xs`}
              value={promptTemplate}
              onChange={(e) => setPromptTemplate(e.target.value)}
            />
            <p className="text-[11px] app-text-muted">
              {outlookQ.data?.using_default_prompt ? "Using default prompt." : "Using custom prompt."}
            </p>
            <button
              type="button"
              className="app-link text-xs"
              disabled={resetPromptM.isPending}
              onClick={() => resetPromptM.mutate()}
            >
              {resetPromptM.isPending ? "Resetting prompt..." : "Reset prompt to default"}
            </button>
          </div>
          <div>
            <button
              type="button"
              className="app-btn-primary"
              disabled={saveOutlookM.isPending}
              onClick={() => saveOutlookM.mutate()}
            >
              {saveOutlookM.isPending ? "Saving..." : "Save Prompt"}
            </button>
          </div>
        </section>
      </section>
    </AppShell>
  );
}
