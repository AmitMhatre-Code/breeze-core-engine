"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { HelpLink } from "@/components/help/HelpLink";
import { BreezeApiMethodPicker } from "@/components/settings/BreezeApiMethodPicker";
import { BreezeApiRiskGateDialog } from "@/components/settings/BreezeApiRiskGateDialog";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { WsSubscribeModePicker } from "@/components/settings/WsSubscribeModePicker";
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
  WS_SUBSCRIBE_MODES,
  type WsSubscribeMode,
} from "@/lib/breeze-api-playground-ws-subscribe";
import { useWsSubscriptionHolder } from "@/lib/use-ws-subscription-holder";

const RISK_ORDER: BreezeApiCatalogEntry["risk_level"][] = ["read", "funds", "trade", "gtt"];

const WS_LOG_LIMIT = 50;

function WarningTriangleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 9v4M12 17h.01" />
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    </svg>
  );
}

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
  return [...byId.values()].sort((a, b) => b.ts - a.ts).slice(0, WS_LOG_LIMIT);
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

type TickShape = {
  signature: string;
  keys: string[];
  count: number;
  example: string;
  firstTs: number;
};

/** Distinguishes tick "types" (quote tick vs order notification vs OHLC tick, …) by their key set. */
function tickShapeKeys(rawJson: string): string[] | null {
  try {
    const parsed = JSON.parse(rawJson) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return Object.keys(parsed as Record<string, unknown>).sort();
    }
    return null;
  } catch {
    return null;
  }
}

