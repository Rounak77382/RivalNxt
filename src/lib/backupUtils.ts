/**
 * Mod Backup System – data model, localStorage helpers, and backup I/O logic.
 */

// ─── Data Model ─────────────────────────────────────────────────────────────

/** Represents a single mod entry stored inside a backup file. */
export interface ModBackupEntry {
  modId: string;
  backendModId: number | null;
  name: string;
  author: string;
  version: string;
  isActive: boolean;
  images: string[];
  sourceDownloadIds: number[];
  sourceFileIds: number[];
  activePaks?: string[];
  /** User-created custom tags for this mod (optional, absent in older backups). */
  customTags?: string[];
  /** Custom description for this mod (optional). */
  description?: string | null;
  /** Custom images uploaded for this mod (optional). */
  customImages?: { data: string; filename?: string; mimeType?: string }[];
  /** Custom Author Metadata (optional) */
  customAuthorId?: number | null;
  customAuthorName?: string | null;
  customAuthorType?: string | null;
  customAuthorAvatar?: string | null;
}

/** The full backup object saved to disk as JSON. */
export interface ModBackup {
  id: string;
  name: string;
  createdAt: string;        // ISO timestamp
  totalMods: number;
  activeMods: number;
  mods: ModBackupEntry[];
}

/** Lightweight metadata stored in localStorage (without the full mod list). */
export interface BackupMeta {
  id: string;
  name: string;
  createdAt: string;
  filePath: string;         // absolute path to the .json file on disk
  totalMods: number;
  activeMods: number;
}

// ─── localStorage ────────────────────────────────────────────────────────────

const LS_KEY = "rivalnxt:backups";

export function loadBackupMetas(): BackupMeta[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as BackupMeta[];
  } catch {
    return [];
  }
}

export function saveBackupMetas(metas: BackupMeta[]): void {
  localStorage.setItem(LS_KEY, JSON.stringify(metas));
}

export function addBackupMeta(meta: BackupMeta): void {
  const metas = loadBackupMetas();
  // Newest first
  metas.unshift(meta);
  saveBackupMetas(metas);
}

export function removeBackupMeta(id: string): void {
  const metas = loadBackupMetas().filter((m) => m.id !== id);
  saveBackupMetas(metas);
}

// ─── Snapshot builder ────────────────────────────────────────────────────────

/**
 * Builds a ModBackup snapshot from the current UI mod list.
 * Captures all installed mods with their active/inactive state.
 */
export function buildBackupFromMods(mods: any[], name: string): ModBackup {
  const id = `backup_${Date.now()}`;
  const createdAt = new Date().toISOString();

  const entries: ModBackupEntry[] = mods
    .filter((m) => m.isInstalled)
    .map((m) => ({
      modId: String(m.id),
      backendModId: m.backendModId ?? null,
      name: m.name || "Unknown Mod",
      author: m.author || "",
      version: m.version || "",
      isActive: Boolean(m.isActive),
      images: Array.isArray(m.images) ? m.images.slice(0, 1) : [],
      sourceDownloadIds: Array.isArray(m.sourceDownloadIds)
        ? m.sourceDownloadIds
        : [],
      sourceFileIds: Array.isArray(m.sourceFileIds) ? m.sourceFileIds : [],
      activePaks: Array.isArray(m.defaultActivePaks) ? m.defaultActivePaks : [],
      customAuthorId: m.customAuthorId ?? null,
      customAuthorName: m.customAuthorName ?? null,
      customAuthorType: m.customAuthorType ?? null,
      customAuthorAvatar: m.customAuthorAvatar ?? null,
    }));

  return {
    id,
    name,
    createdAt,
    totalMods: entries.length,
    activeMods: entries.filter((e) => e.isActive).length,
    mods: entries,
  };
}

/**
 * Generates a friendly datetime name for a new backup.
 * Example: "2026-05-16 19:18"
 */
export function generateBackupName(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(
    now.getDate()
  )} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

// ─── Restore logic ───────────────────────────────────────────────────────────

export interface RestoreResult {
  /** Mods that were toggled (active state changed). */
  toggled: string[];
  /** Mod names that are in the backup but not currently installed. */
  missing: string[];
}

/**
 * Compares a backup against the current installed mods list and returns
 * which mods need toggling and which are missing.
 *
 * Does NOT call onToggleMod — caller is responsible for applying the changes.
 */
export function computeRestoreDiff(
  backup: ModBackup,
  installedMods: any[]
): { toEnable: any[]; toDisable: any[]; missing: string[] } {
  const toEnable: any[] = [];
  const toDisable: any[] = [];
  const missing: string[] = [];

  for (const entry of backup.mods) {
    // Match by backendModId first, then sourceDownloadIds, then modId
    const match = installedMods.find((m) => {
      if (entry.backendModId != null && m.backendModId != null) {
        return m.backendModId === entry.backendModId;
      }
      if (
        entry.sourceDownloadIds.length > 0 &&
        Array.isArray(m.sourceDownloadIds)
      ) {
        return entry.sourceDownloadIds.some((id) =>
          m.sourceDownloadIds.includes(id)
        );
      }
      return String(m.id) === entry.modId;
    });

    if (!match) {
      missing.push(entry.name);
      continue;
    }

    const currentlyActive = Boolean(match.isActive);
    if (entry.isActive && !currentlyActive) {
      toEnable.push(match);
    } else if (!entry.isActive && currentlyActive) {
      toDisable.push(match);
    }
  }

  return { toEnable, toDisable, missing };
}
