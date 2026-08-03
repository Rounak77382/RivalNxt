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
