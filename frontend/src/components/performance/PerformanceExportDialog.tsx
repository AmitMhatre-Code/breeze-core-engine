"use client";

import { useCallback } from "react";
import type { MonthlyPerformanceRow } from "@/lib/performance-data";

export type PerformanceExportSummary = {
  fyLabel: string;
  totalBankBalance: number;
  cashLimit: number;
  netPnl: number;
  premiumEarned: number;
  premiumPaid: number;
  brokerage: number;
  taxes: number;
  annualisedRoiPct: number;
};

function csvCell(value: string | number): string {
  const s = String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function buildCsv(
  summary: PerformanceExportSummary,
  monthly: MonthlyPerformanceRow[],
): string {
  const lines: string[] = [];
  lines.push(["Metric", "Value"].map(csvCell).join(","));
  lines.push(["Financial year", summary.fyLabel].map(csvCell).join(","));
  lines.push(
    ["Total bank balance", summary.totalBankBalance].map(csvCell).join(","),
  );
  lines.push(["Total margin", summary.cashLimit].map(csvCell).join(","));
  lines.push(["Net P&L", summary.netPnl].map(csvCell).join(","));
  lines.push(
    ["Premium earned", summary.premiumEarned].map(csvCell).join(","),
  );
  lines.push(["Premium paid", summary.premiumPaid].map(csvCell).join(","));
  lines.push(["Brokerage", summary.brokerage].map(csvCell).join(","));
  lines.push(["Taxes", summary.taxes].map(csvCell).join(","));
  lines.push(
    ["Annualised ROI %", summary.annualisedRoiPct].map(csvCell).join(","),
  );
  lines.push("");
  lines.push(["Month", "P&L", "Brokerage", "Taxes"].map(csvCell).join(","));
  for (const row of monthly) {
    lines.push(
      [row.month, row.pnl, row.brokerage, row.taxes].map(csvCell).join(","),
    );
  }
  return lines.join("\n");
}

function escapeHtml(value: string | number): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function buildPrintableStatementHtml(
  summary: PerformanceExportSummary,
  monthly: MonthlyPerformanceRow[],
): string {
  const rows = monthly
    .map(
      (m) =>
        `<tr><td>${escapeHtml(m.month)}</td><td>₹${escapeHtml(m.pnl.toLocaleString("en-IN"))}</td><td>₹${escapeHtml(m.brokerage.toLocaleString("en-IN"))}</td><td>₹${escapeHtml(m.taxes.toLocaleString("en-IN"))}</td></tr>`,
    )
    .join("");
  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>P&amp;L statement — ${escapeHtml(summary.fyLabel)}</title>
<style>
  body { font-family: 'IBM Plex Sans', system-ui, sans-serif; color: #0E1520; padding: 32px; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { color: #5A6473; font-size: 12px; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
  th, td { text-align: left; padding: 6px 10px; font-size: 12px; border-bottom: 1px solid #E8ECF2; font-family: 'IBM Plex Mono', monospace; }
  th:first-child, td:first-child { font-family: 'IBM Plex Sans', system-ui, sans-serif; }
  th { text-transform: uppercase; letter-spacing: .05em; font-size: 10px; color: #93A0B0; }
  .summary td:first-child { color: #5A6473; }
  @media print { body { padding: 0; } }
</style>
</head>
<body>
  <h1>P&amp;L statement</h1>
  <div class="sub">${escapeHtml(summary.fyLabel)} · Apr–Mar</div>
  <table class="summary">
    <tr><td>Total bank balance</td><td>₹${escapeHtml(summary.totalBankBalance.toLocaleString("en-IN"))}</td></tr>
    <tr><td>Total margin</td><td>₹${escapeHtml(summary.cashLimit.toLocaleString("en-IN"))}</td></tr>
    <tr><td>Net P&amp;L</td><td>₹${escapeHtml(summary.netPnl.toLocaleString("en-IN"))}</td></tr>
    <tr><td>Premium earned</td><td>₹${escapeHtml(summary.premiumEarned.toLocaleString("en-IN"))}</td></tr>
    <tr><td>Premium paid</td><td>−₹${escapeHtml(summary.premiumPaid.toLocaleString("en-IN"))}</td></tr>
    <tr><td>Brokerage</td><td>−₹${escapeHtml(summary.brokerage.toLocaleString("en-IN"))}</td></tr>
    <tr><td>Taxes</td><td>−₹${escapeHtml(summary.taxes.toLocaleString("en-IN"))}</td></tr>
    <tr><td>Annualised ROI</td><td>${escapeHtml(summary.annualisedRoiPct.toFixed(2))}%</td></tr>
  </table>
  <table>
    <thead><tr><th>Month</th><th>P&amp;L</th><th>Brokerage</th><th>Taxes</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <script>window.onload = () => window.print();</script>
</body></html>`;
}

export function PerformanceExportDialog({
  open,
  onClose,
  summary,
  monthly,
}: {
  open: boolean;
  onClose: () => void;
  summary: PerformanceExportSummary;
  monthly: MonthlyPerformanceRow[];
}) {
  const exportCsv = useCallback(() => {
    const csv = buildCsv(summary, monthly);
    downloadBlob(
      new Blob([csv], { type: "text/csv;charset=utf-8" }),
      `${summary.fyLabel.replace(/\s+/g, "-")}-pnl-statement.csv`,
    );
    onClose();
  }, [summary, monthly, onClose]);

  const exportPdf = useCallback(() => {
    const html = buildPrintableStatementHtml(summary, monthly);
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const win = window.open(url, "_blank");
    if (!win) URL.revokeObjectURL(url);
    onClose();
  }, [summary, monthly, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-6"
      onClick={onClose}
    >
      <div
        className="w-full max-w-[380px] rounded-[13px] border border-border bg-panel p-[22px] shadow-pop"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <span className="text-[14.5px] font-bold text-foreground">
            Export P&amp;L statement
          </span>
          <button
            type="button"
            className="flex h-7 w-7 items-center justify-center rounded-[7px] text-muted transition hover:bg-border-soft hover:text-foreground"
            onClick={onClose}
            aria-label="Close"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="mb-3.5 text-xs text-muted">
          {summary.fyLabel} · Apr–Mar
        </div>
        <div className="mb-4 flex flex-col gap-2">
          <button
            type="button"
            className="flex items-center gap-2.5 rounded-[9px] border border-border bg-panel2 px-3.5 py-2.5 text-left text-sm font-semibold text-foreground transition hover:bg-border-soft"
            onClick={exportPdf}
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--accent-strong)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            PDF · formatted statement
          </button>
          <button
            type="button"
            className="flex items-center gap-2.5 rounded-[9px] border border-border bg-panel2 px-3.5 py-2.5 text-left text-sm font-semibold text-foreground transition hover:bg-border-soft"
            onClick={exportCsv}
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--accent-strong)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M8 13h2M8 17h2M14 13h2M14 17h2M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            CSV · raw transaction data
          </button>
        </div>
        <button
          type="button"
          className="w-full text-xs font-semibold text-muted transition hover:text-foreground"
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
