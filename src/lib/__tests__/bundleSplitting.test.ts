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
});
