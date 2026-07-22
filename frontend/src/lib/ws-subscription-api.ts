import { getBackendBaseUrl } from "@/lib/config";

/** Release all WebSocket feed subscriptions for a page-scoped holder (socket stays open). */
export function releaseWsSubscriptionHolder(holderId: string): void {
  const id = holderId.trim();
  if (!id) return;
  const url = new URL("/market-data/ws/release", getBackendBaseUrl()).toString();
  const body = JSON.stringify({ holder_id: id });
  if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    const blob = new Blob([body], { type: "application/json" });
    if (navigator.sendBeacon(url, blob)) return;
  }
  void fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body,
    keepalive: true,
  });
}
