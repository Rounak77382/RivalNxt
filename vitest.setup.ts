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

// React Testing Library does not auto-cleanup when `globals` is enabled in some
// configurations; unmounting between tests stops leaked timers/effects from one
// test bleeding into the next, which matters a lot for the polling tests here.
afterEach(() => {
  cleanup();
});
