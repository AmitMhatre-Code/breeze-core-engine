import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ACK_KEY,
  clearSessionAck,
  getStoredSessionAckVersion,
  hasSessionAck,
  hasStoredSessionAckForUser,
  isDisclosurePending,
  markDisclosurePending,
  setSessionAck,
} from "@/lib/login-disclosure-session";

function memoryStorage(store: Map<string, string>) {
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
  };
}

describe("login-disclosure-session", () => {
  const local = new Map<string, string>();
  const session = new Map<string, string>();
  const SIGN_IN = 1_791_600_000;

  beforeEach(() => {
    local.clear();
    session.clear();
    vi.stubGlobal("localStorage", memoryStorage(local));
    vi.stubGlobal("sessionStorage", memoryStorage(session));
  });

  afterEach(() => {
    clearSessionAck();
    vi.unstubAllGlobals();
  });

  it("tracks ack per user and version", () => {
    expect(hasSessionAck("icici1", 1, SIGN_IN)).toBe(false);
    setSessionAck("icici1", 1, SIGN_IN);
    expect(hasSessionAck("icici1", 1, SIGN_IN)).toBe(true);
    expect(hasSessionAck("icici1", 2, SIGN_IN)).toBe(false);
    expect(hasSessionAck("icici2", 1, SIGN_IN)).toBe(false);
  });

  it("shares the ack with a new tab of the same sign-in", () => {
    setSessionAck("icici1", 1, SIGN_IN);
    // A new tab starts with empty sessionStorage but the same localStorage.
    session.clear();
    expect(hasSessionAck("icici1", 1, SIGN_IN)).toBe(true);
    expect(getStoredSessionAckVersion("icici1", SIGN_IN)).toBe(1);
  });

  it("asks again after the next sign-in", () => {
    setSessionAck("icici1", 1, SIGN_IN);
    expect(hasSessionAck("icici1", 1, SIGN_IN + 60)).toBe(false);
    expect(getStoredSessionAckVersion("icici1", SIGN_IN + 60)).toBeNull();
  });

  it("clears session ack", () => {
    setSessionAck("icici1", 1, SIGN_IN);
    clearSessionAck();
    expect(hasSessionAck("icici1", 1, SIGN_IN)).toBe(false);
  });

  it("tracks pending login disclosure gate", () => {
    expect(isDisclosurePending()).toBe(false);
    markDisclosurePending();
    expect(isDisclosurePending()).toBe(true);
    clearSessionAck();
    expect(isDisclosurePending()).toBe(false);
  });

  it("reads stored ack version for the current user", () => {
    expect(getStoredSessionAckVersion("icici1", SIGN_IN)).toBeNull();
    expect(hasStoredSessionAckForUser("icici1", SIGN_IN)).toBe(false);

    setSessionAck("icici1", 3, SIGN_IN);
    expect(getStoredSessionAckVersion("icici1", SIGN_IN)).toBe(3);
    expect(getStoredSessionAckVersion("ICICI1", SIGN_IN)).toBe(3);
    expect(hasStoredSessionAckForUser("icici1", SIGN_IN)).toBe(true);
    expect(getStoredSessionAckVersion("icici2", SIGN_IN)).toBeNull();
    expect(hasStoredSessionAckForUser("icici2", SIGN_IN)).toBe(false);
  });

  it("returns null for invalid stored ack values", () => {
    local.set(ACK_KEY, "icici1");
    expect(getStoredSessionAckVersion("icici1", SIGN_IN)).toBeNull();

    local.set(ACK_KEY, `icici1:not-a-version:${SIGN_IN}`);
    expect(getStoredSessionAckVersion("icici1", SIGN_IN)).toBeNull();

    // Pre-sign-in-id format: never counts, so the disclosure is shown once more.
    local.set(ACK_KEY, "icici1:1");
    expect(getStoredSessionAckVersion("icici1", SIGN_IN)).toBeNull();
  });
});
