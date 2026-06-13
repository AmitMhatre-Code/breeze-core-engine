import { clearPreloadedLoginDisclosure } from "@/lib/login-disclosure-preload";

const KEY = "breeze_login_disclosure_ack";
const PENDING_KEY = "breeze_login_disclosure_pending";

function ackValue(userId: string, version: number): string {
  return `${userId.trim().toUpperCase()}:${version}`;
}

export function hasSessionAck(userId: string, version: number): boolean {
  if (typeof sessionStorage === "undefined") return false;
  const uid = userId.trim().toUpperCase();
  if (!uid || version < 1) return false;
  return sessionStorage.getItem(KEY) === ackValue(uid, version);
}

export function setSessionAck(userId: string, version: number): void {
  if (typeof sessionStorage === "undefined") return;
  const uid = userId.trim().toUpperCase();
  if (!uid || version < 1) return;
  sessionStorage.setItem(KEY, ackValue(uid, version));
}

export function markDisclosurePending(): void {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.setItem(PENDING_KEY, "1");
}

export function isDisclosurePending(): boolean {
  if (typeof sessionStorage === "undefined") return false;
  return sessionStorage.getItem(PENDING_KEY) === "1";
}

export function clearDisclosurePending(): void {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.removeItem(PENDING_KEY);
}

export function clearSessionAck(): void {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.removeItem(KEY);
  sessionStorage.removeItem(PENDING_KEY);
  clearPreloadedLoginDisclosure();
}
