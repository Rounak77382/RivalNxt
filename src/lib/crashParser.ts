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
  /** Extracted asset paths that might be causing the crash (e.g. from ObjectSerializationError) */
  faultyAssetPaths: string[];
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

  // Extract faulty asset paths from error message or raw xml
  // e.g. ObjectSerializationError: /Game/Marvel/Characters/1020/1020100/1020100_ChildBP
  const assetRegex = /\/Game\/([a-zA-Z0-9_/-]+)/gi;
  const rawXmlMatches = Array.from(xml.matchAll(assetRegex)).map((m) => m[1]);

  // If a crash mentions a specific skin (e.g. 1020100), another mod for the same base character (1020)
  // could be the culprit (e.g. skeletal mismatches or shared blueprints).
  // We extract the base character path to flag ANY mod touching that character.
  const broadenedPaths: string[] = [];
  for (const match of rawXmlMatches) {
    // Look for Marvel/Characters/XXXX/
    const charMatch = match.match(/Marvel\/Characters\/([0-9]{4})/i);
    if (charMatch) {
      broadenedPaths.push(`Marvel/Characters/${charMatch[1]}/`);
    }
  }

  const faultyAssetPaths = Array.from(new Set([...rawXmlMatches, ...broadenedPaths]));

  return {
    errorMessage,
    timeOfCrash,
    crashGuid,
    engineVersion,
    callStack,
    rawXml: xml,
    faultyAssetPaths,
  };
}

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
];

export interface SuspiciousMod {
  mod: ApiDownload;
  matchType: "exact" | "character" | "fallback";
}

/**
 * Correlate a CrashInfo with a list of downloads that have active PAK files.
 *
 * Returns: { suspicious: SuspiciousMod[], rest: ApiDownload[] }
 *   suspicious = mods whose PAK name appears in the call stack OR that contain the faulty asset, sorted first
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

  // Normalize faulty asset paths for searching
  const exactFaultyLower = crashInfo.faultyAssetPaths
    .filter((p) => !p.endsWith("/"))
    .map((p) => p.toLowerCase());
  const charFaultyLower = crashInfo.faultyAssetPaths
    .filter((p) => p.endsWith("/"))
    .map((p) => p.toLowerCase());

  const suspicious: SuspiciousMod[] = [];
  const rest: ApiDownload[] = [];

  for (const mod of activeMods) {
    let matchType: "exact" | "character" | "fallback" | null = null;

    // 1. Check if the mod contains any of the faulty assets
    if (pakAssets && crashInfo.faultyAssetPaths.length > 0) {
      // Find all assets for this mod's paks
      for (const pakPath of mod.active_paks || []) {
        const pakName = pakPath.split("/").pop()?.split("\\").pop();
        const pakAssetData = pakAssets.find((p) => p.pak_name === pakName);
        if (pakAssetData && pakAssetData.assets) {
          // Check exact matches first
          for (const assetPath of pakAssetData.assets) {
            const assetPathLower = assetPath.toLowerCase();
            for (const faulty of exactFaultyLower) {
              if (assetPathLower.includes(faulty)) {
                matchType = "exact";
                break;
              }
            }
            if (matchType) break;
          }
          if (matchType) break;

          // Then check character-level matches
          for (const assetPath of pakAssetData.assets) {
            const assetPathLower = assetPath.toLowerCase();
            for (const faulty of charFaultyLower) {
              if (assetPathLower.includes(faulty)) {
                matchType = "character";
                break;
              }
            }
            if (matchType) break;
          }
        }
        if (matchType) break;
      }
    }

    // 2. Fallback to heuristic checks (Callstack / error text matching PAK name)
    if (!matchType && isModRelatedCrash) {
      // Check if any of this mod's PAK stems appear in the crash text
      for (const pak of mod.active_paks || []) {
        const pakStem = pak
          .replace(/\\/g, "/")
          .split("/")
          .pop()
          ?.replace(/\.pak$/i, "")
          .toLowerCase();
        if (pakStem && combinedText.includes(pakStem)) {
          matchType = "fallback";
          break;
        }
      }

      // Also check mod name
      const modNameLower = (mod.name || mod.mod_name || "").toLowerCase();
      if (!matchType && modNameLower && combinedText.includes(modNameLower)) {
        matchType = "fallback";
      }
    }

    if (matchType) {
      suspicious.push({ mod, matchType });
    } else {
      rest.push(mod);
    }
  }

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
