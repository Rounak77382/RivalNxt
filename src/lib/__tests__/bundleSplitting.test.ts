/**
 * F5/F6: assert the SHIPPED bundle is actually split.
 *
 * Unit-testing `lazy()` proves the pattern; only the build output proves the
 * bundler honoured it. This reads build/ from the last `npm run build`.
 *
 * Baseline before this work: a single 726.84 kB chunk (211.77 kB gzip), 1759
 * modules, zero code splitting -- Vite itself warned about it.
 *
 * The load graph comes from Rollup's manifest (build.manifest in vite.config.ts),
 * not from regex-scanning minified chunks. That distinction was a real bug while
 * writing this: Vite's __vite__mapDeps preload table lists lazy chunk paths as
 * plain strings, so a scan counts every lazy chunk as eager and reports 744.55 kB
 * of "startup" bytes when the true figure is 367.59 kB.
 *
 * Skips (rather than fails) when build/ is absent or stale, so `vitest` alone
 * still works on a clean checkout. CI runs `npm run build` first, in the same job.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const BUILD = resolve(__dirname, "../../../build");
const ASSETS = resolve(BUILD, "assets");
const MANIFEST = resolve(BUILD, ".vite/manifest.json");
const SRC = resolve(__dirname, "../..");

/** No chunk may exceed this. The stated F6 target. */
const CHUNK_BUDGET_BYTES = 300 * 1024;

/**
 * Newest mtime under a directory tree.
 *
 * Guards against a real false green hit while writing these tests: vitest ran
 * before `npm run build`, so the assertions measured the PREVIOUS build's
 * artifacts and passed on a bundle that no longer existed. A stale build must
 * skip, never silently pass.
 */
function newestMtime(dir: string): number {
  let newest = 0;
  const walk = (d: string) => {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      // Skip the tests themselves: editing a test must not invalidate a build
      // that still matches the application source.
      if (entry.name === "node_modules" || entry.name === "__tests__") continue;
      if (entry.name.startsWith(".")) continue;
      const full = resolve(d, entry.name);
      if (entry.isDirectory()) walk(full);
      else newest = Math.max(newest, statSync(full).mtimeMs);
    }
  };
  walk(dir);
  return newest;
}

const buildIsFresh = (() => {
  if (!existsSync(ASSETS) || !existsSync(MANIFEST)) return false;
  try {
    return newestMtime(ASSETS) >= newestMtime(SRC);
  } catch {
    return false;
  }
})();

type ManifestChunk = {
  file: string;
  isEntry?: boolean;
  imports?: string[];
  dynamicImports?: string[];
};

function manifest(): Record<string, ManifestChunk> {
  return JSON.parse(readFileSync(MANIFEST, "utf8"));
}

function bytes(file: string): number {
  return statSync(resolve(BUILD, file)).size;
}

/**
 * Every JS file reachable from `roots` through STATIC imports only -- i.e. what
 * the engine must parse before it can run a single line of app code. Dynamic
 * imports are deliberately not followed: that is the whole point of splitting.
 */
function staticClosure(m: Record<string, ManifestChunk>, roots: string[]): Set<string> {
  const seen = new Set<string>();
  const queue = [...roots];
  while (queue.length) {
    const file = queue.pop();
    if (!file || seen.has(file) || !file.endsWith(".js")) continue;
    seen.add(file);
    const chunk = Object.values(m).find((c) => c.file === file);
    for (const key of chunk?.imports ?? []) {
      const dep = m[key];
      if (dep) queue.push(dep.file);
    }
  }
  return seen;
}

function totalBytes(files: Iterable<string>): number {
  let sum = 0;
  for (const f of files) sum += bytes(f);
  return sum;
}

function entryChunk(m: Record<string, ManifestChunk>): ManifestChunk {
  const entry = Object.values(m).find((c) => c.isEntry);
  if (!entry) throw new Error("manifest has no entry chunk");
  return entry;
}

type Chunk = { name: string; bytes: number };

function jsChunks(): Chunk[] {
  return readdirSync(ASSETS)
    .filter((f) => f.endsWith(".js"))
    .map((name) => ({ name, bytes: statSync(resolve(ASSETS, name)).size }))
    .sort((a, b) => b.bytes - a.bytes);
}

/** Chunk names are content-hashed: match on the stem before the hash. */
function findChunk(chunks: Chunk[], stem: string): Chunk | undefined {
  return chunks.find((c) => c.name.startsWith(stem + "-"));
}

