/**
 * F5/F6: assert the SHIPPED bundle is actually split.
 *
 * Unit-testing `lazy()` proves the pattern; only the build output proves the
 * bundler honoured it. This reads build/assets from the last `npm run build`.
 *
 * Baseline before this work: a single 726.84 kB chunk (211.77 kB gzip), 1759
 * modules, zero code splitting — Vite itself warned about it.
 *
 * Skips (rather than fails) when build/ is absent, so `vitest` alone still works
 * on a clean checkout. CI runs `npm run build` in the same job.
 */
import { existsSync, readdirSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ASSETS = resolve(__dirname, "../../../build/assets");
const hasBuild = existsSync(ASSETS);

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

describe.skipIf(!hasBuild)("shipped bundle is code-split", () => {
  it("emits more than one JS chunk", () => {
    const chunks = jsChunks();
    expect(chunks.length).toBeGreaterThan(1);
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

  it("the entry chunk is materially smaller than the 726.84 kB baseline", () => {
    const BASELINE_BYTES = 726_840;
    const chunks = jsChunks();
    // The entry is the largest remaining chunk that is not one of the split-out
    // modals.
    const entry = chunks.find((c) => c.name.startsWith("index-"));
    expect(entry, "no index-*.js entry chunk").toBeDefined();
    expect(entry!.bytes).toBeLessThan(BASELINE_BYTES);

    const saved = BASELINE_BYTES - entry!.bytes;
    // Require a real win, not noise.
    expect(saved).toBeGreaterThan(50_000);
  });

  it("no single chunk regresses past the old monolith", () => {
    for (const c of jsChunks()) {
      expect(c.bytes, `${c.name} is larger than the pre-split bundle`).toBeLessThan(
        726_840,
      );
    }
  });

  it("the React runtime is its own chunk", () => {
    expect(findChunk(jsChunks(), "vendor-react")).toBeDefined();
  });

  it("EAGER startup bytes are below the baseline", () => {
    // The metric that actually matters for a Tauri app: everything statically
    // imported by the entry is parsed at startup, whether or not it is a separate
    // file. Lazy modal chunks are excluded because they are fetched on demand.
    const chunks = jsChunks();
    const LAZY_STEMS = [
      "ModModal",
      "GetStartedDialog",
      "SettingsDialog",
      "BackupModal",
      "AssignModIdModal",
      "CrashDetectorModal",
      "TaskOutputSummary",
      "textarea",
      "chevron-up",
    ];
    const eager = chunks.filter(
      (c) => !LAZY_STEMS.some((stem) => c.name.startsWith(stem + "-")),
    );
    const eagerBytes = eager.reduce((sum, c) => sum + c.bytes, 0);

    expect(eagerBytes).toBeLessThan(726_840);
    // Must beat the un-chunked F5 result too, or manualChunks is not earning its
    // keep. (Measured: 563.46 kB unsplit vs 561.34 kB with the React-only split.)
    expect(eagerBytes).toBeLessThanOrEqual(570_000);
  });

  it("modal-only dependencies stay INSIDE the lazy chunks", () => {
    // Regression guard for a mistake made while writing this config. An earlier
    // manualChunks also split @radix-ui and a catch-all "vendor" chunk. Radix
    // modules used only by the lazy modals were hoisted into an EAGER vendor
    // chunk, pulling previously-deferred code back into the startup path and
    // pushing eager bytes from 563.46 kB to 593.43 kB.
    //
    // ModModal shrank from 71.14 kB to 44.16 kB under that config — the missing
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
