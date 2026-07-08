/**
 * crashParser.ts
 * Parses a Marvel Rivals UE4 CrashContext.runtime-xml file and correlates
 * the crash with currently active mods.
 */

import type { ApiDownload } from "./api";

export interface CrashInfo {
  /** Short error / condition message from RuntimeConditions */
  errorMessage: string;
  /** UTC timestamp of crash, e.g. "2025-07-07T16:00:00Z" */
  timeOfCrash: string | null;
  /** UE crash GUID */
  crashGuid: string | null;
  /** Engine version string */
  engineVersion: string | null;
  /** Simplified call stack entries */
  callStack: string[];
  /** Full raw XML content for the "show raw log" expander */
  rawXml: string;
  /**
   * Extracted asset paths that might be causing the crash
   * (e.g. from ObjectSerializationError). Paths are relative to /Game/,
   * e.g. "Marvel/Characters/1020/1020100/1020100_ChildBP"
   */
  faultyAssetPaths: string[];
}

/**
 * Parsed skin coordinate extracted from a faulty asset path.
 * For /Game/Marvel/Characters/CHAR/SKIN/... we get charId="CHAR" skinId="SKIN".
 */
interface SkinCoord {
  charId: string; // e.g. "1020"
  skinId: string; // e.g. "1020100"
  skinPrefix: string; // e.g. "Marvel/Characters/1020/1020100/"  (lowercase)
  charPrefix: string; // e.g. "Marvel/Characters/1020/"          (lowercase)
}

/**
 * Extract the text content of the first matching XML element.
 * Uses the browser's DOMParser so no external deps needed.
 */
function getElementText(doc: Document, tagName: string): string | null {
  const el = doc.querySelector(tagName);
  return el?.textContent?.trim() || null;
}

/**
 * Parse CrashContext.runtime-xml content into a structured CrashInfo object.
 */
