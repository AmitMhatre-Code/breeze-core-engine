"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import {
  deleteAiProvider,
  getGeminiModels,
  getAiProviderState,
  getOutlookConfig,
  OPENAI_MODEL_OPTIONS,
  type AiProviderSideState,
  type OutlookFeedConfig,
  patchAiProviderSettings,
  resetOutlookConfig,
  saveAiProviderSettings,
  saveOutlookConfig,
  setOutlookAiProvider,
  testAiProviderModel,
  testAiProviderSettings,
} from "@/lib/outlook-api";

const fieldCls =
  "mt-1 h-10 w-full rounded-md border border-zinc-300/80 bg-white/95 px-3 text-sm text-zinc-900 shadow-sm outline-none transition-all hover:border-zinc-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus:border-blue-400 dark:focus:ring-blue-400/20";

function CollapsiblePanel({
  title,
  subtitle,
  open,
  onToggle,
  children,
}: {
  title: string;
  subtitle?: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-start gap-2 px-3 py-3 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
      >
        <span
          className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center text-zinc-500 dark:text-zinc-400"
          aria-hidden
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            className={open ? "rotate-180 transition-transform" : "transition-transform"}
          >
            <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-zinc-900 dark:text-zinc-100">{title}</span>
          {subtitle ? (
            <span className="mt-0.5 block text-xs text-zinc-500 dark:text-zinc-400">{subtitle}</span>
          ) : null}
        </span>
      </button>
      {open ? <div className="border-t border-zinc-200 px-3 py-3 dark:border-zinc-800">{children}</div> : null}
    </div>
  );
}

function EditIcon() {
  return (
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
  );
}

function DeleteIcon() {
  return (
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
  );
}

function RefreshIcon({ className }: { className?: string }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

function FallbackToggleSwitch({
  pressed,
  disabled,
  onPressedChange,
  title,
  ariaLabel,
}: {
  pressed: boolean;
  disabled: boolean;
  onPressedChange: (next: boolean) => void;
  title?: string;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={pressed}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
      onClick={() => onPressedChange(!pressed)}
      className={[
        "relative inline-flex h-4 w-7 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-zinc-950",
        disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer",
        pressed ? "bg-blue-600 dark:bg-blue-500" : "bg-zinc-300 dark:bg-zinc-600",
      ].join(" ")}
    >
      <span
        aria-hidden
        className={[
          "pointer-events-none absolute top-0.5 left-0.5 block h-3 w-3 rounded-full bg-white shadow-sm transition-transform duration-200 ease-out dark:bg-zinc-50",
          pressed ? "translate-x-3" : "translate-x-0",
        ].join(" ")}
      />
    </button>
  );
}

function modelDotClass(side: AiProviderSideState, modelId: string): string {
  const h = side.model_health?.[modelId];
  if (!h) return "bg-zinc-400";
  return h.ok ? "bg-emerald-500" : "bg-red-500";
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <button type="button" className="absolute inset-0 bg-black/50" aria-label="Close modal" onClick={onClose} />
      <div className="relative w-full max-w-lg rounded-lg border border-zinc-200 bg-white p-5 shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-base font-semibold text-zinc-900 dark:text-zinc-100">{title}</div>
            <div className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">{providerLabel}</div>
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
            Primary model
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
              aria-busy={isTesting}
              className="app-btn-outline"
              onClick={onTest}
            >
              <AsyncLabelSpan busy={isTesting} idleLabel="Test key" busyLabel="Testing…" />
            </button>
            <button
              type="button"
              disabled={isSaving || !canSave}
              aria-busy={isSaving}
              className="app-btn-primary"
              onClick={onSave}
            >
              <AsyncLabelSpan busy={isSaving} idleLabel="Save Key" busyLabel="Saving…" />
            </button>
          </div>

          <p className="text-[11px] app-text-muted">Save is enabled only after a successful test.</p>
        </div>
      </div>
    </div>
  );
}

