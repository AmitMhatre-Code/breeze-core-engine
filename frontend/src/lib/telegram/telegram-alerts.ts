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
