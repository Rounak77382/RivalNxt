import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

/**
 * Node 26 ships an experimental Web Storage implementation and installs a
 * `localStorage` getter on globalThis. Without `--localstorage-file` that getter
 * yields `undefined`, and because it occupies the slot before jsdom populates
 * globals, jsdom's own localStorage never lands — `window.localStorage` and
 * `globalThis.localStorage` are both undefined while `sessionStorage` works
 * normally. Node emits:
 *
 *   ExperimentalWarning: localStorage is not available because
 *   --localstorage-file was not provided.
 *
 * The descriptor is `configurable: true`, so we can replace it. Installing a
 * spec-shaped in-memory Storage keeps tests deterministic and independent of the
 * Node version's webstorage flags.
 */
function createMemoryStorage(): Storage {
  let store = new Map<string, string>();
  const storage = {
    get length() {
      return store.size;
    },
    clear() {
      store = new Map();
    },
    getItem(key: string) {
      const k = String(key);
      return store.has(k) ? (store.get(k) as string) : null;
    },
    key(index: number) {
      const i = Number(index);
      if (!Number.isInteger(i) || i < 0) return null;
      return Array.from(store.keys())[i] ?? null;
    },
    removeItem(key: string) {
      store.delete(String(key));
    },
    setItem(key: string, value: string) {
      store.set(String(key), String(value));
    },
  };
  return storage as unknown as Storage;
}

function ensureStorage(name: "localStorage" | "sessionStorage") {
  let usable = false;
  try {
    const existing = (globalThis as Record<string, unknown>)[name] as Storage | undefined;
    if (existing && typeof existing.setItem === "function") {
      // Prove it actually works rather than trusting its presence.
      const probe = "__vitest_storage_probe__";
      existing.setItem(probe, "1");
      usable = existing.getItem(probe) === "1";
      existing.removeItem(probe);
    }
  } catch {
    usable = false;
  }

  if (usable) return;

  const value = createMemoryStorage();
  Object.defineProperty(globalThis, name, {
    value,
    configurable: true,
    writable: true,
  });
  if (typeof window !== "undefined") {
    Object.defineProperty(window, name, {
      value,
      configurable: true,
      writable: true,
    });
  }
}

ensureStorage("localStorage");
ensureStorage("sessionStorage");

/**
 * jsdom implements neither ResizeObserver nor IntersectionObserver.
 *
 * @tanstack/react-virtual measures its scroll element through ResizeObserver; with
 * it missing the virtualizer never learns the viewport height and reports zero
 * visible items, so a virtualized list renders nothing at all under test. This
 * polyfill reports each observed element's current getBoundingClientRect, which
 * tests can stub to describe whatever viewport they need.
 */
if (typeof globalThis.ResizeObserver === "undefined") {
  class TestResizeObserver implements ResizeObserver {
    private targets = new Set<Element>();

    constructor(private readonly callback: ResizeObserverCallback) {}

    observe(target: Element) {
      this.targets.add(target);
      // Deliver an initial measurement synchronously, as browsers do.
      this.emit();
    }

    unobserve(target: Element) {
      this.targets.delete(target);
    }

    disconnect() {
      this.targets.clear();
    }

    private emit() {
      const entries = Array.from(this.targets).map((target) => {
        const rect = target.getBoundingClientRect();
        return {
          target,
          contentRect: rect,
          borderBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
          contentBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
          devicePixelContentBoxSize: [
            { inlineSize: rect.width, blockSize: rect.height },
          ],
        } as unknown as ResizeObserverEntry;
      });
      if (entries.length > 0) this.callback(entries, this);
    }
  }
  globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;
}

if (typeof globalThis.IntersectionObserver === "undefined") {
  class TestIntersectionObserver implements IntersectionObserver {
    readonly root = null;
    readonly rootMargin = "";
    readonly thresholds: ReadonlyArray<number> = [];
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  globalThis.IntersectionObserver =
    TestIntersectionObserver as unknown as typeof IntersectionObserver;
}

// React Testing Library does not auto-cleanup when `globals` is enabled in some
// configurations; unmounting between tests stops leaked timers/effects from one
// test bleeding into the next, which matters a lot for the polling tests here.
afterEach(() => {
  cleanup();
});
