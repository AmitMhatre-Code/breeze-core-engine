import { apiClient } from "@/lib/api-client";

export type TelegramStatus = {
  connected: boolean;
  telegram_username: string | null;
  alerts_enabled: boolean;
  onboarding_dismissed: boolean;
  link_token: string | null;
  link_token_expires_at: string | null;
  bot_username: string;
  bot_configured: boolean;
};

export type TelegramLinkToken = {
  link_token: string;
  link_token_expires_at: string;
  bot_username: string;
};

export const TELEGRAM_STATUS_QUERY_KEY = ["telegram", "status"] as const;

export async function fetchTelegramStatus(): Promise<TelegramStatus> {
  return apiClient.get<TelegramStatus>("/api/settings/telegram/status");
}

export async function generateTelegramLinkToken(): Promise<TelegramLinkToken> {
  return apiClient.post<TelegramLinkToken>("/api/settings/telegram/link-token", {});
}

export async function disconnectTelegram(): Promise<void> {
  await apiClient.delete<{ ok: boolean }>("/api/settings/telegram/unlink");
}

export async function setTelegramOnboardingDismissed(dismissed: boolean): Promise<TelegramStatus> {
  return apiClient.put<TelegramStatus>("/api/settings/telegram/onboarding-dismissed", { dismissed });
}

export async function setTelegramAlertsEnabled(enabled: boolean): Promise<TelegramStatus> {
  return apiClient.put<TelegramStatus>("/api/settings/telegram/alerts-enabled", { enabled });
}

export function telegramDeepLink(botUsername: string, token: string): string {
  return `https://t.me/${botUsername}?start=${token}`;
}

/**
 * `tg://` custom-scheme variant of the same deep link. A phone's camera app has no
 * universal-link registration for `https://t.me/...`, so scanning that URL opens the
 * system browser first, which then hands off to Telegram — a visible, slower hop.
 * `tg://resolve` has no browser fallback at all, so only the Telegram app can handle
 * it and the OS opens it directly. Used for the QR code only; the copyable link stays
 * on the `https://t.me/...` form since that's what's safe to paste elsewhere.
 */
export function telegramAppDeepLink(botUsername: string, token: string): string {
  return `tg://resolve?domain=${encodeURIComponent(botUsername)}&start=${encodeURIComponent(token)}`;
}
