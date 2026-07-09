"use client";

import { useEffect, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type { BreakChunkDefaultsResponse } from "@/lib/break-chunk-defaults";
import { snapQuantityToLotMultiple } from "@/lib/strategy-builder/leg-ui-helpers";

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
  const [expanded, setExpanded] = useState(false);
  const [text, setText] = useState(chunkQty);

  useEffect(() => {
    setText(chunkQty);
  }, [chunkQty]);

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

  if (!expanded) {
    return (
      <p
        className={`text-heading leading-snug text-zinc-500 dark:text-zinc-400 ${className}`}
      >
        The order will be broken into multiple chunks based on the freeze
        quantities enforced by the exchanges. Click{" "}
        <button
          type="button"
          className="font-medium text-zinc-700 underline underline-offset-2 hover:text-zinc-900 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-300 dark:hover:text-zinc-100"
          onClick={() => setExpanded(true)}
          disabled={disabled}
        >
          here
        </button>{" "}
        to chunk it differently.
      </p>
    );
  }

  const commit = (raw: string) => {
    const trimmed = raw.replace(/,/g, "").trim();
    if (trimmed === "" || lot == null || lot <= 0) {
      onChunkQtyChange(trimmed);
      setText(trimmed);
      return;
    }
    const n = parseInt(trimmed, 10);
    if (!Number.isFinite(n) || n <= 0) {
      onChunkQtyChange(trimmed);
      return;
    }
    const snapped = snapQuantityToLotMultiple(n, lot);
    onChunkQtyChange(String(snapped));
    setText(String(snapped));
  };

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
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        disabled={disabled}
      />
      <p className="mt-1 text-heading leading-snug text-zinc-500 dark:text-zinc-400">
        Default is the exchange freeze limit for this contract
        {maxQ != null ? ` (${maxQ.toLocaleString("en-IN")} units)` : ""}
        {lot != null ? `. Rounded to a multiple of lot ${lot.toLocaleString("en-IN")}.` : "."}
      </p>
    </div>
  );
}
