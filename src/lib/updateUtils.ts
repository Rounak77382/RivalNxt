
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

