/**
 * F4: the collections refresh must not hammer the backend.
 *
 * Before: `setInterval(fetchCollections, 4000)` with `[]` deps. Every 4s, for the
 * entire life of the page, it ran `listCollections()` and then `getCollection()`
 * for EVERY collection — an N+1 request burst — whether or not the window was
 * visible and whether or not anything had changed.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAdaptivePoll } from "../intervalHelpers";

/** Drive document.hidden, which is a getter on the prototype. */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", {
    value: hidden,
    configurable: true,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  setHidden(false);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  setHidden(false);
});

describe("useAdaptivePoll", () => {
  it("fires once immediately by default", () => {
    const cb = vi.fn();
    renderHook(() => useAdaptivePoll(cb, { activeMs: 1000, idleMs: 10_000 }));
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("can skip the immediate call", () => {
    const cb = vi.fn();
    renderHook(() =>
      useAdaptivePoll(cb, { activeMs: 1000, idleMs: 10_000, immediate: false }),
    );
    expect(cb).not.toHaveBeenCalled();
  });

  it("uses the IDLE cadence when nothing is active", async () => {
    const cb = vi.fn();
    renderHook(() =>
      useAdaptivePoll(cb, {
        activeMs: 4000,
        idleMs: 30_000,
        isActive: () => false,
      }),
    );
    expect(cb).toHaveBeenCalledTimes(1); // immediate

    await vi.advanceTimersByTimeAsync(4000);
    expect(cb).toHaveBeenCalledTimes(1); // the old 4s cadence is gone

    await vi.advanceTimersByTimeAsync(26_000);
    expect(cb).toHaveBeenCalledTimes(2);
  });

  it("uses the ACTIVE cadence while work is in flight", async () => {
    const cb = vi.fn();
    renderHook(() =>
      useAdaptivePoll(cb, {
        activeMs: 4000,
        idleMs: 30_000,
        isActive: () => true,
      }),
    );
    expect(cb).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(4000);
    expect(cb).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(8000);
    expect(cb).toHaveBeenCalledTimes(4);
  });

  it("switches cadence between ticks", async () => {
    // Assert on the DELTA over each window rather than absolute totals: the
    // immediate call plus microtask-scheduled follow-ups make exact counts
    // brittle without testing anything extra.
    let active = true;
    const cb = vi.fn();
    renderHook(() =>
      useAdaptivePoll(cb, {
        activeMs: 1000,
        idleMs: 20_000,
        isActive: () => active,
      }),
    );

    // Fast cadence: several ticks in 5s.
    let before = cb.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    const fastDelta = cb.mock.calls.length - before;
    expect(fastDelta).toBeGreaterThanOrEqual(4);

    active = false; // download finished -> slow cadence
    // Let the in-flight schedule settle onto the new cadence.
    await vi.advanceTimersByTimeAsync(1000);

    before = cb.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    const slowDelta = cb.mock.calls.length - before;
    expect(slowDelta).toBe(0); // 5s < 20s idle cadence

    await vi.advanceTimersByTimeAsync(20_000);
    expect(cb.mock.calls.length).toBeGreaterThan(before);
  });

  it("does no work while the document is hidden", async () => {
    const cb = vi.fn();
    renderHook(() =>
      useAdaptivePoll(cb, { activeMs: 1000, idleMs: 5000, isActive: () => true }),
    );
    expect(cb).toHaveBeenCalledTimes(1);

    setHidden(true);
    await vi.advanceTimersByTimeAsync(30_000);
    // Ticks fire but return early; the expensive callback must not run.
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("refreshes immediately when the tab becomes visible again", async () => {
    const cb = vi.fn();
    renderHook(() =>
      useAdaptivePoll(cb, { activeMs: 1000, idleMs: 60_000, isActive: () => true }),
    );
    expect(cb).toHaveBeenCalledTimes(1);

    setHidden(true);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(cb).toHaveBeenCalledTimes(1);

    const before = cb.mock.calls.length;
    setHidden(false);
    // The visibility handler calls run() synchronously; advancing by 0 flushes
    // its microtasks. `waitFor` cannot be used here -- it polls on real timers,
    // which never advance while fake timers are installed, so it just times out.
    await vi.advanceTimersByTimeAsync(0);
    expect(cb.mock.calls.length).toBeGreaterThan(before);
  });

  it("never overlaps: a slow callback does not stack", async () => {
    let running = 0;
    let maxConcurrent = 0;
    const cb = vi.fn(async () => {
      running += 1;
      maxConcurrent = Math.max(maxConcurrent, running);
      await new Promise((r) => setTimeout(r, 5000)); // slower than the cadence
      running -= 1;
    });

    renderHook(() =>
      useAdaptivePoll(cb, { activeMs: 1000, idleMs: 1000, isActive: () => true }),
    );

    await vi.advanceTimersByTimeAsync(30_000);
    expect(maxConcurrent).toBe(1);
  });

  it("survives a rejecting callback", async () => {
    const cb = vi.fn().mockRejectedValue(new Error("network down"));
    renderHook(() =>
      useAdaptivePoll(cb, { activeMs: 1000, idleMs: 1000, isActive: () => true }),
    );
    await vi.advanceTimersByTimeAsync(3000);
    // The loop must keep going rather than dying on the first failure.
    expect(cb.mock.calls.length).toBeGreaterThan(1);
  });

  it("does nothing at all when disabled", async () => {
    const cb = vi.fn();
    renderHook(() =>
      useAdaptivePoll(cb, { activeMs: 100, idleMs: 100, enabled: false }),
    );
    await vi.advanceTimersByTimeAsync(10_000);
    expect(cb).not.toHaveBeenCalled();
  });

  it("stops and unregisters its listener on unmount", async () => {
    const removeSpy = vi.spyOn(document, "removeEventListener");
    const cb = vi.fn();
    const { unmount } = renderHook(() =>
      useAdaptivePoll(cb, { activeMs: 500, idleMs: 500, isActive: () => true }),
    );
    const callsAtUnmount = cb.mock.calls.length;

    unmount();
    expect(removeSpy).toHaveBeenCalledWith("visibilitychange", expect.any(Function));

    await vi.advanceTimersByTimeAsync(10_000);
    expect(cb).toHaveBeenCalledTimes(callsAtUnmount);
  });

  it("always calls the LATEST callback", async () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(
      ({ cb }: { cb: () => void }) =>
        useAdaptivePoll(cb, { activeMs: 1000, idleMs: 1000, isActive: () => true }),
      { initialProps: { cb: first } },
    );
    first.mockClear();

    rerender({ cb: second });
    await vi.advanceTimersByTimeAsync(1000);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalled();
  });

  it("request volume over 5 minutes: idle poll is ~7x cheaper than the old 4s loop", async () => {
    const cb = vi.fn();
    renderHook(() =>
      useAdaptivePoll(cb, {
        activeMs: 4000,
        idleMs: 30_000,
        isActive: () => false,
      }),
    );

    const FIVE_MIN = 5 * 60 * 1000;
    await vi.advanceTimersByTimeAsync(FIVE_MIN);

    const oldLoopCalls = FIVE_MIN / 4000; // 75
    expect(cb.mock.calls.length).toBeLessThan(oldLoopCalls / 5);
    // 1 immediate + 10 at 30s cadence
    expect(cb.mock.calls.length).toBeLessThanOrEqual(12);
  });
});
