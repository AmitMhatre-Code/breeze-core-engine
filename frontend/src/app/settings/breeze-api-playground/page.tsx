"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
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
  wsStreamUrl,
  wsSubscribePlayground,
  type BreezeApiCatalogEntry,
  type BreezeApiInvokeResponse,
  type BreezeApiWsStatus,
} from "@/lib/breeze-api-tester";

const inputCls =
  "mt-1 w-full rounded-md border border-zinc-300/80 bg-white/95 px-3 py-2 text-sm text-zinc-900 shadow-sm outline-none transition-all hover:border-zinc-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus:border-blue-400 dark:focus:ring-blue-400/20";

const RISK_ORDER: BreezeApiCatalogEntry["risk_level"][] = ["read", "funds", "trade", "gtt"];

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

export default function BreezeApiPlaygroundPage() {
  const qc = useQueryClient();
  const [selectedMethod, setSelectedMethod] = useState("");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [lastResponse, setLastResponse] = useState<BreezeApiInvokeResponse | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);
  const [wsTicks, setWsTicks] = useState<string[]>([]);
  const [wsLastResponse, setWsLastResponse] = useState<BreezeApiWsStatus | null>(null);
  const [wsStatusHint, setWsStatusHint] = useState(
    "Not connected. Click Connect, fill contract fields, Subscribe, then Start tick stream.",
  );
  const [wsStreamOpen, setWsStreamOpen] = useState(false);
  const wsStreamRef = useRef<EventSource | null>(null);
  const [wsForm, setWsForm] = useState({
    exchange_code: "NFO",
    stock_code: "NIFTY",
    expiry_date: "",
    strike_price: "",
    right: "call",
  });

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
    for (const p of selected.params) {
      out[p.name] = paramValues[p.name] ?? "";
    }
    return out;
  }, [selected, paramValues]);

  const onFire = () => {
    if (!selected) return;
    setInvokeError(null);
    invokeM.mutate({ method: selected.method, params: buildParamsForInvoke() });
  };

  const responseText = formatResponse(lastResponse, invokeError);
  const wsResponseText = formatWsApiResponse(wsLastResponse);
  const wsResponseIsError = wsLastResponse?.ok === false;
  const showGate = riskQ.isSuccess && !riskQ.data?.accepted;

  const copyResponse = async () => {
    if (!responseText) return;
    try {
      await navigator.clipboard.writeText(responseText);
    } catch {
      /* ignore */
    }
  };

  const copyWsResponse = async () => {
    if (!wsResponseText) return;
    try {
      await navigator.clipboard.writeText(wsResponseText);
    } catch {
      /* ignore */
    }
  };

  const wsConnectM = useMutation({
    mutationFn: wsConnectPlayground,
    onSuccess: (data) => {
      setWsLastResponse(data);
      setWsStatusHint(
        data.connected ? "ICICI socket connected." : "ICICI socket not connected.",
      );
    },
    onError: (e) => {
      setWsStatusHint(e instanceof Error ? e.message : "Connect failed");
    },
  });
  const wsDisconnectM = useMutation({
    mutationFn: wsDisconnectPlayground,
    onSuccess: (data) => {
      setWsStreamOpen(false);
      setWsLastResponse(data);
      setWsStatusHint("Disconnected.");
    },
    onError: (e) => {
      setWsStatusHint(e instanceof Error ? e.message : "Disconnect failed");
    },
  });
  const wsSubscribeM = useMutation({
    mutationFn: () => wsSubscribePlayground(wsForm),
    onSuccess: (data) => {
      setWsLastResponse(data);
      setWsStatusHint(
        data.connected ? "ICICI socket connected." : "ICICI socket not connected.",
      );
    },
    onError: (e) => {
      setWsStatusHint(e instanceof Error ? e.message : "Subscribe failed");
    },
  });

  const startWsStream = () => {
    wsStreamRef.current?.close();
    setWsTicks([]);
    setWsStatusHint("Opening tick stream…");
    const es = new EventSource(wsStreamUrl(), { withCredentials: true });
    wsStreamRef.current = es;

    es.addEventListener("ws_status", (event) => {
      try {
        const payload = JSON.parse((event as MessageEvent).data) as BreezeApiWsStatus;
        setWsLastResponse(payload);
        setWsStreamOpen(Boolean(payload.connected));
        setWsStatusHint(
          payload.connected ? "Tick stream open · ICICI socket connected." : "Tick stream open.",
        );
      } catch {
        setWsStatusHint("Invalid ws_status payload from server.");
      }
    });
    es.addEventListener("ws_error", (event) => {
      const msgEvent = event as MessageEvent;
      if (!msgEvent.data) return;
      try {
        const payload = JSON.parse(msgEvent.data) as BreezeApiWsStatus;
        setWsLastResponse(payload);
        setWsStreamOpen(false);
        setWsStatusHint("Tick stream reported an error (see Response).");
      } catch {
        setWsStatusHint("Invalid ws_error payload from server.");
      }
    });
    es.addEventListener("ws_tick", (event) => {
      setWsTicks((prev) => [(event as MessageEvent).data, ...prev].slice(0, 40));
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

  useEffect(() => () => wsStreamRef.current?.close(), []);

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
            live from your broker session.
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
                          {p.required ? " *" : ""}
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
                      onClick={() => void copyResponse()}
                    >
                      Copy
                    </button>
                  ) : null}
                </div>
                {lastResponse && (
                  <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                    {lastResponse.method} · {lastResponse.duration_ms} ms
                    {lastResponse.ok === false ? " · non-200 Status in payload" : ""}
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
          <button type="button" className="app-btn-outline" onClick={() => wsDisconnectM.mutate()}>
            Disconnect
          </button>
          <button type="button" className="app-btn-outline" onClick={() => startWsStream()}>
            Start tick stream
          </button>
        </div>
        <p className="text-xs app-text-muted">
          expiry_date format: <span className="font-mono">26-Jun-2026</span>. WebSocket ticks only arrive
          during NSE/BSE market hours.
        </p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {(["exchange_code", "stock_code", "expiry_date", "strike_price", "right"] as const).map((k) => (
            <label key={k} className="block text-xs">
              <span className="font-medium">{k}</span>
              <input
                className={inputCls}
                value={wsForm[k]}
                onChange={(e) => setWsForm((p) => ({ ...p, [k]: e.target.value }))}
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
        <div className="flex min-h-[160px] flex-col">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Response</span>
            {wsResponseText ? (
              <button
                type="button"
                className="app-btn-outline text-xs"
                onClick={() => void copyWsResponse()}
              >
                Copy
              </button>
            ) : null}
          </div>
          <pre
            className={`mt-2 max-h-48 min-h-[120px] flex-1 overflow-auto rounded-md border p-3 font-mono text-xs ${
              wsResponseIsError
                ? "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200"
                : "border-zinc-200 bg-zinc-50 text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900/80 dark:text-zinc-100"
            }`}
          >
            {wsResponseText ||
              "ICICI response will appear here after Connect, Subscribe, or Disconnect."}
          </pre>
        </div>
        <pre className="max-h-48 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs dark:border-zinc-800 dark:bg-zinc-900/80">
          {wsTicks.length ? wsTicks.join("\n\n") : "Ticks appear after connect, subscribe, and start stream."}
        </pre>
      </section>
    </AppShell>
  );
}
