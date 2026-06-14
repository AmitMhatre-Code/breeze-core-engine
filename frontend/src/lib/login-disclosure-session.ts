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

export function getStoredSessionAckVersion(userId: string): number | null {
  if (typeof sessionStorage === "undefined") return null;
  const uid = userId.trim().toUpperCase();
  if (!uid) return null;

  const stored = sessionStorage.getItem(KEY);
  if (!stored) return null;

  const colon = stored.indexOf(":");
  if (colon < 1) return null;

  const storedUid = stored.slice(0, colon).trim().toUpperCase();
  if (storedUid !== uid) return null;

  const version = Number.parseInt(stored.slice(colon + 1), 10);
  return Number.isFinite(version) && version >= 1 ? version : null;
}

export function hasStoredSessionAckForUser(userId: string): boolean {
  return getStoredSessionAckVersion(userId) != null;
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
