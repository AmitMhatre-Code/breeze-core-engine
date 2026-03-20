// Client component so auth cookies are included with browser fetch.
"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";

type IciciApiResponse = {
  Status: number;
  Error?: string;
  Success?: {
    positions?: Array<{
      stock_code: string;
      quantity: number;
      pnl: number;
    }>;
  };
};

export default function PortfolioPage() {
  const q = useQuery({
    queryKey: ["portfolio", "positions"],
    queryFn: async () => apiClient.get<IciciApiResponse>("/portfolio/data"),
  });

  const data = q.data;
  const positions = data?.Success?.positions ?? [];

  return (
    <AppShell>
      {q.isLoading ? (
        <div className="app-card p-4">Loading portfolio...</div>
      ) : q.error ? (
        <div className="app-alert-error">
          Unable to load portfolio:{" "}
          {q.error instanceof Error ? q.error.message : "Unknown error"}
        </div>
      ) : (
        <section className="app-card space-y-3 p-4">
          <header className="flex items-center justify-between">
            <h2 className="app-text-heading">Portfolio</h2>
            {data && data.Status !== 200 && (
              <span className="text-xs text-red-600 dark:text-red-400">
                {data.Error || "Unable to load portfolio"}
              </span>
            )}
          </header>
          <div className="app-table-wrap">
            <table className="min-w-full text-left text-xs">
              <thead className="app-table-head">
                <tr>
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 font-medium text-right">
                    Quantity
                  </th>
                  <th className="px-3 py-2 font-medium text-right">
                    P&amp;L
                  </th>
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 ? (
                  <tr>
                    <td
                      colSpan={3}
                      className="px-3 py-4 text-center app-text-muted"
                    >
                      No positions returned from ICICI.
                    </td>
                  </tr>
                ) : (
                  positions.map((p) => (
                    <tr key={p.stock_code} className="app-table-row">
                      <td className="px-3 py-2">{p.stock_code}</td>
                      <td className="px-3 py-2 text-right">
                        {p.quantity.toLocaleString("en-IN")}
                      </td>
                      <td
                        className={[
                          "px-3 py-2 text-right",
                          p.pnl >= 0
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-red-600 dark:text-red-400",
                        ].join(" ")}
                      >
                        ₹{p.pnl.toLocaleString("en-IN")}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </AppShell>
  );
}
