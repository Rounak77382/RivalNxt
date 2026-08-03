/**
 * F3: the CollectionsPage cleanup timer must not run when there is no work, and
 * must not restart every time an unrelated dependency changes.
 *
 * Before: `setInterval(…, 2000)` with `[installedModsIndex]` deps. It woke every
 * 2s for the life of the page even with an empty map, and every change to the
 * installed mod list tore the timer down and started a fresh one.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  RECENTLY_COMPLETED_TTL_MS,
  pruneRecentlyCompleted,
  useGatedInterval,
} from "../intervalHelpers";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useGatedInterval", () => {
  it("does not schedule anything when delay is null", () => {
    const spy = vi.spyOn(globalThis, "setInterval");
    const cb = vi.fn();

    renderHook(() => useGatedInterval(cb, null));

    expect(spy).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(cb).not.toHaveBeenCalled();
  });

  it("fires on the given cadence when enabled", () => {
    const cb = vi.fn();
    renderHook(() => useGatedInterval(cb, 2000));

    expect(cb).not.toHaveBeenCalled();
    vi.advanceTimersByTime(2000);
    expect(cb).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(4000);
    expect(cb).toHaveBeenCalledTimes(3);
  });

  it("stops firing once the delay flips to null", () => {
    const cb = vi.fn();
    const { rerender } = renderHook(
      ({ delay }: { delay: number | null }) => useGatedInterval(cb, delay),
      { initialProps: { delay: 2000 as number | null } },
    );

    vi.advanceTimersByTime(2000);
    expect(cb).toHaveBeenCalledTimes(1);

    rerender({ delay: null });
    vi.advanceTimersByTime(10_000);
    expect(cb).toHaveBeenCalledTimes(1); // no further ticks
  });

  it("resumes when the delay flips back on", () => {
    const cb = vi.fn();
    const { rerender } = renderHook(
      ({ delay }: { delay: number | null }) => useGatedInterval(cb, delay),
      { initialProps: { delay: null as number | null } },
    );

    vi.advanceTimersByTime(10_000);
    expect(cb).not.toHaveBeenCalled();

    rerender({ delay: 1000 });
    vi.advanceTimersByTime(3000);
    expect(cb).toHaveBeenCalledTimes(3);
  });

  it("does NOT restart the timer when only the callback changes", () => {
    // The regression: [installedModsIndex] deps meant the period drifted and
    // pending work could be skipped every time the mod list changed.
    const clearSpy = vi.spyOn(globalThis, "clearInterval");
    const setSpy = vi.spyOn(globalThis, "setInterval");

    const { rerender } = renderHook(
      ({ cb }: { cb: () => void }) => useGatedInterval(cb, 2000),
      { initialProps: { cb: vi.fn() } },
    );

    const initialSetCount = setSpy.mock.calls.length;
    expect(initialSetCount).toBe(1);

    // Simulate installedModsIndex changing -> a brand new callback identity.
    for (let i = 0; i < 5; i++) {
      rerender({ cb: vi.fn() });
    }

    expect(setSpy.mock.calls.length).toBe(initialSetCount);
    expect(clearSpy).not.toHaveBeenCalled();
  });

  it("always invokes the LATEST callback", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(
      ({ cb }: { cb: () => void }) => useGatedInterval(cb, 1000),
      { initialProps: { cb: first } },
    );

    rerender({ cb: second });
    vi.advanceTimersByTime(1000);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("clears the timer on unmount (no leak)", () => {
    const clearSpy = vi.spyOn(globalThis, "clearInterval");
    const cb = vi.fn();
    const { unmount } = renderHook(() => useGatedInterval(cb, 1000));

    unmount();
    expect(clearSpy).toHaveBeenCalled();

    vi.advanceTimersByTime(10_000);
    expect(cb).not.toHaveBeenCalled();
  });

  it("changing the delay reschedules exactly once", () => {
    const cb = vi.fn();
    const { rerender } = renderHook(
      ({ delay }: { delay: number }) => useGatedInterval(cb, delay),
      { initialProps: { delay: 1000 } },
    );
    const setSpy = vi.spyOn(globalThis, "setInterval");
    rerender({ delay: 5000 });
    expect(setSpy).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1000);
    expect(cb).not.toHaveBeenCalled(); // old 1s cadence is gone
    vi.advanceTimersByTime(4000);
    expect(cb).toHaveBeenCalledTimes(1);
  });
});

describe("pruneRecentlyCompleted", () => {
  const never = () => false;

  it("returns the SAME map instance when nothing changed", () => {
    // Identity matters: a new Map every 2s would re-render the whole page.
    const map = new Map([["1", 1000]]);
    const out = pruneRecentlyCompleted(map, 1500, never);
    expect(out).toBe(map);
  });

  it("drops entries past the TTL", () => {
    const map = new Map([["1", 0]]);
    const out = pruneRecentlyCompleted(map, RECENTLY_COMPLETED_TTL_MS + 1, never);
    expect(out).not.toBe(map);
    expect(out.size).toBe(0);
  });

  it("keeps entries exactly at the TTL boundary", () => {
    const map = new Map([["1", 0]]);
    const out = pruneRecentlyCompleted(map, RECENTLY_COMPLETED_TTL_MS, never);
    expect(out).toBe(map);
    expect(out.has("1")).toBe(true);
  });

  it("drops entries that are now installed, regardless of age", () => {
    const map = new Map([["7", 1000]]);
    const out = pruneRecentlyCompleted(map, 1001, (id) => id === "7");
    expect(out.has("7")).toBe(false);
  });

  it("keeps unrelated entries", () => {
    const map = new Map([
      ["old", 0],
      ["fresh", 19_000],
      ["installed", 19_000],
    ]);
    const out = pruneRecentlyCompleted(map, 20_500, (id) => id === "installed");
    expect(Array.from(out.keys())).toEqual(["fresh"]);
  });

  it("does not mutate the input map", () => {
    const map = new Map([["old", 0]]);
    pruneRecentlyCompleted(map, 999_999, never);
    expect(map.has("old")).toBe(true);
  });

  it("handles an empty map without allocating a change", () => {
    const map = new Map<string, number>();
    expect(pruneRecentlyCompleted(map, Date.now(), never)).toBe(map);
  });
});
