import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearSessionAck,
  getStoredSessionAckVersion,
  hasSessionAck,
  hasStoredSessionAckForUser,
  isDisclosurePending,
  markDisclosurePending,
  setSessionAck,
} from "@/lib/login-disclosure-session";

describe("login-disclosure-session", () => {
  const store = new Map<string, string>();

  beforeEach(() => {
    store.clear();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    });
  });

  afterEach(() => {
    clearSessionAck();
    vi.unstubAllGlobals();
  });

  it("tracks ack per user and version", () => {
    expect(hasSessionAck("icici1", 1)).toBe(false);
    setSessionAck("icici1", 1);
    expect(hasSessionAck("icici1", 1)).toBe(true);
    expect(hasSessionAck("icici1", 2)).toBe(false);
    expect(hasSessionAck("icici2", 1)).toBe(false);
  });

  it("clears session ack", () => {
    setSessionAck("icici1", 1);
    clearSessionAck();
    expect(hasSessionAck("icici1", 1)).toBe(false);
  });

  it("tracks pending login disclosure gate", () => {
    expect(isDisclosurePending()).toBe(false);
    markDisclosurePending();
    expect(isDisclosurePending()).toBe(true);
    clearSessionAck();
    expect(isDisclosurePending()).toBe(false);
  });

  it("reads stored ack version for the current user", () => {
    expect(getStoredSessionAckVersion("icici1")).toBeNull();
    expect(hasStoredSessionAckForUser("icici1")).toBe(false);

    setSessionAck("icici1", 3);
    expect(getStoredSessionAckVersion("icici1")).toBe(3);
    expect(getStoredSessionAckVersion("ICICI1")).toBe(3);
    expect(hasStoredSessionAckForUser("icici1")).toBe(true);
    expect(getStoredSessionAckVersion("icici2")).toBeNull();
    expect(hasStoredSessionAckForUser("icici2")).toBe(false);
  });

  it("returns null for invalid stored ack values", () => {
    const store = sessionStorage as unknown as {
      setItem: (key: string, value: string) => void;
    };
    store.setItem("breeze_login_disclosure_ack", "icici1");
    expect(getStoredSessionAckVersion("icici1")).toBeNull();

    store.setItem("breeze_login_disclosure_ack", "icici1:not-a-version");
    expect(getStoredSessionAckVersion("icici1")).toBeNull();
  });
});
