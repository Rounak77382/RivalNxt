/**
 * Background warming for lazily-loaded chunks.
 *
 * React.lazy only fetches a chunk when the component first *renders*. For a
 * modal that is fine — the user opened it deliberately and a few ms of Suspense
 * is invisible. For a tab page it is not: the user clicks "Collections" and waits
 * at a fallback, which is a worse experience than the eager import we removed.
 *
 * Warming the chunks once the app has gone idle gets both: the startup graph
 * stays small, and a tab switch still renders immediately because the chunk is
 * already in the module cache.
 */

type Loader = () => Promise<unknown>;

export interface PrefetchOptions {
  /** Injected by tests. Defaults to requestIdleCallback, or a short timer. */
  requestIdle?: (cb: () => void) => unknown;
  /** Injected by tests. Must accept whatever requestIdle returned. */
  cancelIdle?: (handle: unknown) => void;
}

function defaultRequestIdle(cb: () => void): unknown {
  // requestIdleCallback is not in Safari/WebKit, which is what Tauri uses on
  // macOS and Linux, so the timer fallback is a real code path and not just a
  // jsdom concession.
  const ric = (globalThis as { requestIdleCallback?: (cb: () => void) => number })
    .requestIdleCallback;
  if (typeof ric === "function") return ric(cb);
  return setTimeout(cb, 200);
}

function defaultCancelIdle(handle: unknown): void {
  const cic = (globalThis as { cancelIdleCallback?: (h: number) => void })
    .cancelIdleCallback;
  if (typeof cic === "function" && typeof handle === "number") {
    cic(handle);
    return;
  }
  clearTimeout(handle as ReturnType<typeof setTimeout>);
}

/**
 * Load `loaders` in order once the app is idle. Returns a cancel function
 * suitable for returning straight from a useEffect.
 *
 * Sequential on purpose: the point is to spend spare time, not to compete with
 * the page the user is actually looking at. Cancelling stops before the next
 * loader — an already-started import cannot be aborted, and does not need to be,
 * since a resolved module is just cached.
 */
export function prefetchWhenIdle(
  loaders: Loader[],
  { requestIdle = defaultRequestIdle, cancelIdle = defaultCancelIdle }: PrefetchOptions = {},
): () => void {
  let cancelled = false;

  const run = async () => {
    for (const load of loaders) {
      if (cancelled) return;
      try {
        await load();
      } catch {
        // A failed prefetch is not an error worth surfacing: React.lazy will
        // request the chunk again when the component actually renders, and that
        // path already has its own error handling.
      }
    }
  };

  const handle = requestIdle(() => {
    void run();
  });

  return () => {
    cancelled = true;
    cancelIdle(handle);
  };
}
