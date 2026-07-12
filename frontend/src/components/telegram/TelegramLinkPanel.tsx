"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";

import {
  fetchTelegramStatus,
  generateTelegramLinkToken,
  telegramDeepLink,
  TELEGRAM_STATUS_QUERY_KEY,
} from "@/lib/telegram/telegram-alerts";

function formatCountdown(msRemaining: number): string {
  const totalSeconds = Math.max(0, Math.floor(msRemaining / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/**
 * Shared QR/countdown/copy-link building block for the Settings screen
 * (unregistered state) and the in-app inline nudge modal. Polls status so it
 * flips to "connected" the moment the bot's long-poll loop records the
 * chat_id, without the user needing to refresh.
 */
export function TelegramLinkPanel({ onConnected }: { onConnected?: () => void }) {
  const qc = useQueryClient();
  const [now, setNow] = useState(() => Date.now());
  const [copied, setCopied] = useState(false);

  const statusQ = useQuery({
    queryKey: TELEGRAM_STATUS_QUERY_KEY,
    queryFn: fetchTelegramStatus,
    refetchInterval: 3000,
  });

  const tokenMut = useMutation({
    mutationFn: generateTelegramLinkToken,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TELEGRAM_STATUS_QUERY_KEY });
    },
  });

  const status = statusQ.data;
  const hasValidToken = Boolean(status?.link_token && status.link_token_expires_at);
  const expiresAtMs = status?.link_token_expires_at ? new Date(status.link_token_expires_at).getTime() : null;
  const msRemaining = expiresAtMs != null ? expiresAtMs - now : 0;
  const expired = hasValidToken && msRemaining <= 0;

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Fetch a token the first time this panel mounts unregistered.
  useEffect(() => {
    if (statusQ.isSuccess && !status?.connected && !hasValidToken && !tokenMut.isPending) {
      tokenMut.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusQ.isSuccess, status?.connected, hasValidToken]);

  // Auto-regenerate once the token expires.
  useEffect(() => {
    if (expired && !tokenMut.isPending) {
      tokenMut.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expired]);

  useEffect(() => {
    if (status?.connected) onConnected?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.connected]);

  const deepLink = useMemo(() => {
    if (!status?.bot_username || !status.link_token) return null;
    return telegramDeepLink(status.bot_username, status.link_token);
  }, [status?.bot_username, status?.link_token]);

  if (statusQ.isLoading) {
    return <p className="text-xs text-muted">Loading…</p>;
  }

  if (status && !status.bot_configured) {
    return (
      <p className="text-xs text-down">
        Telegram alerts are not configured on this deployment yet — ask the operator to set
        TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME.
      </p>
    );
  }

  if (status?.connected) {
    return (
      <p className="text-xs text-muted">
        Connected as <span className="font-semibold text-foreground">@{status.telegram_username}</span>.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <ol className="list-decimal space-y-1 pl-4 text-xs leading-relaxed text-muted">
        <li>Open Telegram on your phone.</li>
        <li>Scan the QR code below, or tap the link on the same device.</li>
        <li>Press <span className="font-semibold text-foreground">Start</span> in the chat.</li>
      </ol>

      <div className="flex flex-col items-center gap-3 rounded-[9px] border border-border-soft bg-panel2 p-4">
        {deepLink && !expired ? (
          <div className="rounded-md bg-white p-2">
            <QRCodeSVG value={deepLink} size={168} />
          </div>
        ) : (
          <div className="flex size-[184px] items-center justify-center text-xs text-faint">
            {tokenMut.isPending ? "Generating code…" : "Code expired"}
          </div>
        )}

        {hasValidToken && !expired ? (
          <p className="text-xs text-muted">
            Expires in <span className="font-mono font-semibold text-foreground">{formatCountdown(msRemaining)}</span>
          </p>
        ) : null}

        {deepLink ? (
          <button
            type="button"
            className="app-btn-outline rounded-[8px] px-3 py-1.5 text-xs"
            onClick={async () => {
              await navigator.clipboard.writeText(deepLink);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? "Copied!" : "Copy Link"}
          </button>
        ) : null}
      </div>

      {tokenMut.isError ? (
        <p className="text-xs text-down">Could not generate a link. Try again.</p>
      ) : null}
    </div>
  );
}
