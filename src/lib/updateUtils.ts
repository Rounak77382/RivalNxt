import type { ApiDownload } from "./api";

/**
 * Normalizes a version string for robust comparison (e.g. "v1.2" vs "1.2")
 */
export function normalizeVersionForCheck(v: string | null | undefined): string {
  if (!v) return "";
  let cleaned = v.replace(/\.\d{9,11}$/, "").toLowerCase();
  if (!cleaned.startsWith("v")) cleaned = "v" + cleaned;
  cleaned = cleaned.replace(/^vs/, "v");
  cleaned = cleaned.replace(/-w\d*$/, "");
  return cleaned;
}

/**
 * Determines if a specific downloaded variant genuinely has an update available.
 * It checks if the backend flagged it for an update, AND ensures the newer version
 * isn't already installed alongside it as a separate variant in the same mod group.
 */
export function isVariantActuallyUpdatable(
  variant: { needs_update?: boolean | null; latest_version?: string | null },
  allVariantsForMod: { version?: string | null }[]
): boolean {
  if (!variant.needs_update) return false;
  if (!variant.latest_version) return true; // If missing, trust backend

  const normalizedLatest = normalizeVersionForCheck(variant.latest_version);

  // Check if any installed variant already has this "latest" version
  for (const other of allVariantsForMod) {
    if (!other.version) continue;
    
    // If we already have a local variant matching the latest remote version, 
    // then this update is redundant (the user already downloaded the newer file).
    if (normalizeVersionForCheck(other.version) === normalizedLatest) {
      return false;
    }
  }

  return true;
}
