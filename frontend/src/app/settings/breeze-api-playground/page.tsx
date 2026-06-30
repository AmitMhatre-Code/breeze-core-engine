"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { HelpLink } from "@/components/help/HelpLink";
import { BreezeApiMethodPicker } from "@/components/settings/BreezeApiMethodPicker";
import { BreezeApiRiskGateDialog } from "@/components/settings/BreezeApiRiskGateDialog";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import {
  acknowledgeBreezeApiTesterRisk,
  getBreezeApiTesterCatalog,
  getBreezeApiTesterRiskStatus,
  invokeBreezeApiTester,
  RISK_GROUP_LABEL,
  wsConnectPlayground,
  wsDisconnectPlayground,
  wsGetPlaygroundEventLog,
  wsReleasePlayground,
  wsStreamUrl,
  wsSubscribePlayground,
  type BreezeApiCatalogEntry,
  type BreezeApiInvokeResponse,
  type BreezeApiWsEventLogEntry,
  type BreezeApiWsStatus,
} from "@/lib/breeze-api-tester";
import { copyTextToClipboard } from "@/lib/copy-to-clipboard";
import {
  buildWsSubscribeParams,
  WS_FIELD_PLACEHOLDERS,
  WS_FORM_INITIAL,
  WS_SUBSCRIBE_MODE_OPTIONS,
  WS_SUBSCRIBE_MODES,
  type WsSubscribeMode,
} from "@/lib/breeze-api-playground-ws-subscribe";
import { useWsSubscriptionHolder } from "@/lib/use-ws-subscription-holder";

const inputCls =
  "mt-1 w-full rounded-md border border-zinc-300/80 bg-white/95 px-3 py-2 text-sm text-zinc-900 shadow-sm outline-none transition-all hover:border-zinc-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus:border-blue-400 dark:focus:ring-blue-400/20";

const RISK_ORDER: BreezeApiCatalogEntry["risk_level"][] = ["read", "funds", "trade", "gtt"];

const WS_LOG_LIMIT = 50;

function formatJsonValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatResponse(payload: BreezeApiInvokeResponse | null, invokeError: string | null): string {
  if (invokeError) return invokeError;
  if (!payload) return "";
  return formatJsonValue(payload.response ?? payload);
}

function formatWsApiResponse(payload: BreezeApiWsStatus | null): string {
  if (!payload) return "";
  return formatJsonValue(payload);
}

function formatCommandLogEntry(entry: BreezeApiWsEventLogEntry): string {
  const ts = new Date(entry.ts * 1000).toLocaleTimeString();
  const lines = [
    `[${ts}] ${entry.step} · ok=${entry.ok}`,
    entry.note ? `note: ${entry.note}` : "",
    `command: ${formatJsonValue(entry.icici_command)}`,
    `response: ${formatJsonValue(entry.icici_response)}`,
  ];
  return lines.filter(Boolean).join("\n");
}

function statusToLogEntry(data: BreezeApiWsStatus, step: string): BreezeApiWsEventLogEntry | null {
  if (!data.icici_command && data.event_id == null) return null;
  return {
    id: data.event_id ?? Date.now(),
    ts: Date.now() / 1000,
    step,
    icici_command: data.icici_command ?? { sdk_method: step, sdk_args: {}, side_effects: [] },
    icici_response: data.response ?? null,
    ok: data.ok !== false,
  };
}

function mergeLogEntries(
  prev: BreezeApiWsEventLogEntry[],
  incoming: BreezeApiWsEventLogEntry[],
): BreezeApiWsEventLogEntry[] {
  const byId = new Map<number, BreezeApiWsEventLogEntry>();
  for (const entry of [...incoming, ...prev]) {
    byId.set(entry.id, entry);
  }
  return [...byId.values()]
    .sort((a, b) => b.ts - a.ts)
    .slice(0, WS_LOG_LIMIT);
}

function tickPayloadToLogEntry(rawJson: string, seq: number): BreezeApiWsEventLogEntry | null {
  try {
    const tick = JSON.parse(rawJson) as unknown;
    return {
      id: -(Date.now() * 1000 + seq),
      ts: Date.now() / 1000,
      step: "tick_received",
      icici_command: { sdk_method: "", sdk_args: {}, side_effects: [] },
      icici_response: tick,
      ok: true,
    };
  } catch {
    return null;
  }
}

function formatTickForDisplay(rawJson: string): string {
  try {
    return formatJsonValue(JSON.parse(rawJson) as unknown);
  } catch {
    return rawJson;
  }
}

