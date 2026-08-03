/**
 * F4 (batching), client half.
 *
 * The backend side is covered by tests/backend/test_collections_detailed.py. This
 * covers what the frontend actually does with it: rendering the collections page
 * must cost ONE request, not 1 + one per collection.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getCollection,
  listCollections,
  listCollectionsDetailed,
} from "../api";

type FetchCall = { url: string };

let calls: FetchCall[];

/** Serves the three collection routes from an in-memory fixture. */
function installFetch(collectionCount: number, filesEach = 3) {
  const collections = Array.from({ length: collectionCount }, (_, i) => ({
    id: i + 1,
    slug: `slug-${i}`,
    name: `Collection ${i}`,
    mod_files: Array.from({ length: filesEach }, (_, f) => ({
      file_id: 1000 * i + f,
      mod_id: 500 + f,
      file_name: `c${i}f${f}.zip`,
    })),
  }));

  const handler = vi.fn(async (input: string) => {
    const url = String(input);
    calls.push({ url });

    if (url.includes("/api/collections/detailed")) {
      return jsonResponse({ ok: true, collections });
    }
    const single = url.match(/\/api\/collections\/(\d+)$/);
    if (single) {
      const id = Number(single[1]);
      return jsonResponse({
        ok: true,
        collection: collections.find((c) => c.id === id),
      });
    }
    if (url.includes("/api/collections")) {
      // The summary route: no mod_files, which is why the old client had to
      // follow up with a request per collection.
      return jsonResponse({
        ok: true,
        collections: collections.map(({ mod_files: _drop, ...rest }) => rest),
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  });

  globalThis.fetch = handler as unknown as typeof fetch;
  return collections;
}

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

describe("collections are fetched in one request", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    calls = [];
    vi.stubEnv("VITE_API_BASE_URL", "http://test.local");
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("hits /api/collections/detailed exactly once", async () => {
    installFetch(5);
    await listCollectionsDetailed();

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/api/collections/detailed");
  });

  it("request count does not grow with collection count", async () => {
    // The regression this guards: 20 collections used to mean 21 requests, on
    // every poll.
    installFetch(3);
    await listCollectionsDetailed();
    const few = calls.length;

    calls = [];
    installFetch(30);
    await listCollectionsDetailed();
    const many = calls.length;

    expect(few).toBe(1);
    expect(many, `request count scaled with collections: ${few} -> ${many}`).toBe(few);
  });

  it("costs 1 request where the old N+1 path cost 21", async () => {
    // Runs the old shape against the same fixture so the comparison is real
    // rather than asserted from memory.
    installFetch(20);
    const summaries = await listCollections();
    for (const s of summaries) await getCollection(s.id);
    const oldCount = calls.length;

    calls = [];
    installFetch(20);
    await listCollectionsDetailed();
    const newCount = calls.length;

    expect(oldCount).toBe(21);
    expect(newCount).toBe(1);
  });

  it("returns each collection with its mod_files populated", async () => {
    // The batched route replaces getCollection(), so it must carry the field the
    // page renders -- the summary route does not have it.
    installFetch(4, 2);
    const result = await listCollectionsDetailed();

    expect(result).toHaveLength(4);
    for (const coll of result) {
      expect(coll.mod_files, `collection ${coll.id} has no mod_files`).toHaveLength(2);
    }
  });

  it("files stay attached to the right collection", async () => {
    installFetch(3, 2);
    const result = await listCollectionsDetailed();

    for (const coll of result) {
      const index = Number(String(coll.slug).split("-")[1]);
      for (const f of coll.mod_files ?? []) {
        expect(f.file_id).toBeGreaterThanOrEqual(1000 * index);
        expect(f.file_id).toBeLessThan(1000 * index + 100);
      }
    }
  });

  it("a response with no collections field yields [] rather than undefined", async () => {
    globalThis.fetch = vi.fn(async () => jsonResponse({ ok: true })) as unknown as typeof fetch;
    await expect(listCollectionsDetailed()).resolves.toEqual([]);
  });

  it("an empty collection list is not an error", async () => {
    installFetch(0);
    await expect(listCollectionsDetailed()).resolves.toEqual([]);
    expect(calls).toHaveLength(1);
  });
});

/**
 * Blank out comments, keeping every other character at its original offset so
 * indexOf comparisons still line up.
 *
 * Needed because the first version of the ordering test below failed on correct
 * code: the doc comment above the batched call names the old getCollection()
 * path, and a raw indexOf found the comment before the real call. Tracks string
 * and template literals too, so a "http://..." inside one is not read as a
 * line comment.
 */
function stripComments(src: string): string {
  const out = src.split("");
  let i = 0;
  const blank = (from: number, to: number) => {
    for (let k = from; k < to; k++) if (out[k] !== "\n") out[k] = " ";
  };
  while (i < src.length) {
    const c = src[i];
    const next = src[i + 1];
    if (c === "/" && next === "/") {
      const end = src.indexOf("\n", i);
      blank(i, end === -1 ? src.length : end);
      i = end === -1 ? src.length : end;
    } else if (c === "/" && next === "*") {
      const end = src.indexOf("*/", i + 2);
      const stop = end === -1 ? src.length : end + 2;
      blank(i, stop);
      i = stop;
    } else if (c === '"' || c === "'" || c === "`") {
      i += 1;
      while (i < src.length && src[i] !== c) {
        if (src[i] === "\\") i += 1;
        i += 1;
      }
      i += 1;
    } else {
      i += 1;
    }
  }
  return out.join("");
}

describe("stripComments", () => {
  // The helper above is load-bearing for the ordering assertion, so it gets its
  // own coverage rather than being trusted.
  it("removes line and block comments", () => {
    // Comments become spaces rather than disappearing -- that is what keeps
    // offsets stable -- so compare with per-line trailing space collapsed.
    const collapse = (s: string) => s.replace(/[ \t]+$/gm, "");
    expect(collapse(stripComments("a // b\nc"))).toBe("a\nc");

    const block = stripComments("a /* b */ c");
    expect(block).toHaveLength("a /* b */ c".length);
    expect(block.replace(/\s/g, "")).toBe("ac");
  });

  it("preserves offsets", () => {
    const src = "xx // comment\nyy";
    expect(stripComments(src)).toHaveLength(src.length);
    expect(stripComments(src).indexOf("yy")).toBe(src.indexOf("yy"));
  });

  it("does not treat // inside a string as a comment", () => {
    const src = 'const u = "http://x"; real();';
    expect(stripComments(src)).toContain("real()");
  });
});

describe("CollectionsPage uses the batched route with a fallback", () => {
  // Source-level: rendering CollectionsPage here would need the whole page's
  // provider tree. What matters is which call it reaches for first, and that a
  // failure of the new route still renders something.
  let source: string;

  beforeEach(async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    source = stripComments(
      readFileSync(resolve(__dirname, "../../components/CollectionsPage.tsx"), "utf8"),
    );
  });

  it("imports and calls listCollectionsDetailed", () => {
    expect(source).toContain("listCollectionsDetailed");
  });

  it("prefers the batched call inside fetchCollections", () => {
    const start = source.indexOf("const fetchCollections");
    expect(start).toBeGreaterThan(-1);
    const body = source.slice(start, start + 2500);
    const batchedAt = body.indexOf("listCollectionsDetailed(");
    const perCollectionAt = body.indexOf("getCollection(");
    expect(batchedAt, "fetchCollections does not call the batched route").toBeGreaterThan(-1);
    if (perCollectionAt > -1) {
      expect(
        batchedAt,
        "the per-collection path runs before the batched one",
      ).toBeLessThan(perCollectionAt);
    }
  });

  it("keeps a fallback so an older backend still renders", () => {
    // /api/collections/detailed does not exist on a released build. Without the
    // fallback the page would come up empty against one.
    const start = source.indexOf("const fetchCollections");
    const body = source.slice(start, start + 2500);
    expect(body).toContain("catch");
    expect(
      body.indexOf("getCollection("),
      "no per-collection fallback after the batched call",
    ).toBeGreaterThan(-1);
  });
});