describe.skipIf(!buildIsFresh)("shipped bundle is code-split", () => {
  // Skipped when build/ is missing OR older than src/, so a stale artifact set
  // can never produce a green result.
  it("emits more than one JS chunk", () => {
    expect(jsChunks().length).toBeGreaterThan(1);
  });

  it("ModModal is its own chunk, not part of the entry", () => {
    // 112 KB of source that five components imported statically.
    const chunk = findChunk(jsChunks(), "ModModal");
    expect(chunk, "no ModModal-*.js chunk was emitted").toBeDefined();
    expect(chunk!.bytes).toBeGreaterThan(10_000);
  });

  it.each([
    "GetStartedDialog",
    "SettingsDialog",
    "BackupModal",
    "AssignModIdModal",
    "CrashDetectorModal",
  ])("%s is split out of the entry chunk", (stem) => {
    expect(findChunk(jsChunks(), stem), `no ${stem}-*.js chunk`).toBeDefined();
  });

  it.each(["DownloadsPage", "ActiveModsView", "CollectionsPage"])(
    "tab page %s is its own chunk",
    (stem) => {
      expect(findChunk(jsChunks(), stem), `no ${stem}-*.js chunk`).toBeDefined();
    },
  );

  it("NO chunk exceeds the 300 kB budget", () => {
    // The F6 target. Before page-level splitting the entry alone was 434.10 kB.
    const offenders = jsChunks().filter((c) => c.bytes > CHUNK_BUDGET_BYTES);
    expect(
      offenders.map((c) => `${c.name} ${(c.bytes / 1024).toFixed(2)} kB`),
      `chunks over ${CHUNK_BUDGET_BYTES / 1024} kB`,
    ).toEqual([]);
  });

  it("the entry chunk is materially smaller than the 726.84 kB baseline", () => {
    const BASELINE_BYTES = 726_840;
    const entry = jsChunks().find((c) => c.name.startsWith("index-"));
    expect(entry, "no index-*.js entry chunk").toBeDefined();
    expect(entry!.bytes).toBeLessThan(BASELINE_BYTES);
    // Require a real win, not noise.
    expect(BASELINE_BYTES - entry!.bytes).toBeGreaterThan(50_000);
  });

  it("the React runtime is its own chunk", () => {
    expect(findChunk(jsChunks(), "vendor-react")).toBeDefined();
  });

  it("no tab page is in the eager static graph", () => {
    // The claim page-level splitting actually makes: opening the app must not
    // parse the two tabs the user is not on. Asserted against Rollup's own
    // import graph, so a stray static import anywhere in the tree fails here.
    const m = manifest();
    const eager = staticClosure(m, [entryChunk(m).file]);
    for (const page of ["ActiveModsView", "CollectionsPage", "DownloadsPage"]) {
      const chunk = m[`src/components/${page}.tsx`];
      expect(chunk, `${page} is not a manifest entry -- it was inlined`).toBeDefined();
      expect(
        eager.has(chunk!.file),
        `${page} is statically reachable from the entry, so lazy() bought nothing`,
      ).toBe(false);
    }
  });

  it("EAGER startup bytes are far below the baseline", () => {
    // The metric that actually matters for a Tauri app loading from local disk:
    // bytes parsed before anything renders.
    //
    //   726.84 kB  original single chunk
    //   574.29 kB  after lazy modals + vendor-react (pages still eager)
    //   367.59 kB  after page-level splitting
    const m = manifest();
    const eagerBytes = totalBytes(staticClosure(m, [entryChunk(m).file]));
    expect(eagerBytes).toBeLessThan(574_290);
    expect(eagerBytes).toBeLessThanOrEqual(400_000);
  });

  it("FIRST PAINT bytes -- entry plus the default tab -- beat the baseline", () => {
    // Honest companion to the metric above. The app opens on "downloads", so that
    // page's chunk graph is fetched immediately; counting only the static closure
    // would understate real startup cost.
    //
    //   574.29 kB  before (everything was in the entry)
    //   515.36 kB  after
    //
    // The modest delta is expected: the visible page genuinely needs its own UI.
    // The win is that the other two tabs and their exclusive dependencies no
    // longer load at all until visited.
    const m = manifest();
    const downloads = m["src/components/DownloadsPage.tsx"];
    expect(downloads, "DownloadsPage missing from manifest").toBeDefined();
    const firstPaint = staticClosure(m, [entryChunk(m).file, downloads!.file]);
    expect(totalBytes(firstPaint)).toBeLessThan(574_290);
  });

  it("modal-only dependencies stay INSIDE the lazy chunks", () => {
    // Regression guard for a mistake made while writing this config. An earlier
    // manualChunks also split @radix-ui and a catch-all "vendor" chunk. Radix
    // modules used only by the lazy modals were hoisted into an EAGER vendor
    // chunk, pulling previously-deferred code back into the startup path and
    // pushing eager bytes from 563.46 kB to 593.43 kB.
    //
    // ModModal shrank from 71.14 kB to 44.16 kB under that config -- the missing
    // 27 kB had moved into the eager graph. So a small ModModal chunk is the
    // symptom to watch for.
    const modModal = findChunk(jsChunks(), "ModModal");
    expect(modModal).toBeDefined();
    expect(
      modModal!.bytes,
      "ModModal chunk shrank: its dependencies were probably hoisted into an " +
        "eagerly-loaded vendor chunk, which defeats the lazy load",
    ).toBeGreaterThan(60_000);

    // And there must be no catch-all eager vendor chunk beyond the React one.
    const vendorChunks = jsChunks().filter((c) => c.name.startsWith("vendor"));
    expect(vendorChunks.map((c) => c.name.replace(/-[^-]+\.js$/, ""))).toEqual([
      "vendor-react",
    ]);
  });
});
