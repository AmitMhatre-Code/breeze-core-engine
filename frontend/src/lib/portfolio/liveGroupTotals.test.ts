import { describe, expect, it, vi } from "vitest";
import { createLiveGroupTotalsStore } from "@/lib/portfolio/liveGroupTotals";

describe("createLiveGroupTotalsStore", () => {
  it("exposes an empty map to start", () => {
    const store = createLiveGroupTotalsStore();
    expect(store.getSnapshot().size).toBe(0);
  });

  it("set() adds an entry and notifies subscribers with a new snapshot identity", () => {
    const store = createLiveGroupTotalsStore();
    const listener = vi.fn();
    store.subscribe(listener);
    const before = store.getSnapshot();

    store.set("a", { mtm: 100, carry: -20 });

    expect(listener).toHaveBeenCalledTimes(1);
    const after = store.getSnapshot();
    expect(after).not.toBe(before);
    expect(after.get("a")).toEqual({ mtm: 100, carry: -20 });
  });

  it("set() with unchanged values is a no-op — no notify, stable identity", () => {
    const store = createLiveGroupTotalsStore();
    store.set("a", { mtm: 100, carry: -20 });
    const snap = store.getSnapshot();
    const listener = vi.fn();
    store.subscribe(listener);

    store.set("a", { mtm: 100, carry: -20 });

    expect(listener).not.toHaveBeenCalled();
    expect(store.getSnapshot()).toBe(snap);
  });

  it("set() re-notifies when only one field changes", () => {
    const store = createLiveGroupTotalsStore();
    store.set("a", { mtm: 100, carry: -20 });
    const listener = vi.fn();
    store.subscribe(listener);

    store.set("a", { mtm: 100, carry: -21 });

    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().get("a")).toEqual({ mtm: 100, carry: -21 });
  });

  it("treats null figures as a distinct, comparable value", () => {
    const store = createLiveGroupTotalsStore();
    store.set("a", { mtm: null, carry: null });
    const listener = vi.fn();
    store.subscribe(listener);

    store.set("a", { mtm: null, carry: null });
    expect(listener).not.toHaveBeenCalled();

    store.set("a", { mtm: 0, carry: null });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("remove() drops the entry and notifies; removing an absent key is a no-op", () => {
    const store = createLiveGroupTotalsStore();
    store.set("a", { mtm: 1, carry: 2 });
    store.set("b", { mtm: 3, carry: 4 });
    const listener = vi.fn();
    store.subscribe(listener);

    store.remove("a");
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().has("a")).toBe(false);
    expect(store.getSnapshot().has("b")).toBe(true);

    const snap = store.getSnapshot();
    store.remove("a");
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot()).toBe(snap);
  });

  it("unsubscribe stops further notifications", () => {
    const store = createLiveGroupTotalsStore();
    const listener = vi.fn();
    const unsub = store.subscribe(listener);
    store.set("a", { mtm: 1, carry: 1 });
    unsub();
    store.set("a", { mtm: 2, carry: 2 });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("two independent stores do not share state", () => {
    const a = createLiveGroupTotalsStore();
    const b = createLiveGroupTotalsStore();
    a.set("x", { mtm: 1, carry: 1 });
    expect(b.getSnapshot().size).toBe(0);
  });
});