function LlmProviderCard({
  kind,
  side,
  label,
  subLabel,
  expanded,
  onToggleExpanded,
  geminiModelIds,
  onEdit,
  onDelete,
  deleteDisabled,
  onToggleFallback,
  onTestModel,
  testingModelId,
  patchPending,
}: {
  kind: "gemini" | "openai";
  side: AiProviderSideState;
  label: string;
  subLabel: string;
  expanded: boolean;
  onToggleExpanded: () => void;
  geminiModelIds: string[];
  onEdit: () => void;
  onDelete: () => void;
  deleteDisabled: boolean;
  onToggleFallback: (modelId: string, include: boolean) => void;
  onTestModel: (modelId: string) => void;
  testingModelId: string | null;
  patchPending: boolean;
}) {
  const configured = side.configured && side.enabled;
  const primary = (side.model || "").trim();
  const openAiIds = [...OPENAI_MODEL_OPTIONS];
  const modelIds = kind === "gemini" ? geminiModelIds : openAiIds;

  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800">
      <div className="flex items-center gap-1 px-2 py-2">
        <button
          type="button"
          onClick={onToggleExpanded}
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-zinc-600 transition-all hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900"
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            className={expanded ? "rotate-180 transition-transform" : "transition-transform"}
          >
            <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <button
          type="button"
          onClick={onToggleExpanded}
          className="min-w-0 flex-1 rounded-md px-1 py-1 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900/40"
        >
          <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{label}</div>
          <div className="text-xs text-zinc-500 dark:text-zinc-400">{subLabel}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {side.configured ? (
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-900 dark:bg-emerald-900/45 dark:text-emerald-100">
                Configured
              </span>
            ) : (
              <span className="rounded-full bg-zinc-200 px-2 py-0.5 text-[11px] font-medium text-zinc-700 dark:bg-zinc-600 dark:text-zinc-200">
                Not configured
              </span>
            )}
            {side.configured ? (
              <>
                <span className="inline-flex items-center gap-1 text-[11px] text-zinc-600 dark:text-zinc-300">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" aria-hidden />
                  {side.models_working} working
                </span>
                <span className="inline-flex items-center gap-1 text-[11px] text-zinc-600 dark:text-zinc-300">
                  <span className="h-2 w-2 rounded-full bg-red-500" aria-hidden />
                  {side.models_failing} not working
                </span>
              </>
            ) : null}
          </div>
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onEdit();
          }}
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-zinc-600 transition-all hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
          aria-label={`Edit ${label} key and primary model`}
          title="Edit"
        >
          <EditIcon />
        </button>
        <button
          type="button"
          disabled={deleteDisabled}
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className={[
            "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md transition-all",
            deleteDisabled
              ? "cursor-not-allowed text-zinc-400 dark:text-zinc-600"
              : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-100",
          ].join(" ")}
          aria-label={`Delete ${label} provider`}
          title="Delete"
        >
          <DeleteIcon />
        </button>
      </div>

      {expanded ? (
        <div className="border-t border-zinc-200 px-3 py-3 dark:border-zinc-800">
          {!configured ? (
            <p className="text-sm app-text-muted">Configure an API key to see models and health.</p>
          ) : (
            <div className="space-y-2">
              {primary ? (
                <p className="text-[11px] text-zinc-600 dark:text-zinc-400">
                  Primary model: <span className="font-mono text-zinc-800 dark:text-zinc-200">{primary}</span>{" "}
                  (change via Edit)
                </p>
              ) : null}
              {kind === "gemini" && !geminiModelIds.length ? (
                <div className="text-sm app-text-muted">Loading model list…</div>
              ) : null}
              <ul className="space-y-1">
                {modelIds.map((mid) => {
                  const isPrimary = mid === primary;
                  const inFallback = side.fallback_models.includes(mid);
                  const healthOk = side.model_health[mid]?.ok === true;
                  const fallbackToggleDisabled =
                    patchPending || isPrimary || (!healthOk && !inFallback);
                  return (
                    <li
                      key={mid}
                      className="flex flex-wrap items-center gap-2 rounded-md border border-zinc-100 px-2 py-1.5 dark:border-zinc-800"
                    >
                      <span className="flex min-w-0 flex-1 items-center gap-2">
                        <span className="min-w-0 truncate font-mono text-xs text-zinc-800 dark:text-zinc-200">
                          {mid}
                        </span>
                        <span
                          className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${modelDotClass(side, mid)}`}
                          title={side.model_health[mid]?.message || undefined}
                        />
                      </span>
                      <FallbackToggleSwitch
                        pressed={inFallback && !isPrimary}
                        disabled={fallbackToggleDisabled}
                        onPressedChange={(next) => onToggleFallback(mid, next)}
                        ariaLabel={`Include ${mid} in fallback chain`}
                        title={
                          isPrimary
                            ? "Primary model"
                            : !healthOk && !inFallback
                              ? "Run a successful test to enable fallback"
                              : "Include in fallback chain"
                        }
                      />
                      <button
                        type="button"
                        disabled={testingModelId === mid}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
                        title="Test this model"
                        aria-label={`Test model ${mid}`}
                        onClick={() => onTestModel(mid)}
                      >
                        <RefreshIcon
                          className={testingModelId === mid ? "animate-spin" : undefined}
                        />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      ) : null}
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
  const [editingProvider, setEditingProvider] = useState<"gemini" | "openai">("gemini");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gemini-2.5-flash");
  const [testedOk, setTestedOk] = useState(false);
  const [expanded, setExpanded] = useState<{ gemini: boolean; openai: boolean }>({
    gemini: false,
    openai: false,
  });
  const [testingModel, setTestingModel] = useState<{ provider: "gemini" | "openai"; model: string } | null>(null);
  const [feedsOpen, setFeedsOpen] = useState(true);
  const [promptsOpen, setPromptsOpen] = useState(true);
  const [feeds, setFeeds] = useState<OutlookFeedConfig[]>([]);
  const [promptTemplate, setPromptTemplate] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [editingFeedIdx, setEditingFeedIdx] = useState<number | null>(null);

  const outlookQ = useQuery({
    queryKey: ["settings", "outlook-config"],
    queryFn: () => getOutlookConfig(),
  });
  const geminiModelsQ = useQuery({
    queryKey: ["settings", "ai-provider", "gemini-models"],
    queryFn: () => getGeminiModels(),
    enabled: Boolean(
      q.data?.gemini?.configured &&
        (expanded.gemini || (modalOpen && editingProvider === "gemini")),
    ),
  });

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!outlookQ.data) return;
    setFeeds(outlookQ.data.feeds ?? []);
    setPromptTemplate(outlookQ.data.prompt_template ?? "");
    setSystemPrompt(outlookQ.data.system_prompt ?? "");
  }, [outlookQ.data]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const geminiCatalogIds = [
    ...(geminiModelsQ.data?.available_models?.map((m) => m.model) ?? []),
    ...(geminiModelsQ.data?.stale_models?.map((m) => m.model) ?? []),
  ].filter((m, i, a) => a.indexOf(m) === i);

  const saveM = useMutation({
    mutationFn: () =>
      saveAiProviderSettings({
        provider: editingProvider,
        api_key: apiKey.trim(),
        model: model.trim() || undefined,
        fallback_models:
          editingProvider === "gemini" ? (q.data?.gemini.fallback_models ?? []) : [],
        enabled: true,
      }),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider"] });
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider", "gemini-models"] });
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
        fallback_models:
          editingProvider === "gemini" ? (q.data?.gemini.fallback_models ?? []) : [],
      }),
    onSuccess: (data) => {
      setTestedOk(Boolean(data.ok));
      if (!data.ok) {
        const failed = (data.results ?? []).filter((r) => !r.ok).map((r) => r.model);
        alert(
          `${data.message ?? "Test failed"}${failed.length ? ` Failed: ${failed.join(", ")}` : ""}`,
        );
        return;
      }
      alert(data.message ?? "Provider key valid.");
    },
    onError: (e) => {
      setTestedOk(false);
      alert(e instanceof Error ? e.message : "Test failed");
    },
  });

  const deleteM = useMutation({
    mutationFn: (provider: "gemini" | "openai") => deleteAiProvider(provider),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider"] });
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider", "gemini-models"] });
      alert(data.message ?? "Removed");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Delete failed"),
  });

  const patchM = useMutation({
    mutationFn: (body: { provider: "gemini" | "openai"; model?: string; fallback_models: string[] }) =>
      patchAiProviderSettings(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider"] });
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Update failed"),
  });

  const testModelM = useMutation({
    mutationFn: (args: { provider: "gemini" | "openai"; model: string }) => testAiProviderModel(args),
    onMutate: (args) => setTestingModel(args),
    onSettled: () => setTestingModel(null),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider"] });
      void qc.invalidateQueries({ queryKey: ["settings", "ai-provider", "gemini-models"] });
      if (!data.ok) alert(data.message ?? "Model test failed");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Test failed"),
  });

  const outlookPickM = useMutation({
    mutationFn: (provider: "gemini" | "openai") => setOutlookAiProvider({ provider }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["settings", "ai-provider"] }),
    onError: (e) => alert(e instanceof Error ? e.message : "Update failed"),
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
    mutationFn: () =>
      resetOutlookConfig({ reset_feeds: false, reset_prompt: false, reset_system_prompt: true }),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["settings", "outlook-config"] });
      setSystemPrompt(data.system_prompt ?? "");
      alert(data.message ?? "System prompt reset to default.");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Reset failed"),
  });

  const cur = q.data;
  const geminiSide = cur?.gemini;
  const openaiSide = cur?.openai;
  const bothReady =
    Boolean(geminiSide?.configured && geminiSide.enabled && openaiSide?.configured && openaiSide.enabled);

  const openEdit = (provider: "gemini" | "openai") => {
    setEditingProvider(provider);
    setApiKey("");
    const side = provider === "gemini" ? cur?.gemini : cur?.openai;
    setModel(
      (side?.model || "").trim() ||
        (provider === "gemini" ? "gemini-2.5-flash" : OPENAI_MODEL_OPTIONS[0]),
    );
    setTestedOk(false);
    setModalOpen(true);
  };

  const toggleFallback = (provider: "gemini" | "openai", modelId: string, include: boolean) => {
    const side = provider === "gemini" ? cur?.gemini : cur?.openai;
    if (!side) return;
    const primary = (side.model || "").trim();
    let next = [...(side.fallback_models ?? [])];
    if (include) {
      if (!next.includes(modelId) && modelId !== primary) next.push(modelId);
    } else {
      next = next.filter((m) => m !== modelId);
    }
    patchM.mutate({ provider, fallback_models: next });
  };

  return (
    <AppShell>
      <section className="app-card max-w-2xl space-y-5 p-5">
        <Link href="/settings" className="app-link inline-block text-xs">
          Back to Settings
        </Link>
        <h2 className="app-text-heading text-xl">Gen AI Settings</h2>
        {q.isLoading ? <div className="text-sm app-text-muted">Loading…</div> : null}
        {q.error ? (
          <div className="app-alert-error text-sm">
            {q.error instanceof Error ? q.error.message : "Unable to load"}
          </div>
        ) : null}

        <section className="space-y-3">
          <h3 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">AI Provider Keys (BYOK)</h3>
          <p className="text-xs app-text-muted">
            English-only generation. AI-generated outlook is informational only and not investment advice.
          </p>

          {bothReady ? (
            <div className="rounded-md border border-zinc-200 px-3 py-2 dark:border-zinc-800">
              <label className="text-xs text-zinc-600 dark:text-zinc-400">
                LLM used for Market Outlook
                <select
                  className={`${fieldCls} mt-1 appearance-none`}
                  value={cur?.outlook_ai_provider ?? "gemini"}
                  disabled={outlookPickM.isPending}
                  onChange={(e) =>
                    outlookPickM.mutate(e.target.value as "gemini" | "openai")
                  }
                >
                  <option value="gemini">Google (Gemini)</option>
                  <option value="openai">OpenAI</option>
                </select>
              </label>
            </div>
          ) : null}

          <div className="space-y-3">
            {geminiSide ? (
              <LlmProviderCard
                kind="gemini"
                side={geminiSide}
                label="Google"
                subLabel="Gemini API"
                expanded={expanded.gemini}
                onToggleExpanded={() =>
                  setExpanded((s) => ({ ...s, gemini: !s.gemini }))
                }
                geminiModelIds={geminiCatalogIds}
                onEdit={() => openEdit("gemini")}
                onDelete={() => {
                  if (!confirm("Remove saved Gemini API key and settings?")) return;
                  deleteM.mutate("gemini");
                }}
                deleteDisabled={deleteM.isPending || !geminiSide.configured}
                onToggleFallback={(mid, inc) => toggleFallback("gemini", mid, inc)}
                onTestModel={(mid) => testModelM.mutate({ provider: "gemini", model: mid })}
                testingModelId={
                  testingModel?.provider === "gemini" ? testingModel.model : null
                }
                patchPending={patchM.isPending}
              />
            ) : null}
            {openaiSide ? (
              <LlmProviderCard
                kind="openai"
                side={openaiSide}
                label="OpenAI"
                subLabel="Chat Completions API"
                expanded={expanded.openai}
                onToggleExpanded={() =>
                  setExpanded((s) => ({ ...s, openai: !s.openai }))
                }
                geminiModelIds={[]}
                onEdit={() => openEdit("openai")}
                onDelete={() => {
                  if (!confirm("Remove saved OpenAI API key and settings?")) return;
                  deleteM.mutate("openai");
                }}
                deleteDisabled={deleteM.isPending || !openaiSide.configured}
                onToggleFallback={(mid, inc) => toggleFallback("openai", mid, inc)}
                onTestModel={(mid) => testModelM.mutate({ provider: "openai", model: mid })}
                testingModelId={
                  testingModel?.provider === "openai" ? testingModel.model : null
                }
                patchPending={patchM.isPending}
              />
            ) : null}
          </div>
        </section>

        <KeyModal
          open={modalOpen}
          title="Configure API key"
          providerLabel={editingProvider === "gemini" ? "Google (Gemini)" : "OpenAI"}
          apiKey={apiKey}
          model={model}
          modelChoices={(() => {
            if (editingProvider === "openai") return [...OPENAI_MODEL_OPTIONS];
            const ids = (geminiModelsQ.data?.available_models ?? [])
              .concat(geminiModelsQ.data?.stale_models ?? [])
              .map((m) => m.model)
              .filter((m, i, a) => a.indexOf(m) === i);
            return ids.length ? ids : ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest"];
          })()}
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

          <CollapsiblePanel
            title="Market Feeds"
            subtitle="RSS and news sources for outlook context"
            open={feedsOpen}
            onToggle={() => setFeedsOpen((o) => !o)}
          >
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-xs text-zinc-600 dark:text-zinc-400">RSS / News Sources</label>
              </div>
              <div className="space-y-2 rounded-md bg-transparent">
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
                            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-zinc-600 transition-all hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
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
                              <EditIcon />
                            )}
                          </button>
                          <button
                            type="button"
                            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-zinc-600 transition-all hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
                            onClick={() => {
                              setFeeds((prev) => prev.filter((_, i) => i !== idx));
                              setEditingFeedIdx((curIdx) => (curIdx === idx ? null : curIdx));
                            }}
                            aria-label="Delete source"
                            title="Delete source"
                          >
                            <DeleteIcon />
                          </button>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="px-1 py-1 text-sm text-zinc-500 dark:text-zinc-400">No sources yet.</div>
                )}

                <div className="grid items-center gap-3 px-1 py-1 md:grid-cols-[1fr_1.4fr_auto]">
                  <div className="text-xs text-zinc-500 dark:text-zinc-400">Add a new source</div>
                  <div />
                  <div className="flex items-center justify-end">
                    <button
                      type="button"
                      className="inline-flex h-10 w-10 items-center justify-center rounded-md text-base font-semibold text-zinc-700 transition-all hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-900"
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
                aria-busy={resetFeedsM.isPending}
                onClick={() => resetFeedsM.mutate()}
              >
                <AsyncLabelSpan
                  busy={resetFeedsM.isPending}
                  idleLabel="Reset feeds to default"
                  busyLabel="Resetting feeds..."
                />
              </button>
            </div>
          </CollapsiblePanel>

          <CollapsiblePanel
            title="Prompts"
            subtitle="System prompt and outlook generation template"
            open={promptsOpen}
            onToggle={() => setPromptsOpen((o) => !o)}
          >
            <div className="space-y-4">
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
                  aria-busy={resetSystemPromptM.isPending}
                  onClick={() => resetSystemPromptM.mutate()}
                >
                  <AsyncLabelSpan
                    busy={resetSystemPromptM.isPending}
                    idleLabel="Reset system prompt to default"
                    busyLabel="Resetting system prompt..."
                  />
                </button>
              </div>

              <div className="space-y-2">
                <label className="text-xs text-zinc-600 dark:text-zinc-400">
                  Prompt template (supports placeholders: {"{scope}"}, {"{symbol}"}, {"{sources_json}"},{" "}
                  {"{required_schema_json}"})
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
                  aria-busy={resetPromptM.isPending}
                  onClick={() => resetPromptM.mutate()}
                >
                  <AsyncLabelSpan
                    busy={resetPromptM.isPending}
                    idleLabel="Reset prompt to default"
                    busyLabel="Resetting prompt..."
                  />
                </button>
              </div>
              <div>
                <button
                  type="button"
                  className="app-btn-primary"
                  disabled={saveOutlookM.isPending}
                  aria-busy={saveOutlookM.isPending}
                  onClick={() => saveOutlookM.mutate()}
                >
                  <AsyncLabelSpan
                    busy={saveOutlookM.isPending}
                    idleLabel="Save Prompt"
                    busyLabel="Saving..."
                  />
                </button>
              </div>
            </div>
          </CollapsiblePanel>
        </section>
      </section>
    </AppShell>
  );
}
