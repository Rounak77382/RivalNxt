/**
 * F6: prefetchWhenIdle is what makes lazy tab pages acceptable.
 *
 * Splitting the three tab pages out of the entry chunk is only a win if the user
 * never sees the Suspense fallback. That depends on the chunks being warmed
 * before the first tab click, which is what this covers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { prefetchWhenIdle } from "../prefetch";

/** Captures the idle callback so tests decide when "idle" happens. */
function manualIdle() {
  let queued: (() => void) | null = null;
  let cancelled = 0;
  return {
    requestIdle: (cb: () => void) => {
      queued = cb;
      return 42;
    },
    cancelIdle: (handle: unknown) => {
      expect(handle).toBe(42);
      cancelled += 1;
    },
    fire: () => {
      const cb = queued;
      queued = null;
      cb?.();
    },
    get pending() {
      return queued !== null;
    },
    get cancelCount() {
      return cancelled;
    },
  };
}

describe("prefetchWhenIdle", () => {
  it("does not load anything before idle fires", () => {
    const idle = manualIdle();
    const load = vi.fn().mockResolvedValue({});

    prefetchWhenIdle([load], idle);

    expect(load).not.toHaveBeenCalled();
    expect(idle.pending).toBe(true);
  });

  it("loads every loader once idle fires", async () => {
    const idle = manualIdle();
    const a = vi.fn().mockResolvedValue({});
    const b = vi.fn().mockResolvedValue({});

    prefetchWhenIdle([a, b], idle);
    idle.fire();
    await vi.waitFor(() => expect(b).toHaveBeenCalled());

    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  it("loads sequentially, not all at once", async () => {
    // The point of prefetching is to use spare time. Firing every import in
    // parallel would compete with the page the user is actually looking at.
    const idle = manualIdle();
    let releaseFirst: () => void = () => {};
    const first = vi.fn(
      () => new Promise<void>((r) => { releaseFirst = r; }),
    );
    const second = vi.fn().mockResolvedValue({});

    prefetchWhenIdle([first, second], idle);
    idle.fire();

    await vi.waitFor(() => expect(first).toHaveBeenCalled());
    expect(second, "second loader started before the first resolved").not.toHaveBeenCalled();

    releaseFirst();
    await vi.waitFor(() => expect(second).toHaveBeenCalled());
  });

  it("cancelling before idle prevents any load", () => {
    const idle = manualIdle();
    const load = vi.fn().mockResolvedValue({});

    prefetchWhenIdle([load], idle)();

    expect(idle.cancelCount).toBe(1);
    idle.fire(); // nothing queued any more
    expect(load).not.toHaveBeenCalled();
  });

  it("cancelling mid-run stops before the next loader", async () => {
    // Matters because this is returned straight from a useEffect: unmounting the
    // app must not keep pulling chunks.
    const idle = manualIdle();
    let releaseFirst: () => void = () => {};
    const first = vi.fn(
      () => new Promise<void>((r) => { releaseFirst = r; }),
    );
    const second = vi.fn().mockResolvedValue({});

    const cancel = prefetchWhenIdle([first, second], idle);
    idle.fire();
    await vi.waitFor(() => expect(first).toHaveBeenCalled());

    cancel();
    releaseFirst();
    await Promise.resolve();
    await Promise.resolve();

    expect(second, "kept loading after cancel").not.toHaveBeenCalled();
  });

  it("a rejected prefetch does not stop the rest, and does not throw", async () => {
    // React.lazy re-requests the chunk when the component renders, so a failed
    // warm-up is not an error worth surfacing -- but it must not swallow the
    // remaining loaders either.
    const idle = manualIdle();
    const boom = vi.fn().mockRejectedValue(new Error("network gone"));
    const after = vi.fn().mockResolvedValue({});

    prefetchWhenIdle([boom, after], idle);
    idle.fire();

    await vi.waitFor(() => expect(after).toHaveBeenCalled());
    expect(boom).toHaveBeenCalledTimes(1);
  });

  it("an unhandled rejection is not raised", async () => {
    const idle = manualIdle();
    const onUnhandled = vi.fn();
    process.on("unhandledRejection", onUnhandled);
    try {
      prefetchWhenIdle([() => Promise.reject(new Error("x"))], idle);
      idle.fire();
      await new Promise((r) => setTimeout(r, 20));
      expect(onUnhandled).not.toHaveBeenCalled();
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }
  });

  it("an empty loader list is a no-op", () => {
    const idle = manualIdle();
    expect(() => prefetchWhenIdle([], idle)()).not.toThrow();
  });
});

describe("prefetchWhenIdle default scheduling", () => {
  const originalRIC = (globalThis as Record<string, unknown>).requestIdleCallback;
  const originalCIC = (globalThis as Record<string, unknown>).cancelIdleCallback;

  afterEach(() => {
    if (originalRIC === undefined) delete (globalThis as Record<string, unknown>).requestIdleCallback;
    else (globalThis as Record<string, unknown>).requestIdleCallback = originalRIC;
    if (originalCIC === undefined) delete (globalThis as Record<string, unknown>).cancelIdleCallback;
    else (globalThis as Record<string, unknown>).cancelIdleCallback = originalCIC;
    vi.useRealTimers();
  });

  it("uses requestIdleCallback when the platform has it", () => {
    const ric = vi.fn().mockReturnValue(7);
    const cic = vi.fn();
    (globalThis as Record<string, unknown>).requestIdleCallback = ric;
    (globalThis as Record<string, unknown>).cancelIdleCallback = cic;

    const cancel = prefetchWhenIdle([vi.fn().mockResolvedValue({})]);
    expect(ric).toHaveBeenCalledTimes(1);

    cancel();
    expect(cic).toHaveBeenCalledWith(7);
  });

  it("falls back to a timer when requestIdleCallback is missing", async () => {
    // Not a jsdom concession: WebKit -- which is the Tauri webview on macOS and
    // Linux -- has no requestIdleCallback.
    delete (globalThis as Record<string, unknown>).requestIdleCallback;
    delete (globalThis as Record<string, unknown>).cancelIdleCallback;
    vi.useFakeTimers();

    const load = vi.fn().mockResolvedValue({});
    prefetchWhenIdle([load]);

    expect(load).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(500);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("the timer fallback is cancellable", async () => {
    delete (globalThis as Record<string, unknown>).requestIdleCallback;
    delete (globalThis as Record<string, unknown>).cancelIdleCallback;
    vi.useFakeTimers();

    const load = vi.fn().mockResolvedValue({});
    prefetchWhenIdle([load])();

    await vi.advanceTimersByTimeAsync(500);
    expect(load).not.toHaveBeenCalled();
  });
});

describe("App wires the tab pages lazily", () => {
  // Source-level, because importing App.tsx here would pull in the Tauri API and
  // the whole component tree. The build-output assertions in
  // bundleSplitting.test.ts are the other half of this proof.
  let source: string;

  beforeEach(async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    source = readFileSync(resolve(__dirname, "../../App.tsx"), "utf8");
  });

  it.each(["DownloadsPage", "ActiveModsView", "CollectionsPage"])(
    "%s is declared with lazy(), not imported statically",
    (name) => {
      expect(
        source,
        `${name} is still a static import, so it stays in the entry chunk`,
      ).not.toMatch(new RegExp(`^import \\{[^}]*\\b${name}\\b[^}]*\\} from`, "m"));
      expect(source).toMatch(
        new RegExp(`const ${name} = lazy\\(\\(\\) =>\\s*\\n?\\s*import\\(`),
      );
    },
  );

  it("the tab content is wrapped in a Suspense boundary", () => {
    // Without one, the first render of a lazy page throws to the nearest
    // boundary -- which would be the whole app.
    const tabContent = source.slice(source.indexOf("{/* Tab Content */}"));
    const suspenseAt = tabContent.indexOf("<Suspense");
    const firstPageAt = tabContent.indexOf("<DownloadsPage");
    expect(suspenseAt).toBeGreaterThanOrEqual(0);
    expect(suspenseAt).toBeLessThan(firstPageAt);
  });

  it("warms the two non-default tabs on idle", () => {
    // The default tab renders immediately so needs no warming; the other two are
    // one click away and must not show a fallback.
    expect(source).toContain("prefetchWhenIdle([");
    expect(source).toMatch(/prefetchWhenIdle\(\[[\s\S]{0,200}components\/ActiveModsView/);
    expect(source).toMatch(/prefetchWhenIdle\(\[[\s\S]{0,200}components\/CollectionsPage/);
  });
});