export function parseCrashContext(xml: string): CrashInfo {
  let doc: Document | null = null;

  try {
    const parser = new DOMParser();
    doc = parser.parseFromString(xml, "application/xml");
    // DOMParser won't throw on malformed XML – it returns a parseerror document
    const parseError = doc.querySelector("parsererror");
    if (parseError) {
      doc = null;
    }
  } catch {
    doc = null;
  }

  if (!doc) {
    // Fallback: return minimal info from raw text
    return {
      errorMessage: "Unable to parse crash report",
      timeOfCrash: null,
      crashGuid: null,
      engineVersion: null,
      callStack: [],
      rawXml: xml,
      faultyAssetPaths: [],
    };
  }

  // --- Extract fields ---

  // RuntimeConditions is the human-readable error message
  const runtimeConditions = getElementText(doc, "RuntimeConditions");

  // Some crash reports use ProblemSignature or ExceptionCode
  const problemSignature = getElementText(doc, "ProblemSignatureLine1");
  const exceptionCode = getElementText(doc, "ExceptionCode");

  const errorMessage =
    runtimeConditions ||
    problemSignature ||
    (exceptionCode ? `Exception: ${exceptionCode}` : "Unknown crash error");

  const timeOfCrash = getElementText(doc, "TimeOfCrash");
  const crashGuid = getElementText(doc, "CrashGUID");
  const engineVersion = getElementText(doc, "EngineVersion");

  // Call stack — look for several common formats
  const callStack: string[] = [];
  const callStackEl =
    doc.querySelector("CallStack") ||
    doc.querySelector("PortableCallStack") ||
    doc.querySelector("ModuleName"); // fallback

  if (callStackEl) {
    const raw = callStackEl.textContent || "";
    // Split by newlines and clean up
    const lines = raw
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l.length > 0 && !l.startsWith("0x0000000000000000"));
    callStack.push(...lines.slice(0, 30)); // cap at 30 frames
  }

  // Also collect all PortableCallStack entries if they exist
  if (callStack.length === 0) {
    const entries = doc.querySelectorAll("PortableCallStack Entry");
    entries.forEach((e) => {
      const text = e.textContent?.trim();
      if (text) callStack.push(text);
    });
  }

  // Extract faulty asset paths from the full XML.
  // e.g. ObjectSerializationError: /Game/Marvel/Characters/1020/1020100/1020100_ChildBP
  const assetRegex = /\/Game\/([a-zA-Z0-9_\-/.]+)/gi;
  const rawXmlMatches = Array.from(new Set(
    Array.from(xml.matchAll(assetRegex)).map((m) =>
      // Trim trailing dots, slashes, quotes, parens
      m[1].replace(/[./"'\s()]+$/, "")
    )
  ));

  return {
    errorMessage,
    timeOfCrash,
    crashGuid,
    engineVersion,
    callStack,
    rawXml: xml,
    faultyAssetPaths: rawXmlMatches,
  };
}

// ---------------------------------------------------------------------------
// Skin-coordinate helpers
// ---------------------------------------------------------------------------

/**
 * Parse skin coordinates out of a raw faulty asset path string.
 * Recognises the Marvel Rivals layout:
 *   Marvel/Characters/<charId>/<skinId>/...
 * Returns null if the path doesn't match that structure.
 */
function parseSkinCoord(assetPath: string): SkinCoord | null {
  // Match Marvel/Characters/1020/1020100/... (case-insensitive)
  const m = assetPath.match(
    /marvel\/characters\/([0-9]{4,6})\/([0-9]{4,8})\//i,
  );
  if (!m) return null;
  const charId = m[1];
  const skinId = m[2];
  return {
    charId,
    skinId,
    skinPrefix: `marvel/characters/${charId}/${skinId}/`,
    charPrefix: `marvel/characters/${charId}/`,
  };
}

/**
 * Given a list of faulty asset paths, return the most specific skin coords
 * mentioned. Deduplicates by skinId.
 */
function extractSkinCoords(faultyAssetPaths: string[]): SkinCoord[] {
  const seen = new Set<string>();
  const coords: SkinCoord[] = [];
  for (const p of faultyAssetPaths) {
    const coord = parseSkinCoord(p.toLowerCase());
    if (coord && !seen.has(coord.skinId)) {
      seen.add(coord.skinId);
      coords.push(coord);
    }
  }
  return coords;
}

// ---------------------------------------------------------------------------
// PAK companion-pak analysis
// ---------------------------------------------------------------------------

/**
 * Marvel Rivals mod authors often split a skin mod into:
 *   - BASE pak  (mesh/skeleton/materials)  – name typically contains "BASE", "zBASE", etc.
 *   - FIX pak   (patches on top of BASE)   – name typically contains "FIX", "zFIX", etc.
 *
 * If only the FIX pak is active without the BASE pak, the engine tries to
 * deserialize a ChildBP that references name-table entries only present in
 * the BASE pak → "Bad name index" crash.
 *
 * This function checks whether a mod has a known FIX pak active but is
 * missing a corresponding BASE pak in the SAME download.
 */
function hasMissingCompanionPak(mod: ApiDownload): boolean {
  const activePakNames = (mod.active_paks || []).map((p) =>
    p.replace(/\\/g, "/").split("/").pop()?.toLowerCase() ?? "",
  );
  const allPakNames = (mod.contents || []).map((p) =>
    p.replace(/\\/g, "/").split("/").pop()?.toLowerCase() ?? "",
  );

  // Check if any ACTIVE pak is a FIX pak
  const hasFix = activePakNames.some((n) =>
    /[_\-](fix|zfix|patch|zpatch)[_\-0-9]/i.test(n),
  );
  if (!hasFix) return false;

  // Check that no BASE pak is active in the same mod
  const hasActiveBase = activePakNames.some((n) =>
    /[_\-](base|zbase)[_\-0-9]/i.test(n),
  );
  if (hasActiveBase) return false;

  // There IS a base pak in this download but it's not active
  const hasInactiveBase = allPakNames.some(
    (n) =>
      /[_\-](base|zbase)[_\-0-9]/i.test(n) && !activePakNames.includes(n),
  );

  return hasInactiveBase;
}

// ---------------------------------------------------------------------------
// Known UE crash patterns
// ---------------------------------------------------------------------------

/**
 * Known UE crash patterns that strongly suggest a mod-related IO/asset fault.
 * If ANY of these appear in the call stack, all active mods are considered suspicious.
 */
const MOD_CRASH_PATTERNS = [
  /FIoStore/i,
  /FPakFile/i,
  /LoadPackage/i,
  /AsyncLoadingThread/i,
  /FAsyncPackage/i,
  /FLinkerLoad/i,
  /UObjectLinker/i,
  /PakManager/i,
  /IoDispatcher/i,
  /FIoDispatcher/i,
  /FIoBatch/i,
  /OpenRead/i,
  /GetMountedPakFilenames/i,
  /Decompress/i,
  /FAESKey/i,
  /CrashInEngineCode/i,
  /ObjectSerializationError/i,
  /Bad name index/i,
];

// ---------------------------------------------------------------------------
// Match-type definition
// ---------------------------------------------------------------------------

export interface SuspiciousMod {
  mod: ApiDownload;
  /**
   * How confident we are that this mod caused the crash:
   *   "exact"     – a PAK in this mod directly contains the crashing asset file
   *   "skin"      – a PAK in this mod touches the same skin subfolder (charId+skinId)
   *                 OR the mod has an active FIX pak without its BASE pak
   *   "character" – a PAK in this mod touches the same character but a different skin
   *   "fallback"  – crash text / call stack references a PAK name from this mod
   */
  matchType: "exact" | "skin" | "character" | "fallback";
  /** Human-readable reason shown in the UI tooltip */
  reason?: string;
}

// ---------------------------------------------------------------------------
// Main correlation function
// ---------------------------------------------------------------------------

/**
 * Correlate a CrashInfo with a list of downloads that have active PAK files.
 *
 * Returns: { suspicious: SuspiciousMod[], rest: ApiDownload[] }
 *   suspicious = mods whose PAK name appears in the call stack OR that contain
 *                the faulty asset, sorted first
 *   rest = all other active mods
 *
 * Because UE crash reports don't directly name mods, this is HEURISTIC.
 */
export function correlateCrashWithMods(
  crashInfo: CrashInfo,
  downloads: ApiDownload[],
  pakAssets?: import("./api").ApiPakAsset[],
): {
  suspicious: SuspiciousMod[];
  rest: ApiDownload[];
} {
  // Only include downloads that currently have active PAKs
  const activeMods = downloads.filter(
    (d) => d.active_paks && d.active_paks.length > 0,
  );

  if (activeMods.length === 0) {
    return { suspicious: [], rest: [] };
  }

  const callStackText = crashInfo.callStack.join("\n").toLowerCase();
  const rawXmlLower = crashInfo.rawXml.toLowerCase();
  const combinedText = callStackText + " " + rawXmlLower;

  // Check if the crash is IO/asset related (suggests a broken mod file)
  const isModRelatedCrash = MOD_CRASH_PATTERNS.some((pattern) =>
    pattern.test(combinedText),
  );

  // Extract specific skin coordinates from the faulty paths
  const skinCoords = extractSkinCoords(crashInfo.faultyAssetPaths);

  // Build lowercase faulty path set for fast lookup
  const faultyExactLower = new Set(
    crashInfo.faultyAssetPaths
      .filter((p) => !p.endsWith("/"))
      .map((p) => p.toLowerCase()),
  );

  const suspicious: SuspiciousMod[] = [];
  const rest: ApiDownload[] = [];

  for (const mod of activeMods) {
    let matchType: SuspiciousMod["matchType"] | null = null;
    let reason: string | undefined;

    // -----------------------------------------------------------------------
    // Priority 1: Asset-database match (most precise)
    // -----------------------------------------------------------------------
    if (pakAssets && crashInfo.faultyAssetPaths.length > 0) {
      outer: for (const pakPath of mod.active_paks || []) {
        const pakName = pakPath.replace(/\\/g, "/").split("/").pop();
        const pakAssetData = pakAssets.find((p) => p.pak_name === pakName);
        if (!pakAssetData?.assets) continue;

        for (const assetPath of pakAssetData.assets) {
          const assetLower = assetPath.toLowerCase();

          // 1a. Exact file match (highest confidence)
          for (const faulty of faultyExactLower) {
            if (assetLower.includes(faulty)) {
              matchType = "exact";
              reason = `PAK contains the crashing file: ${assetPath}`;
              break outer;
            }
          }

          // 1b. Skin-level match (same charId + skinId subfolder)
          for (const coord of skinCoords) {
            if (assetLower.includes(coord.skinPrefix)) {
              matchType = "skin";
              reason = `PAK modifies the same skin (${coord.charId}/${coord.skinId})`;
              break outer;
            }
          }
        }

        // 1c. Character-level match (same character, different skin)
        for (const assetPath of pakAssetData.assets) {
          const assetLower = assetPath.toLowerCase();
          for (const coord of skinCoords) {
            if (assetLower.includes(coord.charPrefix)) {
              const mt = matchType as string | null;
              if (!mt || mt === "fallback") {
                matchType = "character";
                reason = `PAK modifies character ${coord.charId} (different skin)`;
              }
            }
          }
        }
      }
    }

    // -----------------------------------------------------------------------
    // Priority 2: Companion-pak analysis (FIX without BASE)
    // If the faulty path names a specific skin, and this mod has a FIX pak
    // active for the same skin but is missing its BASE pak → very likely cause
    // -----------------------------------------------------------------------
    if (!(matchType as string | null) || (matchType as string) === "character" || (matchType as string) === "fallback") {
      if (skinCoords.length > 0 && hasMissingCompanionPak(mod)) {
        // Confirm the mod is about the same skin by checking pak names or mod name
        const modNameLower = (mod.name || mod.mod_name || "").toLowerCase();
        const pakNamesLower = (mod.active_paks || []).map((p) =>
          p.replace(/\\/g, "/").split("/").pop()?.toLowerCase() ?? "",
        );
        const combinedModText = modNameLower + " " + pakNamesLower.join(" ");

        const isRelatedSkin = skinCoords.some(
          (c) =>
            combinedModText.includes(c.skinId) ||
            combinedModText.includes(c.charId),
        );

        if (isRelatedSkin) {
          matchType = "skin";
          reason =
            "FIX pak is active but the required BASE pak for this skin is not active — this is the most likely crash cause";
        }
      }
    }

    // -----------------------------------------------------------------------
    // Priority 3: Skin-level match via PAK file name (no asset DB)
    // Look at the pak filename itself for the skin ID
    // -----------------------------------------------------------------------
    if (!matchType && skinCoords.length > 0) {
      for (const pakPath of mod.active_paks || []) {
        const pakName = pakPath.replace(/\\/g, "/").split("/").pop()?.toLowerCase() ?? "";
        for (const coord of skinCoords) {
          if (pakName.includes(coord.skinId) || pakName.includes(coord.charId)) {
            matchType = "skin";
            reason = `PAK file name references the same skin (${coord.charId}/${coord.skinId})`;
            break;
          }
        }
        if (matchType) break;
      }
    }

    // -----------------------------------------------------------------------
    // Priority 4: Fallback heuristic (PAK stem in crash text)
    // -----------------------------------------------------------------------
    if (!matchType && isModRelatedCrash) {
      for (const pak of mod.active_paks || []) {
        const pakStem = pak
          .replace(/\\/g, "/")
          .split("/")
          .pop()
          ?.replace(/\.pak$/i, "")
          .toLowerCase();
        if (pakStem && combinedText.includes(pakStem)) {
          matchType = "fallback";
          reason = `PAK name "${pakStem}" found in crash report`;
          break;
        }
      }

      // Also check mod name
      const modNameLower = (mod.name || mod.mod_name || "").toLowerCase();
      if (!matchType && modNameLower && combinedText.includes(modNameLower)) {
        matchType = "fallback";
        reason = `Mod name found in crash report`;
      }
    }

    if (matchType) {
      suspicious.push({ mod, matchType, reason });
    } else {
      rest.push(mod);
    }
  }

  // Sort: exact → skin → character → fallback
  const order: Record<SuspiciousMod["matchType"], number> = {
    exact: 0,
    skin: 1,
    character: 2,
    fallback: 3,
  };
  suspicious.sort((a, b) => order[a.matchType] - order[b.matchType]);

  return { suspicious, rest };
}

/**
 * Format a crash timestamp for display.
 */
export function formatCrashTime(timeOfCrash: string | null): string {
  if (!timeOfCrash) return "Unknown time";
  try {
    const date = new Date(timeOfCrash);
    if (isNaN(date.getTime())) return timeOfCrash;
    return date.toLocaleString();
  } catch {
    return timeOfCrash;
  }
}
