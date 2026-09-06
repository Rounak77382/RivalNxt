
/**
 * Normalizes a version string for robust comparison (e.g. "v1.2" vs "1.2")
 */
export function normalizeVersionForCheck(v: string | null | undefined): string {
  if (!v) return "";
  let cleaned = v.trim().replace(/\.\d{9,11}$/, "").toLowerCase();
  if (!cleaned.startsWith("v")) cleaned = "v" + cleaned;
  cleaned = cleaned.replace(/^vs/, "v");
  cleaned = cleaned.replace(/-w\d*$/, "");
  return cleaned;
}

/**
 * Checks if two version strings are equivalent (directional: local can have extra artifact segments).
 */
export function versionsEquivalent(
  local: string | null | undefined,
  remote: string | null | undefined,
): boolean {
  if (!local || !remote) return false;
  const lNorm = normalizeVersionForCheck(local);
  const rNorm = normalizeVersionForCheck(remote);
  if (lNorm === rNorm) return true;

  const lClean = lNorm.replace(/^v/, "");
  const rClean = rNorm.replace(/^v/, "");

  const lParts = lClean.split(/[^0-9]+/).filter(Boolean).map(Number);
  const rParts = rClean.split(/[^0-9]+/).filter(Boolean).map(Number);

  if (lParts.length === 0 || rParts.length === 0) return false;

  // Remote is more precise than local (e.g. local "2" vs remote "2.5") -> NOT equivalent
  if (rParts.length > lParts.length) return false;

  for (let i = 0; i < rParts.length; i++) {
    if (lParts[i] !== rParts[i]) return false;
  }

  return true;
}

/**
 * Normalizes a filename or variant name to a canonical identity key (strips versions, timestamps, extensions).
 */
export function getVariantIdentityKey(name: string | null | undefined, modId?: number | null): string {
  if (!name) return modId != null ? `mod:${modId}` : "";
  let clean = name
    .toLowerCase()
    .replace(/\.(zip|rar|7z|pak|utoc|ucas)$/i, "")
    .replace(/[-_\s]\d{9,11}(?:[-_\s]\d+)?$/i, "")
    .replace(/[-_\s]v?\d+(?:[-_.]\d+)*$/i, "")
    .replace(/[^a-z0-9]/g, "");
  return clean || (modId != null ? `mod:${modId}` : name.toLowerCase().trim());
}

/**
 * Determines if a specific variant in a mod's download list is genuinely updatable
 * (i.e. not superseded by a newer local version of the same variant/mod, and not matching latest).
 */
export function isVariantActuallyUpdatable(
  variant: any,
  allVariants: any[],
  overallLatestVersion?: string | null,
  overallLatestKey?: string | null,
): boolean {
  if (!variant || !variant.needs_update) return false;
  const vVer = variant.version;
  const vLatest = variant.latest_version || overallLatestVersion;
  if (!vVer || !vLatest) return false;

  if (versionsEquivalent(vVer, vLatest)) return false;

  if (
    variant.local_version_key &&
    variant.latest_version_key &&
    variant.local_version_key >= variant.latest_version_key
  ) {
    return false;
  }

  // Check if another download in allVariants for the same variant identity is newer and already up-to-date
  const myKey = getVariantIdentityKey(variant.name || variant.mod_name, variant.mod_id);
  for (const other of allVariants) {
    if (other === variant) continue;
    const otherKey = getVariantIdentityKey(other.name || other.mod_name, other.mod_id);
    if (otherKey && otherKey === myKey) {
      const otherIsNewer =
        (other.local_version_key && variant.local_version_key && other.local_version_key > variant.local_version_key) ||
        (new Date(other.created_at || 0).getTime() > new Date(variant.created_at || 0).getTime());

      if (otherIsNewer) {
        if (
          !other.needs_update ||
          versionsEquivalent(other.version, other.latest_version || vLatest) ||
          (other.local_version_key && (other.latest_version_key || overallLatestKey) &&
            other.local_version_key >= (other.latest_version_key || overallLatestKey))
        ) {
          return false;
        }
      }
    }
  }

  return true;
}

/**
 * Minimal shape of a Mod required for update count computation.
 * Keeps updateUtils.ts free of component imports.
 */
export interface UpdateCountMod {
  id: string;
  backendModId?: number | null;
  name?: string;
  hasUpdate?: boolean;
  installedVersion?: string;
  latestVersion?: string;
  localVersionKey?: string | null;
  latestVersionKey?: string | null;
  updateVariantName?: string | null;
}

/**
 * Canonical update count: deduplicates by backendModId (or name fallback),
 * suppresses false positives where installed version matches latest.
 *
 * This is the single source of truth used by the sidebar badge, the
 * Check-for-Updates modal header, and any other UI that shows a count.
 */
export function computeUpdatesCount(mods: UpdateCountMod[]): number {
  const seen = new Set<string>();
  for (const mod of mods) {
    if (!mod.hasUpdate) continue;

    // Suppress if version already matches latest and no sub-variant explicitly needs update
    if (
      mod.installedVersion &&
      mod.latestVersion &&
      !mod.updateVariantName &&
      (versionsEquivalent(mod.installedVersion, mod.latestVersion) ||
        (mod.localVersionKey && mod.latestVersionKey && mod.localVersionKey >= mod.latestVersionKey))
    ) {
      continue;
    }

    if (typeof mod.backendModId === "number" && Number.isFinite(mod.backendModId)) {
      seen.add(`id:${String(mod.backendModId)}`);
    } else if (mod.name) {
      seen.add(`name:${String(mod.name).toLowerCase().trim()}`);
    } else {
      seen.add(`internal:${String(mod.id)}`);
    }
  }
  return seen.size;
}

/**
 * Returns the deduplicated set of mods that genuinely have updates,
 * using the same logic as computeUpdatesCount.
 * Each backendModId appears at most once (keeps the first occurrence).
 */
export function getModsWithUpdates<T extends UpdateCountMod>(mods: T[]): T[] {
  const seen = new Set<string>();
  const result: T[] = [];
  for (const mod of mods) {
    if (!mod.hasUpdate) continue;

    if (
      mod.installedVersion &&
      mod.latestVersion &&
      !mod.updateVariantName &&
      (versionsEquivalent(mod.installedVersion, mod.latestVersion) ||
        (mod.localVersionKey && mod.latestVersionKey && mod.localVersionKey >= mod.latestVersionKey))
    ) {
      continue;
    }

    let key: string;
    if (typeof mod.backendModId === "number" && Number.isFinite(mod.backendModId)) {
      key = `id:${String(mod.backendModId)}`;
    } else if (mod.name) {
      key = `name:${String(mod.name).toLowerCase().trim()}`;
    } else {
      key = `internal:${String(mod.id)}`;
    }

    if (!seen.has(key)) {
      seen.add(key);
      result.push(mod);
    }
  }
  return result;
}
