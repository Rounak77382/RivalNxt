/**
 * H4 (frontend): v1 (localStorage-era) and v2 (backend API) backups must both
 * be readable, and migration must be lossless.
 *
 * v1 backups were a JSON projection of mod metadata written by the frontend and
 * indexed in localStorage -- so clearing webview storage orphaned every file on
 * disk. v2 backups are backend-produced zips containing the real mods.db, with
 * the filesystem as the index.
 *
 * NOTE: this suite has NOT been executed. Node.js/npm are not installed in the
 * environment where it was written, so `npm run test` could not be run. It is
 * expected to pass under the `frontend` CI job.
 */
import { beforeEach, describe, expect, it } from "vitest";
import {
  addBackupMeta,
  fromLegacyMeta,
  fromServerBackup,
  loadBackupMetas,
  mergeBackupSources,
  removeBackupMeta,
  type BackupMeta,
  type ServerBackupInfo,
} from "../backupUtils";

const legacy = (over: Partial<BackupMeta> = {}): BackupMeta => ({
  id: "legacy-1",
  name: "My Old Backup",
  createdAt: "2025-01-01T00:00:00.000Z",
  filePath: "C:/backups/old.json",
  totalMods: 12,
  activeMods: 5,
  ...over,
});

const server = (over: Partial<ServerBackupInfo> = {}): ServerBackupInfo => ({
  name: "Nightly",
  path: "C:/data/backups/Nightly-2026-02-01.zip",
  created_at: "2026-02-01T00:00:00.000Z",
  size_bytes: 4096,
  manifest_version: 2,
  total_mods: 40,
  active_mods: 18,
  ...over,
});

const mockStorage = new Map<string, string>();
Object.defineProperty(global, 'localStorage', {
  value: {
    getItem: (key: string) => mockStorage.get(key) ?? null,
    setItem: (key: string, value: string) => mockStorage.set(key, value.toString()),
    removeItem: (key: string) => mockStorage.delete(key),
    clear: () => mockStorage.clear(),
  },
  writable: true
});

beforeEach(() => {
  localStorage.clear();
});

describe("v1 -> unified migration is lossless", () => {
  it("preserves every field of a legacy meta", () => {
    const meta = legacy();
    const unified = fromLegacyMeta(meta);

    expect(unified.id).toBe(meta.id);
    expect(unified.name).toBe(meta.name);
    expect(unified.createdAt).toBe(meta.createdAt);
    expect(unified.filePath).toBe(meta.filePath);
    expect(unified.totalMods).toBe(meta.totalMods);
    expect(unified.activeMods).toBe(meta.activeMods);
  });

  it("marks legacy entries as generation 1 and not API-restorable", () => {
    const unified = fromLegacyMeta(legacy());
    expect(unified.generation).toBe(1);
    expect(unified.restorableViaApi).toBe(false);
  });

  it("round-trips through localStorage without loss", () => {
    const meta = legacy();
    addBackupMeta(meta);
    const [loaded] = loadBackupMetas();
    expect(fromLegacyMeta(loaded)).toEqual(fromLegacyMeta(meta));
  });
});

describe("v2 manifest mapping", () => {
  it("maps the backend shape onto the unified shape", () => {
    const unified = fromServerBackup(server());
    expect(unified.name).toBe("Nightly");
    expect(unified.filePath).toBe("C:/data/backups/Nightly-2026-02-01.zip");
    expect(unified.createdAt).toBe("2026-02-01T00:00:00.000Z");
    expect(unified.totalMods).toBe(40);
    expect(unified.activeMods).toBe(18);
    expect(unified.generation).toBe(2);
    expect(unified.restorableViaApi).toBe(true);
  });

  it("uses the archive path as a stable id", () => {
    const info = server();
    expect(fromServerBackup(info).id).toBe(info.path);
  });

  it("tolerates a manifest with missing counts", () => {
    const unified = fromServerBackup(
      server({ total_mods: null, active_mods: null, created_at: null }),
    );
    expect(unified.totalMods).toBe(0);
    expect(unified.activeMods).toBe(0);
    expect(unified.createdAt).toBe("");
  });
});

describe("mergeBackupSources", () => {
  it("returns both generations", () => {
    const merged = mergeBackupSources([legacy()], [server()]);
    expect(merged).toHaveLength(2);
    expect(merged.map((m) => m.generation).sort()).toEqual([1, 2]);
  });

  it("sorts newest first", () => {
    const merged = mergeBackupSources(
      [legacy({ createdAt: "2025-01-01T00:00:00.000Z" })],
      [server({ created_at: "2026-02-01T00:00:00.000Z" })],
    );
    expect(merged[0].createdAt).toBe("2026-02-01T00:00:00.000Z");
    expect(merged[1].createdAt).toBe("2025-01-01T00:00:00.000Z");
  });

  it("prefers the v2 record when both describe the same file", () => {
    const shared = "C:/data/backups/same.zip";
    const merged = mergeBackupSources(
      [legacy({ filePath: shared })],
      [server({ path: shared })],
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].generation).toBe(2);
    expect(merged[0].restorableViaApi).toBe(true);
  });

  it("handles an empty legacy index (the post-storage-clear case)", () => {
    // This is the exact failure mode of the old design: with localStorage gone,
    // v1 surfaced nothing. v2 archives are still listed because the filesystem
    // is the index.
    const merged = mergeBackupSources([], [server(), server({ path: "b.zip" })]);
    expect(merged).toHaveLength(2);
    expect(merged.every((m) => m.restorableViaApi)).toBe(true);
  });

  it("handles an empty server list", () => {
    const merged = mergeBackupSources([legacy()], []);
    expect(merged).toHaveLength(1);
    expect(merged[0].generation).toBe(1);
  });

  it("handles both empty", () => {
    expect(mergeBackupSources([], [])).toEqual([]);
  });

  it("keeps entries with no timestamp last", () => {
    const merged = mergeBackupSources(
      [legacy({ createdAt: "" })],
      [server({ created_at: "2026-02-01T00:00:00.000Z" })],
    );
    expect(merged[0].createdAt).toBe("2026-02-01T00:00:00.000Z");
    expect(merged[1].createdAt).toBe("");
  });
});

describe("legacy index management still works", () => {
  it("adds and removes metas", () => {
    addBackupMeta(legacy({ id: "a" }));
    addBackupMeta(legacy({ id: "b", filePath: "C:/backups/b.json" }));
    expect(loadBackupMetas()).toHaveLength(2);

    removeBackupMeta("a");
    const remaining = loadBackupMetas();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].id).toBe("b");
  });

  it("returns an empty array when storage is empty or corrupt", () => {
    expect(loadBackupMetas()).toEqual([]);
    localStorage.setItem("rivalnxt:backups", "{not json");
    expect(loadBackupMetas()).toEqual([]);
  });
});
