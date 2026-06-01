"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
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
  type BreezeApiCatalogEntry,
  type BreezeApiInvokeResponse,
} from "@/lib/breeze-api-tester";

const inputCls =
  "mt-1 w-full rounded-md border border-zinc-300/80 bg-white/95 px-3 py-2 text-sm text-zinc-900 shadow-sm outline-none transition-all hover:border-zinc-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus:border-blue-400 dark:focus:ring-blue-400/20";

const RISK_ORDER: BreezeApiCatalogEntry["risk_level"][] = ["read", "funds", "trade", "gtt"];

function formatResponse(payload: BreezeApiInvokeResponse | null, invokeError: string | null): string {
  if (invokeError) return invokeError;
  if (!payload) return "";
  if (payload.error) {
    return JSON.stringify({ error: payload.error, response: payload.response }, null, 2);
  }
  try {
    return JSON.stringify(payload.response ?? payload, null, 2);
  } catch {
    return String(payload.response ?? payload);
  }
}

export default function BreezeApiPlaygroundPage() {
  const qc = useQueryClient();
  const [selectedMethod, setSelectedMethod] = useState("");
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [lastResponse, setLastResponse] = useState<BreezeApiInvokeResponse | null>(null);
  const [invokeError, setInvokeError] = useState<string | null>(null);

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
      const v = (paramValues[p.name] ?? "").trim();
      if (!v && !p.required) continue;
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
  const showGate = riskQ.isSuccess && !riskQ.data?.accepted;

  const copyResponse = async () => {
    if (!responseText) return;
    try {
      await navigator.clipboard.writeText(responseText);
    } catch {
      /* ignore */
    }
  };

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
    </AppShell>
  );
}
