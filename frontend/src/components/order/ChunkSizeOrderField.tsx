"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import type { BreakChunkDefaultsResponse } from "@/lib/break-chunk-defaults";

type ChunkSizeOrderFieldProps = {
  id: string;
  chunkQty: string;
  onChunkQtyChange: (value: string) => void;
  defaultsQuery: UseQueryResult<BreakChunkDefaultsResponse, Error>;
  disabled?: boolean;
  /** Extra classes on the outer wrapper */
  className?: string;
};

export function ChunkSizeOrderField({
  id,
  chunkQty,
  onChunkQtyChange,
  defaultsQuery,
  disabled = false,
  className = "",
}: ChunkSizeOrderFieldProps) {
  if (defaultsQuery.isPending) {
    return (
      <p className={`text-xs text-zinc-500 dark:text-zinc-400 ${className}`}>
        Loading exchange limits…
      </p>
    );
  }

  const d = defaultsQuery.data;
  if (defaultsQuery.isError || !d?.ok) {
    const httpMsg = defaultsQuery.error?.message?.trim();
    const msg =
      httpMsg && httpMsg.length > 0
        ? httpMsg
        : d && !d.ok && d.error?.trim()
          ? d.error
          : "Could not load default chunk size.";
    return (
      <p
        className={`text-xs text-red-600 dark:text-red-400 ${className}`}
        role="alert"
      >
        {msg}
      </p>
    );
  }

  const lot = d.lot_size ?? null;
  const maxQ = d.max_chunk_qty ?? d.default_chunk_qty ?? null;

  return (
    <div className={className}>
      <label
        htmlFor={id}
        className="block text-xs font-medium text-zinc-500 dark:text-zinc-400"
      >
        Max per order (chunk)
      </label>
      <input
        id={id}
        type="number"
        min={lot != null && lot > 0 ? lot : 1}
        max={maxQ != null && maxQ > 0 ? maxQ : undefined}
        className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
        value={chunkQty}
        onChange={(e) => onChunkQtyChange(e.target.value)}
        disabled={disabled}
      />
      <p className="mt-1 text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">
        Default is the exchange freeze limit for this contract
        {maxQ != null ? ` (${maxQ.toLocaleString("en-IN")} units)` : ""}
        {lot != null ? `. Rounded to a multiple of lot ${lot.toLocaleString("en-IN")}.` : "."}
      </p>
    </div>
  );
}
