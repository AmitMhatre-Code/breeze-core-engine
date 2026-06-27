import { apiClient } from "@/lib/api-client";

export type ExchangeCalendarWorkingHours = {
  open_hour: number;
  open_minute: number;
  close_hour: number;
  close_minute: number;
};

export type ExchangeCalendarHolidayItem = {
  date: string;
  name: string;
};

export type ExchangeCalendarState = {
  user_id: string;
  source: "local" | "console_sync" | string;
  working_hours: ExchangeCalendarWorkingHours;
  holidays: Record<string, string>;
  holidays_list: ExchangeCalendarHolidayItem[];
  portal_configured: boolean;
  has_local_edits: boolean;
  console_updated_at: string | null;
  local_updated_at: string | null;
  updated_at: string | null;
};

export type ExchangeCalendarSyncPreview = {
  portal_configured: boolean;
  would_overwrite_local: boolean;
  console: ExchangeCalendarState | null;
  local_holiday_count: number;
  console_holiday_count: number;
  message: string | null;
};

export async function fetchExchangeCalendar(): Promise<ExchangeCalendarState> {
  return apiClient.get<ExchangeCalendarState>("/api/settings/exchange-calendar/data");
}

export async function saveExchangeCalendar(body: {
  working_hours: ExchangeCalendarWorkingHours;
  holidays: ExchangeCalendarHolidayItem[];
}): Promise<ExchangeCalendarState> {
  return apiClient.put<ExchangeCalendarState>("/api/settings/exchange-calendar", body);
}

export async function fetchExchangeCalendarSyncPreview(): Promise<ExchangeCalendarSyncPreview> {
  return apiClient.get<ExchangeCalendarSyncPreview>(
    "/api/settings/exchange-calendar/sync-preview",
  );
}

export async function syncExchangeCalendarFromConsole(
  confirmOverride: boolean,
): Promise<ExchangeCalendarState> {
  return apiClient.post<ExchangeCalendarState>("/api/settings/exchange-calendar/sync", {
    confirm_override: confirmOverride,
  });
}
