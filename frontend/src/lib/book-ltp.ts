import { apiClient } from "@/lib/api-client";

export type BookGroupLtpItem = {
  group: string;
  stock_code: string;
  expiry_date: string;
  strike_price: string | number;
  right: string;
  exchange_code: string;
};

export type BookGroupLtpResponse = {
  ltps: Record<string, number | null>;
};

export function fetchBookGroupLtps(
  groups: BookGroupLtpItem[],
): Promise<BookGroupLtpResponse> {
  return apiClient.post<BookGroupLtpResponse>("/book/group-ltp", { groups });
}
