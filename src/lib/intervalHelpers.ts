import { useEffect, useRef } from "react";

/**
 * setInterval that can be switched off, and whose callback can change without
 * restarting the timer.
 *
 * Two problems this solves, both present in CollectionsPage:
 *
 * 1. Intervals that run forever even when there is no work. A 2s timer that wakes
 *    up, finds an empty map and goes back to sleep still costs a React state
 *    update attempt and a timer wakeup every 2s for the lifetime of the page.
 *    Passing `delayMs = null` unsubscribes entirely.
 *
 * 2. Intervals recreated whenever a dependency changes. The cleanup timer had
 *    `[installedModsIndex]` as its dependency, so every change to the installed
 *    mod list tore the timer down and started a new one — meaning the effective
 *    period drifted and work could be skipped. The callback is held in a ref, so
 *    the latest closure always runs without resubscribing.
 */
export function useGatedInterval(callback: () => void, delayMs: number | null): void {
  const callbackRef = useRef(callback);

  // Keep the ref current without restarting the timer below.
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    if (delayMs === null) return undefined;
    const id = setInterval(() => callbackRef.current(), delayMs);
    return () => clearInterval(id);
  }, [delayMs]);
}

export type AdaptivePollOptions = {
  /** Cadence while `isActive()` returns true. */
  activeMs: number;
  /** Cadence while it returns false. */
  idleMs: number;
  /** Whether something is currently happening that warrants the fast cadence. */
  isActive?: () => boolean;
  /** Set false to disable the poll entirely (e.g. component not visible). */
  enabled?: boolean;
  /** Run the callback once immediately on mount. Defaults to true. */
  immediate?: boolean;
};

/**
 * Self-rescheduling poll that pauses when the document is hidden and slows down
 * when nothing is happening.
 *
 * Motivation: CollectionsPage refetched every 4s with `setInterval` and `[]`
 * deps. Each tick ran `listCollections()` and then `getCollection()` for EVERY
 * collection — an N+1 request burst — forever, whether or not the window was
 * even on screen.
 *
 * Three properties a bare setInterval does not give:
 *
 * 1. Hidden tabs cost nothing. Browsers throttle background timers but still run
 *    them; here the poll is unsubscribed on `visibilitychange` and fires once
 *    immediately on return, so the user sees fresh data without a backlog.
 * 2. No overlap. The next tick is scheduled only after the current one settles,
 *    so a slow request cannot stack up behind itself.
 * 3. Cadence can change between ticks, because each tick schedules the next.
 */
export function useAdaptivePoll(
  callback: () => void | Promise<void>,
  options: AdaptivePollOptions,
): void {
  const { activeMs, idleMs, isActive, enabled = true, immediate = true } = options;

  const callbackRef = useRef(callback);
  const isActiveRef = useRef(isActive);

  useEffect(() => {
    callbackRef.current = callback;
    isActiveRef.current = isActive;
  }, [callback, isActive]);

  useEffect(() => {
    if (!enabled) return undefined;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const clear = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = () => {
      if (cancelled) return;
      const active = isActiveRef.current?.() ?? false;
      timer = setTimeout(run, active ? activeMs : idleMs);
    };

    const run = async () => {
      if (cancelled) return;
      // Skip the work entirely while hidden, but keep a slow heartbeat so the
      // loop is alive and ready when the tab comes back.
      if (typeof document !== "undefined" && document.hidden) {
        timer = setTimeout(run, idleMs);
        return;
      }
      try {
        await callbackRef.current();
      } catch {
        // Swallow: a failed poll must not kill the loop.
      }
      schedule();
    };

    const onVisibilityChange = () => {
      if (cancelled) return;
      if (!document.hidden) {
        // Back on screen: refresh now rather than waiting out the interval.
        clear();
        void run();
      }
    };

    if (immediate) {
      void run();
    } else {
      schedule();
    }

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }

    return () => {
      cancelled = true;
      clear();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
    };
  }, [activeMs, idleMs, enabled, immediate]);
}

/** How long a just-completed download stays flagged in the UI. */
export const RECENTLY_COMPLETED_TTL_MS = 20_000;

/**
 * Drop entries that are either already installed or older than the TTL.
 *
 * Returns the SAME map instance when nothing changed, so React can bail out of
 * the state update instead of re-rendering the page.
 */
export function pruneRecentlyCompleted(
  current: Map<string, number>,
  now: number,
  isInstalled: (fileId: string) => boolean,
  ttlMs: number = RECENTLY_COMPLETED_TTL_MS,
): Map<string, number> {
  let changed = false;
  const next = new Map(current);

  for (const [fileId, timestamp] of current.entries()) {
    if (isInstalled(fileId) || now - timestamp > ttlMs) {
      next.delete(fileId);
      changed = true;
    }
  }

  return changed ? next : current;
}