export function ApiPlaygroundScreen() {
  const subscriptionHolder = useWsSubscriptionHolder();
  const qc = useQueryClient();
  const [selectedMethod, setSelectedMethod] = useState("");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [extraParamsJson, setExtraParamsJson] = useState("");
  const [lastResponse, setLastResponse] = useState<BreezeApiInvokeResponse | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);
  const [wsTicks, setWsTicks] = useState<string[]>([]);
  const [wsTickShapes, setWsTickShapes] = useState<TickShape[]>([]);
  const [wsPaused, setWsPaused] = useState(false);
  const wsPausedRef = useRef(false);
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
  const [wsResponseCopyState, setWsResponseCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [wsLogCopyState, setWsLogCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [wsShapesCopyState, setWsShapesCopyState] = useState<"idle" | "copied" | "failed">("idle");
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
  const sortedTickShapes = useMemo(
    () => [...wsTickShapes].sort((a, b) => b.count - a.count),
    [wsTickShapes],
  );
  const wsTickShapesText = sortedTickShapes
    .map(
      (shape) =>
        `--- ${shape.count}x · ${shape.keys.length} keys ---\nkeys: ${JSON.stringify(shape.keys)}\n${formatTickForDisplay(shape.example)}`,
    )
    .join("\n\n---\n\n");
  const showGate = riskQ.isSuccess && !riskQ.data?.accepted;

  useEffect(() => {
    if (!riskQ.data?.accepted) return;
    void refreshEventLog();
  }, [riskQ.data?.accepted, refreshEventLog]);

  const flashCopyState = useCallback(
    (target: "response" | "ws" | "wsLog" | "wsShapes", result: ReturnType<typeof copyTextToClipboard>) => {
      const next = result.ok ? "copied" : "failed";
      const setters = {
        response: setResponseCopyState,
        ws: setWsResponseCopyState,
        wsLog: setWsLogCopyState,
        wsShapes: setWsShapesCopyState,
      } as const;
      setters[target](next);
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
      copyResetRef.current = setTimeout(() => {
        setters[target]("idle");
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

  const copyWsTickShapes = () => {
    flashCopyState("wsShapes", copyTextToClipboard(wsTickShapesText));
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
        setWsStatusHint(`Subscribed · ${subs} active subscription${subs === 1 ? "" : "s"} on ICICI socket.`);
      } else {
        const err =
          typeof data.response === "string" ? data.response : data.last_error || "Subscribe failed (see command log).";
        setWsStatusHint(`Subscribe failed: ${err}`);
      }
    },
    onError: (e) => {
      setWsStatusHint(e instanceof Error ? e.message : "Subscribe failed");
    },
  });

  const toggleWsPause = () => {
    setWsPaused((prev) => {
      wsPausedRef.current = !prev;
      return !prev;
    });
  };

  const clearWsTickShapes = () => setWsTickShapes([]);

  const startWsStream = () => {
    wsStreamRef.current?.close();
    setWsTicks([]);
    setWsTickShapes([]);
    setWsPaused(false);
    wsPausedRef.current = false;
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
      if (wsPausedRef.current) return;
      setWsTicks((prev) => [data, ...prev].slice(0, 40));
      tickLogSeqRef.current += 1;
      appendWsLog(tickPayloadToLogEntry(data, tickLogSeqRef.current));

      const keys = tickShapeKeys(data);
      if (keys) {
        const signature = keys.join("|");
        setWsTickShapes((prev) => {
          const idx = prev.findIndex((s) => s.signature === signature);
          if (idx === -1) {
            return [...prev, { signature, keys, count: 1, example: data, firstTs: Date.now() / 1000 }];
          }
          const next = [...prev];
          next[idx] = { ...next[idx], count: next[idx].count + 1 };
          return next;
        });
      }
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
    <div>
      <BreezeApiRiskGateDialog open={showGate} pending={ackM.isPending} onAccept={() => ackM.mutate()} />

      <SettingsScreenHeader
        icon={<WarningTriangleIcon />}
        danger
        title="Breeze API Playground"
        description={
          <>
            Invoke ICICI Breeze REST APIs from the official breeze-connect client. Responses are
            live from your broker session.{" "}
            <HelpLink topicId="breeze-api-playground" className="text-xs">
              Safety notes
            </HelpLink>
          </>
        }
      />

      <div className="space-y-4">
        <section
          className={`app-card space-y-4 border-down/35 p-5 ${showGate ? "pointer-events-none select-none opacity-40" : ""}`}
          aria-hidden={showGate}
        >
          {catalogQ.isLoading && <p className="text-sm text-muted">Loading API catalog…</p>}

          {catalogQ.isError && (
            <p className="app-alert-error text-xs">Failed to load catalog. Ensure you are logged in.</p>
          )}

          {selected && (
            <div className="grid min-w-0 gap-4 lg:grid-cols-2">
              <div className="min-w-0 space-y-3">
                <BreezeApiMethodPicker
                  groups={groupedOptions}
                  selectedMethod={selectedMethod}
                  onSelect={setSelectedMethod}
                />

                {selected.description ? (
                  <p className="text-xs text-muted">{selected.description}</p>
                ) : null}
                {selected.notes ? (
                  <p className="rounded-[8px] border border-amber-accent/40 bg-amber-tint px-2.5 py-1.5 text-xs text-amber-accent">
                    {selected.notes}
                  </p>
                ) : null}

                {(selected.risk_level === "trade" ||
                  selected.risk_level === "funds" ||
                  selected.risk_level === "gtt") && (
                  <p className="rounded-[8px] border border-down/40 bg-down-tint px-2.5 py-1.5 text-xs text-down">
                    This API can modify live orders, funds, or GTT triggers. Double-check every
                    parameter before firing.
                  </p>
                )}

                <div className="app-card-muted space-y-3 p-4">
                  <h4 className="app-text-heading">Parameters</h4>
                  <p className="rounded-[8px] border border-amber-accent/40 bg-amber-tint px-2.5 py-1.5 text-xs text-amber-accent">
                    Values are interpreted as Python/JSON literals where possible (lists,
                    booleans, numbers). Be careful with quotes — for multiple stock tokens use{" "}
                    <code className="font-mono">[&apos;4.1!44684&apos;,&apos;4.1!44734&apos;]</code>{" "}
                    or{" "}
                    <code className="font-mono">[&quot;4.1!44684&quot;,&quot;4.1!44734&quot;]</code>; do not
                    wrap the whole expression in extra quotes. See the{" "}
                    <a
                      href="https://pypi.org/project/breeze-connect/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="app-link"
                    >
                      breeze-connect documentation
                    </a>{" "}
                    for API parameter formats.
                  </p>
                  {selected.params.length === 0 ? (
                    <p className="app-text-muted">No parameters required.</p>
                  ) : (
                    selected.params.map((p) => (
                      <label key={p.name} className="block space-y-1.5">
                        <span className="app-text-muted">{p.label}</span>
                        {p.type === "json" ? (
                          <textarea
                            className="app-input min-h-[120px] font-mono text-xs"
                            value={paramValues[p.name] ?? ""}
                            placeholder={p.placeholder}
                            onChange={(e) =>
                              setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))
                            }
                          />
                        ) : (
                          <input
                            type="text"
                            className="app-input"
                            value={paramValues[p.name] ?? ""}
                            placeholder={p.placeholder}
                            onChange={(e) =>
                              setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))
                            }
                          />
                        )}
                        {p.help ? <span className="app-text-muted block">{p.help}</span> : null}
                      </label>
                    ))
                  )}
                  <label className="block space-y-1.5">
                    <span className="app-text-muted">Additional parameters (JSON)</span>
                    <textarea
                      className="app-input min-h-[80px] font-mono text-xs"
                      value={extraParamsJson}
                      placeholder='{"custom_key": "value"}'
                      onChange={(e) => setExtraParamsJson(e.target.value)}
                    />
                    <span className="app-text-muted block">
                      Optional keys not in the catalog. Form fields override on conflict.
                    </span>
                  </label>
                </div>

                <button
                  type="button"
                  className="inline-flex items-center justify-center rounded-lg bg-down-btn px-3.5 py-1.5 text-sm font-bold text-white transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-down/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={invokeM.isPending || !riskQ.data?.accepted}
                  onClick={onFire}
                >
                  <AsyncLabelSpan busy={invokeM.isPending} idleLabel="Fire API" busyLabel="Calling ICICI…" />
                </button>
              </div>

              <div className="flex min-h-[280px] min-w-0 flex-col">
                <div className="flex items-center justify-between gap-2">
                  <h4 className="app-text-heading">Response</h4>
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
                  <p className="mt-1 text-xs text-muted">
                    {lastResponse.method} · {lastResponse.duration_ms} ms
                    {lastResponse.ok === false ? " · failed" : ""}
                  </p>
                )}
                <pre className="app-pre mt-2 max-h-[50vh] min-h-[200px] min-w-0 flex-1 text-xs">
                  {responseText || "Response will appear here after you fire an API."}
                </pre>
              </div>
            </div>
          )}
        </section>

        <section
          className={`app-card space-y-4 p-5 ${showGate ? "pointer-events-none select-none opacity-40" : ""}`}
        >
          <header className="space-y-1">
            <h3 className="app-text-title">WebSocket (market hours)</h3>
            <p className="app-text-muted max-w-prose text-sm">
              Connect to Breeze exchange-quote stream for NFO/BFO options. Subscribe to a contract, then
              watch ticks below.
            </p>
          </header>
          <p
            className={`rounded-[8px] border px-3 py-2 text-xs ${
              wsStreamOpen ? "border-up/35 bg-up-tint text-up" : "border-border bg-panel2 text-muted"
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
            <button
              type="button"
              className="app-btn-outline"
              disabled={!wsStreamOpen && !wsPaused}
              onClick={toggleWsPause}
            >
              {wsPaused ? "Resume capture" : "Pause capture"}
            </button>
          </div>
          <p className="text-xs text-amber-accent">
            Frequent connect/disconnect cycles may be treated as connection thrashing by ICICI.
            Prefer <span className="font-medium">Release subscriptions</span> when you only want to
            stop ticks; use <span className="font-medium">Disconnect socket</span> only when you need
            to tear down the WebSocket entirely.
          </p>
          <p className="text-xs app-text-muted">WebSocket ticks only arrive during NSE/BSE market hours.</p>
          <WsSubscribeModePicker value={wsSubscribeMode} onChange={setWsSubscribeMode} />
          <p className="rounded-[8px] border border-amber-accent/40 bg-amber-tint px-2.5 py-1.5 text-xs text-amber-accent">
            Values are interpreted as Python/JSON literals where possible (lists, booleans,
            numbers). Be careful with quotes — for multiple stock tokens use{" "}
            <code className="font-mono">[&apos;4.1!44684&apos;,&apos;4.1!44734&apos;]</code> or{" "}
            <code className="font-mono">[&quot;4.1!44684&quot;,&quot;4.1!44734&quot;]</code>; do not
            wrap the whole expression in extra quotes. See the{" "}
            <a
              href="https://pypi.org/project/breeze-connect/"
              target="_blank"
              rel="noopener noreferrer"
              className="app-link"
            >
              breeze-connect documentation
            </a>{" "}
            for API parameter formats.
          </p>
          <p className="app-text-muted">Fields depend on subscription type per ICICI docs. Only filled fields are sent.</p>
          <div className="app-card-muted grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {WS_SUBSCRIBE_MODES[wsSubscribeMode].map((key) => (
              <label key={key} className="block space-y-1.5">
                <span className="app-text-muted font-mono text-heading uppercase tracking-wide">{key}</span>
                <input
                  className="app-input font-mono text-xs"
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
          <div className="grid min-w-0 gap-4 lg:grid-cols-2">
            <div className="flex min-h-[160px] min-w-0 flex-col">
              <div className="flex items-center justify-between gap-2">
                <h4 className="app-text-heading">ICICI command log</h4>
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
                    {wsLogCopyState === "copied" ? "Copied!" : wsLogCopyState === "failed" ? "Copy failed" : "Copy"}
                  </button>
                ) : null}
              </div>
              <pre className="app-pre mt-2 max-h-56 min-h-[140px] min-w-0 flex-1 text-xs">
                {wsCommandLogText ||
                  "Commands and ICICI responses appear here after Connect, Subscribe, Disconnect, or Start tick stream."}
              </pre>
            </div>
            <div className="flex min-h-[160px] min-w-0 flex-col">
              <div className="flex items-center justify-between gap-2">
                <h4 className="app-text-heading">Latest operation</h4>
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
                className={[
                  "app-pre mt-2 max-h-56 min-h-[140px] min-w-0 flex-1 text-xs",
                  wsResponseIsError ? "border-down/40 bg-down-tint text-down" : "",
                ].join(" ")}
              >
                {wsResponseText || "ICICI response will appear here after Connect, Subscribe, or Disconnect."}
              </pre>
            </div>
          </div>
          <div className="min-w-0">
            <div className="flex items-center justify-between gap-2">
              <h4 className="app-text-heading">Ticks {wsPaused ? "(paused)" : ""}</h4>
            </div>
            <pre className="app-pre mt-2 max-h-48 min-w-0 text-xs">
              {wsTicks.length
                ? wsTicks.map((tick) => formatTickForDisplay(tick)).join("\n\n")
                : "Ticks appear after connect, subscribe, and start stream (during market hours)."}
            </pre>
          </div>

          <div className="min-w-0">
            <div className="flex items-center justify-between gap-2">
              <h4 className="app-text-heading">
                Unique tick shapes {wsTickShapes.length ? `(${wsTickShapes.length})` : ""}
              </h4>
              {wsTickShapes.length ? (
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="app-btn-outline text-xs"
                    onClick={copyWsTickShapes}
                    title={
                      wsShapesCopyState === "failed"
                        ? "Copy failed — select the text and copy manually"
                        : undefined
                    }
                  >
                    {wsShapesCopyState === "copied"
                      ? "Copied!"
                      : wsShapesCopyState === "failed"
                        ? "Copy failed"
                        : "Copy"}
                  </button>
                  <button type="button" className="app-btn-outline text-xs" onClick={clearWsTickShapes}>
                    Clear
                  </button>
                </div>
              ) : null}
            </div>
            <p className="app-text-muted mt-1 text-xs">
              One entry per distinct key set seen since Start/Clear — e.g. quote ticks vs order
              notifications land as separate shapes here even though both arrive as
              &quot;ws_tick&quot;.
            </p>
            {wsTickShapes.length ? (
              <div className="mt-2 space-y-1.5">
                {sortedTickShapes.map((shape) => (
                  <details key={shape.signature} className="app-card-muted rounded-[8px] px-3 py-2">
                    <summary className="cursor-pointer text-xs font-medium">
                      {shape.count}× · {shape.keys.length} keys · {shape.keys.slice(0, 6).join(", ")}
                      {shape.keys.length > 6 ? ", …" : ""}
                    </summary>
                    <pre className="app-pre mt-2 max-h-56 min-w-0 text-xs">
                      {formatTickForDisplay(shape.example)}
                    </pre>
                  </details>
                ))}
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
