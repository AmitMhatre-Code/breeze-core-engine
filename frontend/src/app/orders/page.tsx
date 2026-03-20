// Client component so auth cookies are included with browser fetch.
"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";

type IciciApiResponse = {
  Status: number;
  Error?: string;
  Success?: {
    orders?: Array<{
      order_id: string;
      stock_code: string;
      side: string;
      quantity: number;
      status: string;
    }>;
  };
};

export default function OrdersPage() {
  const q = useQuery({
    queryKey: ["orders", "list"],
    queryFn: async () => apiClient.get<IciciApiResponse>("/order/data"),
  });

  const data = q.data;
  const orders = data?.Success?.orders ?? [];

  return (
    <AppShell>
      {q.isLoading ? (
        <div className="app-card p-4">Loading orders...</div>
      ) : q.error ? (
        <div className="app-alert-error">
          Unable to load orders:{" "}
          {q.error instanceof Error ? q.error.message : "Unknown error"}
        </div>
      ) : (
        <section className="app-card space-y-3 p-4">
          <header className="flex items-center justify-between">
            <h2 className="app-text-heading">Orders</h2>
            {data && data.Status !== 200 && (
              <span className="text-xs text-red-600 dark:text-red-400">
                {data.Error || "Unable to load orders"}
              </span>
            )}
          </header>
          <div className="app-table-wrap">
            <table className="min-w-full text-left text-xs text-zinc-800 dark:text-zinc-200">
              <thead className="app-table-head">
                <tr>
                  <th className="px-3 py-2 font-medium">Order ID</th>
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 font-medium text-right">Side</th>
                  <th className="px-3 py-2 font-medium text-right">
                    Quantity
                  </th>
                  <th className="px-3 py-2 font-medium text-right">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {orders.length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-3 py-4 text-center app-text-muted"
                    >
                      No orders returned from ICICI.
                    </td>
                  </tr>
                ) : (
                  orders.map((o) => (
                    <tr key={o.order_id} className="app-table-row">
                      <td className="px-3 py-2">{o.order_id}</td>
                      <td className="px-3 py-2">{o.stock_code}</td>
                      <td className="px-3 py-2 text-right">{o.side}</td>
                      <td className="px-3 py-2 text-right">
                        {o.quantity.toLocaleString("en-IN")}
                      </td>
                      <td className="px-3 py-2 text-right">{o.status}</td>
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