export default function BreezeApiPlaygroundPage() {
  const subscriptionHolder = useWsSubscriptionHolder();
  const qc = useQueryClient();
  const [selectedMethod, setSelectedMethod] = useState("");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [extraParamsJson, setExtraParamsJson] = useState("");
  const [lastResponse, setLastResponse] = useState<BreezeApiInvokeResponse | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);
  const [wsTicks, setWsTicks] = useState<string[]>([]);
  const [wsCommandLog, setWsCommandLog] = useState<BreezeApiWsEventLogEntry[]>([]);
  const [wsLastResponse, setWsLastResponse] = useState<BreezeApiWsStatus | null>(null);
  const [wsStatusHint, setWsStatusHint] = useState(
    "Not connected. Click Connect, fill contract fields, Subscribe, then Start tick stream.",
  );
  const [wsStreamOpen, setWsStreamOpen] = useState(false);
  const wsStreamRef = useRef<EventSource | null>(null);
  const tickLogSeqRef = useRef(0);
  const [wsForm, setWsForm] = useState<Record<string, string>>({ ...WS_FORM_INITIAL });
  const [wsSubscribeMode, setWsSubscribeMode] = useState<WsSubscribeMode>("fno_quotes");
  const [responseCopyState, setResponseCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [wsResponseCopyState, setWsResponseCopyState] = useState<"idle" | "copied" | "failed">(
    "idle",
  );
  const [wsLogCopyState, setWsLogCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const appendWsLog = useCallback((entry: BreezeApiWsEventLogEntry | null) => {
    if (!entry) return;
    setWsCommandLog((prev) => mergeLogEntries(prev, [entry]));
  }, []);

  const refreshEventLog = useCallback(async () => {
    try {
      const { events } = await wsGetPlaygroundEventLog();
      setWsCommandLog((prev) => mergeLogEntries(prev, events));
    } catch {
      /* ignore when not logged in */
    }
  }, []);

  const riskQ = useQuery({
    queryKey: ["settings", "breeze-api-tester", "risk"],
    queryFn: getBreezeApiTesterRiskStatus,
  });

  const catalogQ = useQuery({
    queryKey: ["settings", "breeze-api-tester", "catalog"],
    queryFn: getBreezeApiTesterCatalog,
    enabled: Boolean(riskQ.data?.accepted),
  });

  const ackM = useMutation({
    mutationFn: acknowledgeBreezeApiTesterRisk,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["settings", "breeze-api-tester", "risk"] });
    },
  });

  const invokeM = useMutation({
    mutationFn: ({ method, params }: { method: string; params: Record<string, string> }) =>
      invokeBreezeApiTester(method, params),
    onSuccess: (data) => {
      setLastResponse(data);
      setInvokeError(null);
    },
    onError: (e) => {
      setInvokeError(e instanceof Error ? e.message : "Invoke failed");
      setLastResponse(null);
    },
  });

  const entries = catalogQ.data?.entries ?? [];
  const selected = useMemo(
    () => entries.find((e) => e.method === selectedMethod) ?? null,
    [entries, selectedMethod],
  );

  useEffect(() => {
    if (!entries.length) return;
    if (!selectedMethod || !entries.some((e) => e.method === selectedMethod)) {
      setSelectedMethod(entries[0].method);
    }
  }, [entries, selectedMethod]);

  useEffect(() => {
    if (!selected) return;
    const next: Record<string, string> = {};
    for (const p of selected.params) {
      next[p.name] = paramValues[p.name] ?? "";
    }
    setParamValues(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset form when API changes
  }, [selected?.method]);

  const groupedOptions = useMemo(() => {
    const groups = new Map<BreezeApiCatalogEntry["risk_level"], BreezeApiCatalogEntry[]>();
    for (const level of RISK_ORDER) {
      groups.set(level, []);
    }
    for (const e of entries) {
      const list = groups.get(e.risk_level) ?? [];
      list.push(e);
      groups.set(e.risk_level, list);
    }
    return RISK_ORDER.map((level) => ({
      level,
      label: RISK_GROUP_LABEL[level],
      items: (groups.get(level) ?? []).sort((a, b) => a.title.localeCompare(b.title)),
    })).filter((g) => g.items.length > 0);
  }, [entries]);

  const buildParamsForInvoke = useCallback((): Record<string, string> => {
    if (!selected) return {};
    const out: Record<string, string> = {};
    const extraTrimmed = extraParamsJson.trim();
    if (extraTrimmed) {
      try {
        const extra = JSON.parse(extraTrimmed) as Record<string, unknown>;
        for (const [key, value] of Object.entries(extra)) {
          if (value == null) continue;
          const s = String(value).trim();
          if (s) out[key] = s;
        }
      } catch {
        /* invalid JSON ignored; catalog params still sent */
      }
    }
    for (const p of selected.params) {
      const v = (paramValues[p.name] ?? "").trim();
      if (v) out[p.name] = v;
    }
    return out;
  }, [selected, paramValues, extraParamsJson]);

  const onFire = () => {
    if (!selected) return;
    setInvokeError(null);
    invokeM.mutate({ method: selected.method, params: buildParamsForInvoke() });
  };

  const responseText = formatResponse(lastResponse, invokeError);
  const wsResponseText = formatWsApiResponse(wsLastResponse);
  const wsResponseIsError = wsLastResponse?.ok === false;
  const wsCommandLogText = wsCommandLog.map(formatCommandLogEntry).join("\n\n---\n\n");
  const showGate = riskQ.isSuccess && !riskQ.data?.accepted;

  useEffect(() => {
    if (!riskQ.data?.accepted) return;
    void refreshEventLog();
  }, [riskQ.data?.accepted, refreshEventLog]);

  const flashCopyState = useCallback(
    (target: "response" | "ws" | "wsLog", result: ReturnType<typeof copyTextToClipboard>) => {
      const next = result.ok ? "copied" : "failed";
      if (target === "response") {
        setResponseCopyState(next);
      } else if (target === "ws") {
        setWsResponseCopyState(next);
      } else {
        setWsLogCopyState(next);
      }
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
      copyResetRef.current = setTimeout(() => {
        if (target === "response") {
          setResponseCopyState("idle");
        } else if (target === "ws") {
          setWsResponseCopyState("idle");
        } else {
          setWsLogCopyState("idle");
        }
        copyResetRef.current = null;
      }, 2000);
    },
    [],
  );

  const copyResponse = () => {
    flashCopyState("response", copyTextToClipboard(responseText));
  };

  const copyWsResponse = () => {
    flashCopyState("ws", copyTextToClipboard(wsResponseText));
  };

  const copyWsLog = () => {
    flashCopyState("wsLog", copyTextToClipboard(wsCommandLogText));
  };

  const closeWsStream = useCallback(() => {
    wsStreamRef.current?.close();
    wsStreamRef.current = null;
    setWsStreamOpen(false);
  }, []);

  const wsConnectM = useMutation({
    mutationFn: wsConnectPlayground,
    onSuccess: (data) => {
      setWsLastResponse(data);
      appendWsLog(statusToLogEntry(data, "ws_connect"));
      void refreshEventLog();
      setWsStatusHint(
        data.ok && data.connected
          ? "ICICI socket connected."
          : data.connected
            ? "ICICI socket connected with warnings (see log)."
            : "ICICI socket not connected.",
      );
    },
    onError: (e) => {
      setWsStatusHint(e instanceof Error ? e.message : "Connect failed");
    },
  });
  const wsReleaseM = useMutation({
    mutationFn: () => wsReleasePlayground(subscriptionHolder),
    onSuccess: (data) => {
      closeWsStream();
      setWsLastResponse(data);
      appendWsLog(statusToLogEntry(data, "ws_release"));
      void refreshEventLog();
      setWsStatusHint("Subscriptions released. ICICI socket remains connected.");
    },
    onError: (e) => {
      setWsStatusHint(e instanceof Error ? e.message : "Release failed");
    },
  });
  const wsDisconnectM = useMutation({
    mutationFn: wsDisconnectPlayground,
    onSuccess: (data) => {
      closeWsStream();
      setWsLastResponse(data);
      appendWsLog(statusToLogEntry(data, "ws_disconnect"));
      void refreshEventLog();
      setWsStatusHint("Socket disconnected and subscriptions cleared.");
    },
    onError: (e) => {
      setWsStatusHint(e instanceof Error ? e.message : "Disconnect failed");
    },
  });
  const wsSubscribeM = useMutation({
    mutationFn: () =>
      wsSubscribePlayground(buildWsSubscribeParams(wsForm, subscriptionHolder, wsSubscribeMode)),
    onSuccess: (data) => {
      setWsLastResponse(data);
      appendWsLog(statusToLogEntry(data, "subscribe_feeds"));
      void refreshEventLog();
      if (data.ok) {
        const subs = data.active_subscriptions ?? 0;
        setWsStatusHint(
          `Subscribed · ${subs} active subscription${subs === 1 ? "" : "s"} on ICICI socket.`,
        );
      } else {
        const err =
          typeof data.response === "string"
            ? data.response
            : data.last_error || "Subscribe failed (see command log).";
        setWsStatusHint(`Subscribe failed: ${err}`);
      }
    },
    onError: (e) => {
      setWsStatusHint(e instanceof Error ? e.message : "Subscribe failed");
    },
  });

  const startWsStream = () => {
    wsStreamRef.current?.close();
    setWsTicks([]);
    tickLogSeqRef.current = 0;
    setWsStatusHint("Opening tick stream…");
    const es = new EventSource(wsStreamUrl(), { withCredentials: true });
    wsStreamRef.current = es;

    es.addEventListener("ws_status", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as BreezeApiWsStatus;
        setWsStreamOpen(Boolean(payload.connected));
        setWsStatusHint(
          payload.connected
            ? "Tick stream open · ICICI socket connected."
            : "Tick stream open · ICICI socket not connected.",
        );
      } catch {
        setWsStatusHint("Invalid ws_status payload from server.");
      }
    });
    es.addEventListener("ws_command", (event) => {
      try {
        const entry = JSON.parse((event as MessageEvent).data) as BreezeApiWsEventLogEntry;
        appendWsLog(entry);
        void refreshEventLog();
      } catch {
        /* ignore malformed ws_command */
      }
    });
    es.addEventListener("ws_error", (event) => {
      const msgEvent = event as MessageEvent;
      if (!msgEvent.data) return;
      try {
        const payload = JSON.parse(msgEvent.data) as BreezeApiWsStatus;
        setWsStreamOpen(false);
        setWsStatusHint("Tick stream reported an error (see command log).");
        appendWsLog({
          id: Date.now(),
          ts: Date.now() / 1000,
          step: "sse_stream_error",
          icici_command: { sdk_method: "", sdk_args: {}, side_effects: [] },
          icici_response: payload,
          ok: false,
        });
      } catch {
        setWsStatusHint("Invalid ws_error payload from server.");
      }
    });
    es.addEventListener("ws_tick", (event) => {
      const data = (event as MessageEvent).data;
      setWsTicks((prev) => [data, ...prev].slice(0, 40));
      tickLogSeqRef.current += 1;
      appendWsLog(tickPayloadToLogEntry(data, tickLogSeqRef.current));
    });
    es.addEventListener("ws_ping", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as BreezeApiWsStatus & { ts?: number };
        setWsStreamOpen(Boolean(payload.connected));
      } catch {
        /* ignore malformed ping */
      }
    });
    es.onerror = () => {
      setWsStreamOpen(false);
      setWsStatusHint("Tick stream transport error (login expired or network issue).");
      es.close();
      if (wsStreamRef.current === es) wsStreamRef.current = null;
    };

    return es;
  };

  useEffect(
    () => () => {
      closeWsStream();
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
    },
    [closeWsStream],
  );

  return (
    <AppShell>
      <BreezeApiRiskGateDialog
        open={showGate}
        pending={ackM.isPending}
        onAccept={() => ackM.mutate()}
      />

      <section
        className={`app-card space-y-4 p-4 ${showGate ? "pointer-events-none select-none opacity-40" : ""}`}
        aria-hidden={showGate}
      >
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>

        <header className="space-y-1">
          <h2 className="text-xl app-text-heading">Breeze API Playground</h2>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Invoke ICICI Breeze REST APIs from the official breeze-connect client. Responses are
            live from your broker session.{" "}
            <HelpLink topicId="breeze-api-playground" className="text-sm">
              Safety notes
            </HelpLink>
          </p>
        </header>

        {catalogQ.isLoading && (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading API catalog…</p>
        )}

        {catalogQ.isError && (
          <p className="text-sm text-red-700 dark:text-red-300">
            Failed to load catalog. Ensure you are logged in.
          </p>
        )}

        {selected && (
          <>
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-3">
                <BreezeApiMethodPicker
                  groups={groupedOptions}
                  selectedMethod={selectedMethod}
                  onSelect={setSelectedMethod}
                />

                {selected.description ? (
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">{selected.description}</p>
                ) : null}
                {selected.notes ? (
                  <p className="rounded-md border border-amber-200 bg-amber-50/80 px-2 py-1.5 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200">
                    {selected.notes}
                  </p>
                ) : null}

                {(selected.risk_level === "trade" ||
                  selected.risk_level === "funds" ||
                  selected.risk_level === "gtt") && (
                  <p className="rounded-md border border-red-300 bg-red-50 px-2 py-1.5 text-xs font-medium text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
                    This API can modify live orders, funds, or GTT triggers. Double-check every
                    parameter before firing.
                  </p>
                )}

                <div className="space-y-3 rounded-md border border-zinc-200/80 p-3 dark:border-zinc-800">
                  <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    Parameters
                  </div>
                  {selected.params.length === 0 ? (
                    <p className="text-xs text-zinc-500 dark:text-zinc-400">No parameters required.</p>
                  ) : (
                    selected.params.map((p) => (
                      <label key={p.name} className="block text-xs">
                        <span className="font-medium text-zinc-700 dark:text-zinc-300">
                          {p.label}
                        </span>
                        {p.type === "json" ? (
                          <textarea
                            className={`${inputCls} min-h-[120px] font-mono text-xs`}
                            value={paramValues[p.name] ?? ""}
                            placeholder={p.placeholder}
                            onChange={(e) =>
                              setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))
                            }
                          />
                        ) : (
                          <input
                            type="text"
                            className={inputCls}
                            value={paramValues[p.name] ?? ""}
                            placeholder={p.placeholder}
                            onChange={(e) =>
                              setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))
                            }
                          />
                        )}
                        {p.help ? (
                          <span className="mt-0.5 block text-zinc-500 dark:text-zinc-500">{p.help}</span>
                        ) : null}
                      </label>
                    ))
                  )}
                  <label className="block text-xs">
                    <span className="font-medium text-zinc-700 dark:text-zinc-300">
                      Additional parameters (JSON)
                    </span>
                    <textarea
                      className={`${inputCls} min-h-[80px] font-mono text-xs`}
                      value={extraParamsJson}
                      placeholder='{"custom_key": "value"}'
                      onChange={(e) => setExtraParamsJson(e.target.value)}
                    />
                    <span className="mt-0.5 block text-zinc-500 dark:text-zinc-500">
                      Optional keys not in the catalog. Form fields override on conflict.
                    </span>
                  </label>
                </div>

                <button
                  type="button"
                  className="app-btn-primary"
                  disabled={invokeM.isPending || !riskQ.data?.accepted}
                  onClick={onFire}
                >
                  <AsyncLabelSpan
                    busy={invokeM.isPending}
                    idleLabel="Fire API"
                    busyLabel="Calling ICICI…"
                  />
                </button>
              </div>

              <div className="flex min-h-[280px] flex-col">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    Response
                  </span>
                  {responseText ? (
                    <button
                      type="button"
                      className="app-btn-outline text-xs"
                      onClick={copyResponse}
                      title={
                        responseCopyState === "failed"
                          ? "Copy failed — select the response text and copy manually"
                          : undefined
                      }
                    >
                      {responseCopyState === "copied"
                        ? "Copied!"
                        : responseCopyState === "failed"
                          ? "Copy failed"
                          : "Copy"}
                    </button>
                  ) : null}
                </div>
                {lastResponse && (
                  <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                    {lastResponse.method} · {lastResponse.duration_ms} ms
                    {lastResponse.ok === false ? " · failed" : ""}
                  </p>
                )}
                <pre className="mt-2 max-h-[50vh] min-h-[200px] flex-1 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900/80 dark:text-zinc-100">
                  {responseText || "Response will appear here after you fire an API."}
                </pre>
              </div>
            </div>
          </>
        )}
      </section>

      <section
        className={`app-card mt-4 space-y-4 p-4 ${showGate ? "pointer-events-none select-none opacity-40" : ""}`}
      >
        <header className="space-y-1">
          <h3 className="text-lg font-semibold app-text-heading">WebSocket (market hours)</h3>
          <p className="text-sm app-text-muted">
            Connect to Breeze exchange-quote stream for NFO/BFO options. Subscribe to a contract, then
            watch ticks below.
          </p>
        </header>
        <p
          className={`rounded-md border px-3 py-2 text-xs ${
            wsStreamOpen
              ? "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200"
              : "border-zinc-200 bg-zinc-50 text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/80 dark:text-zinc-300"
          }`}
        >
          {wsStatusHint}
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="app-btn-outline" onClick={() => wsConnectM.mutate()}>
            Connect
          </button>
          <button
            type="button"
            className="app-btn-outline"
            disabled={wsReleaseM.isPending}
            onClick={() => wsReleaseM.mutate()}
          >
            Release subscriptions
          </button>
          <button
            type="button"
            className="app-btn-outline"
            disabled={wsDisconnectM.isPending}
            onClick={() => wsDisconnectM.mutate()}
          >
            Disconnect socket
          </button>
          <button type="button" className="app-btn-outline" onClick={() => startWsStream()}>
            Start tick stream
          </button>
        </div>
        <p className="text-xs text-amber-800 dark:text-amber-200">
          Frequent connect/disconnect cycles may be treated as connection thrashing by ICICI.
          Prefer <span className="font-medium">Release subscriptions</span> when you only want to
          stop ticks; use <span className="font-medium">Disconnect socket</span> only when you need
          to tear down the WebSocket entirely.
        </p>
        <p className="text-xs app-text-muted">
          WebSocket ticks only arrive during NSE/BSE market hours.
        </p>
        <label className="block text-xs">
          <span className="font-medium text-zinc-700 dark:text-zinc-300">Subscribe mode</span>
          <select
            className={inputCls}
            value={wsSubscribeMode}
            onChange={(e) => setWsSubscribeMode(e.target.value as WsSubscribeMode)}
          >
            {WS_SUBSCRIBE_MODE_OPTIONS.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <p className="text-xs app-text-muted">
          Fields depend on subscription type per ICICI docs. Only filled fields are sent.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {WS_SUBSCRIBE_MODES[wsSubscribeMode].map((key) => (
            <label key={key} className="block text-xs">
              <span className="font-medium">{key}</span>
              <input
                className={inputCls}
                value={wsForm[key] ?? ""}
                placeholder={WS_FIELD_PLACEHOLDERS[key] ?? ""}
                onChange={(e) => setWsForm((p) => ({ ...p, [key]: e.target.value }))}
              />
            </label>
          ))}
        </div>
        <button
          type="button"
          className="app-btn-primary"
          disabled={wsSubscribeM.isPending}
          onClick={() => wsSubscribeM.mutate()}
        >
          Subscribe
        </button>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="flex min-h-[160px] flex-col">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                ICICI command log
              </span>
              {wsCommandLogText ? (
                <button
                  type="button"
                  className="app-btn-outline text-xs"
                  onClick={copyWsLog}
                  title={
                    wsLogCopyState === "failed"
                      ? "Copy failed — select the log text and copy manually"
                      : undefined
                  }
                >
                  {wsLogCopyState === "copied"
                    ? "Copied!"
                    : wsLogCopyState === "failed"
                      ? "Copy failed"
                      : "Copy"}
                </button>
              ) : null}
            </div>
            <pre className="mt-2 max-h-56 min-h-[140px] flex-1 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900/80 dark:text-zinc-100">
              {wsCommandLogText ||
                "Commands and ICICI responses appear here after Connect, Subscribe, Disconnect, or Start tick stream."}
            </pre>
          </div>
          <div className="flex min-h-[160px] flex-col">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Latest operation
              </span>
              {wsResponseText ? (
                <button
                  type="button"
                  className="app-btn-outline text-xs"
                  onClick={copyWsResponse}
                  title={
                    wsResponseCopyState === "failed"
                      ? "Copy failed — select the response text and copy manually"
                      : undefined
                  }
                >
                  {wsResponseCopyState === "copied"
                    ? "Copied!"
                    : wsResponseCopyState === "failed"
                      ? "Copy failed"
                      : "Copy"}
                </button>
              ) : null}
            </div>
            <pre
              className={`mt-2 max-h-56 min-h-[140px] flex-1 overflow-auto rounded-md border p-3 font-mono text-xs ${
                wsResponseIsError
                  ? "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
                  : "border-zinc-200 bg-zinc-50 text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900/80 dark:text-zinc-100"
              }`}
            >
              {wsResponseText ||
                "ICICI response will appear here after Connect, Subscribe, or Disconnect."}
            </pre>
          </div>
        </div>
        <div>
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Ticks</span>
          <pre className="mt-2 max-h-48 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs dark:border-zinc-800 dark:bg-zinc-900/80">
            {wsTicks.length
              ? wsTicks.map((tick) => formatTickForDisplay(tick)).join("\n\n")
              : "Ticks appear after connect, subscribe, and start stream (during market hours)."}
          </pre>
        </div>
      </section>
    </AppShell>
  );
}
