import type { SettingsTask } from "./api";

export type StepStatus = "pending" | "active" | "done";

export interface ParsedStep {
  id: string;
  label: string;
  status: StepStatus;
  current?: number;
  total?: number;
  detail?: string;
}

export interface ParsedSummary {
  supported: boolean;
  steps: ParsedStep[];
}

interface ConflictCounts {
  total?: number;
  active?: number;
}

export function summarizeTaskOutput(
  task: SettingsTask | undefined,
  raw: string
): ParsedSummary {
  const trimmed = raw.trim();
  if (!trimmed) {
    return { supported: false, steps: [] };
  }

  if (!task || task === "bootstrap_rebuild") {
    return summarizeBootstrap(trimmed);
  }

  if (task === "ingest_download_assets") {
    return summarizeIngest(trimmed);
  }

  if (task === "scan_active_mods") {
    return summarizeActiveScan(trimmed);
  }

  if (task === "rebuild_conflicts") {
    return summarizeConflicts(trimmed);
  }

  if (task === "rebuild_tags") {
    return summarizeTags(trimmed);
  }

  if (task === "sync_nexus") {
    return summarizeSync(trimmed);
  }

  if (task === "delete_outdated_versions") {
    return summarizeDeleteOutdated(trimmed);
  }

  if (task === "rebuild_character_data") {
    return summarizeCharacterData(trimmed);
  }

  return { supported: false, steps: [] };
}

function summarizeBootstrap(raw: string): ParsedSummary {
  const lines = splitLines(raw);

  let databaseStatus: StepStatus = "pending";
  let downloadsStatus: StepStatus = "pending";
  let syncStatus: StepStatus = "pending";
  let extractionStatus: StepStatus = "pending";
  let tagsStatus: StepStatus = "pending";
  let conflictsStatus: StepStatus = "pending";
  let conflictCounts: ConflictCounts | null = null;

  let ueExtractionStatus: StepStatus = "pending";
  let ueExtractionDetail: string | undefined;

  let downloadsTotal: number | null = null;
  let syncTotal: number | null = null;
  let extractionTotal: number | null = null;

  let syncCurrent = 0;
  let extractionCurrent = 0;
  const seenExtractionPaths = new Set<string>();

  const seenSyncMods = new Set<string>();
  const seenExtractionTargets = new Set<string>();

  lines.forEach((line) => {
    if (!line) return;

    if (matches(line, /database (location|file)/i)) {
      databaseStatus = "done";
      return;
    }

    const foundDownloads = matchNumber(
      line,
      /(found|scanned)\s+(\d+)\s+(?:local\s+)?download row\(s\)/i
    );
    if (foundDownloads !== null) {
      downloadsTotal = foundDownloads;
      downloadsStatus = "done";
      if (extractionTotal === null) {
        extractionTotal = foundDownloads;
      }
      if (syncTotal === null) {
        syncTotal = foundDownloads;
      }
      return;
    }

    const foundMods = matchNumber(line, /found\s+(\d+)\s+mod\(s\)/i);
    if (foundMods !== null && downloadsTotal === null) {
      downloadsTotal = foundMods;
      downloadsStatus = "done";
      if (extractionTotal === null) {
        extractionTotal = foundMods;
      }
      if (syncTotal === null) {
        syncTotal = foundMods;
      }
      return;
    }

    const syncedModId = matchNumber(line, /synced mod\s+([0-9]+)/i);
    if (syncedModId !== null) {
      const key = `${syncedModId}`;
      if (!seenSyncMods.has(key)) {
        seenSyncMods.add(key);
        syncCurrent = seenSyncMods.size;
      }
      syncStatus = "active";
      return;
    }

    const syncedSummary = matchNumber(line, /synced\s+(\d+)\s+mod\(s\)/i);
    if (syncedSummary !== null) {
      syncTotal = syncedSummary;
      syncCurrent = Math.max(syncCurrent, syncTotal);
      syncStatus = "done";
      return;
    }

    // Match the bracketed name that appears immediately before the extract
    // keywords. Logs include a logger tag earlier (e.g. [ingest_download_assets])
    // so prefer the last bracketed token closest to the keyword.
    if (
      matches(
        line,
        /.*\[(.*?)\]\s+(extracting archive|processing folder|processing download)/i
      )
    ) {
      const name = extractBracketName(line);
      if (name && !seenExtractionTargets.has(name)) {
        seenExtractionTargets.add(name);
        extractionCurrent = seenExtractionTargets.size;
      }
      extractionStatus = "active";
      return;
    }

    // More robust extraction detection: also accept lines that include a bracketed
    // extract event with a path ("[Name] Extracting archive -> C:\path\...")
    // or plain "Processing folder (already extracted) -> C:\path\..." lines.
    // Ensure we capture the bracketed archive name that appears closest to
    // the "Extracting archive" token by allowing a greedy prefix up to the
    // last '[' before the keyword.
    const bracketExtract = line.match(
      /.*\[(.*?)\]\s+Extracting archive\s*->\s*(.+)/i
    );
    if (bracketExtract) {
      const name = bracketExtract[1]?.trim();
      const path = bracketExtract[2]?.trim();
      if (name && !seenExtractionTargets.has(name)) {
        seenExtractionTargets.add(name);
        extractionCurrent = seenExtractionTargets.size;
      } else if (path) {
        const key = path.toLowerCase();
        if (!seenExtractionPaths.has(key)) {
          seenExtractionPaths.add(key);
          extractionCurrent = Math.max(
            extractionCurrent,
            seenExtractionTargets.size + seenExtractionPaths.size
          );
        }
      }
      extractionStatus = "active";
      return;
    }

    const folderExtract = line.match(
      /Processing folder \(already extracted\)\s*->\s*(.+)/i
    );
    if (folderExtract) {
      const path = folderExtract[1]?.trim();
      if (path) {
        const key = path.toLowerCase();
        if (!seenExtractionPaths.has(key)) {
          seenExtractionPaths.add(key);
          extractionCurrent = Math.max(
            extractionCurrent,
            seenExtractionTargets.size + seenExtractionPaths.size
          );
        }
      }
      extractionStatus = "active";
      return;
    }

    const extractionSummary = matchNumber(
      line,
      /processed\s+(\d+)\s+archive\(s\)/i
    );
    if (extractionSummary !== null) {
      extractionTotal = extractionSummary;
      extractionStatus = "done";
      return;
    }

    if (
      matches(
        line,
        /rebuilding (asset_tags|pak_tags)|tagged \d+|tag rebuild complete|tag artifacts rebuilt|upserted tags for \d+/i
      )
    ) {
      tagsStatus =
        /tag rebuild complete|tag artifacts rebuilt|upserted tags for \d+/i.test(line)
          ? "done"
          : tagsStatus === "pending"
          ? "active"
          : tagsStatus;
      return;
    }

    // Catch-all: if the task log itself says it finished with exit code 0, mark everything in-progress as done
    if (matches(line, /task '.+' finished with exit code 0/i)) {
      if (tagsStatus === "active") tagsStatus = "done";
      if (conflictsStatus === "active") conflictsStatus = "done";
      if (syncStatus === "active") syncStatus = "done";
      if (extractionStatus === "active") extractionStatus = "done";
      if (ueExtractionStatus === "active") ueExtractionStatus = "done";
      return;
    }

    if (
      matches(
        line,
        /conflict tables rebuilt|Active scan: discovered|Scanning installed mods/i
      )
    ) {
      conflictsStatus = /conflict tables rebuilt/i.test(line)
        ? "done"
        : conflictsStatus === "pending"
        ? "active"
        : conflictsStatus;
      conflictCounts = mergeConflictCounts(
        conflictCounts,
        extractConflictCounts(line)
      );
      return;
    }

    // Marvel Rivals Extraction
    if (matches(line, /\[1\/4\]|Extracting character names/i)) {
      ueExtractionStatus = "active";
      ueExtractionDetail = "Step 1/4: Extracting characters...";
      return;
    }
    if (matches(line, /\[2\/4\]|Extracting skin ids/i)) {
      ueExtractionStatus = "active";
      ueExtractionDetail = "Step 2/4: Scanning skin variants...";
      return;
    }
    if (matches(line, /\[3\/4\]|Extracting skin names/i)) {
      ueExtractionStatus = "active";
      ueExtractionDetail = "Step 3/4: Reading localization...";
      return;
    }
    if (matches(line, /\[4\/4\]|Building final database/i)) {
      ueExtractionStatus = "active";
      ueExtractionDetail = "Step 4/4: Finalizing database...";
      return;
    }
    if (
      matches(line, /EXTRACTION AND INGESTION COMPLETE!|Total characters:/i)
    ) {
      ueExtractionStatus = "done";
      ueExtractionDetail = "Extraction complete";
      return;
    }
  });

  if (syncStatus === "pending" && syncCurrent > 0) {
    syncStatus = "active";
  }
  if (
    syncStatus === "active" &&
    syncTotal !== null &&
    syncCurrent >= syncTotal
  ) {
    syncStatus = "done";
  }

  if (extractionStatus === "pending" && extractionCurrent > 0) {
    extractionStatus = "active";
  }
  const extractionTargetTotal =
    extractionTotal !== null
      ? extractionTotal
      : downloadsTotal !== null
      ? downloadsTotal
      : seenExtractionTargets.size > 0
      ? seenExtractionTargets.size
      : null;
  if (
    extractionStatus === "active" &&
    extractionTargetTotal !== null &&
    extractionCurrent >= extractionTargetTotal
  ) {
    extractionStatus = "done";
  }

  const steps: ParsedStep[] = [];

  if (databaseStatus !== "pending") {
    steps.push({
      id: "database",
      label: "Database location found",
      status: databaseStatus,
    });
  }

  if (ueExtractionStatus !== "pending") {
    steps.push({
      id: "ue_extraction",
      label: "Game Data Extraction",
      status: ueExtractionStatus,
      detail: ueExtractionDetail,
    });
  }

  if (downloadsStatus !== "pending" && downloadsTotal !== null) {
    steps.push({
      id: "downloads",
      label: `Found ${downloadsTotal} mods`,
      status: downloadsStatus,
    });
  }

  if (syncStatus !== "pending") {
    const total = syncTotal ?? downloadsTotal ?? null;
    steps.push({
      id: "sync",
      label: syncStatus === "done" ? "Synced mods" : "Syncing mods",
      status: syncStatus,
      current:
        syncCurrent || (syncStatus === "done" && total ? total : undefined),
      total: total ?? undefined,
    });
  }

  if (extractionStatus !== "pending") {
    steps.push({
      id: "extract",
      label: extractionStatus === "done" ? "Extracted mods" : "Extracting mods",
      status: extractionStatus,
      current:
        extractionCurrent ||
        (extractionStatus === "done" && extractionTargetTotal
          ? extractionTargetTotal
          : undefined),
      total: extractionTargetTotal ?? undefined,
    });
  }

  if (tagsStatus !== "pending") {
    steps.push({
      id: "tags",
      label: "Building tags",
      status: tagsStatus,
    });
  }

  if (conflictsStatus !== "pending") {
    steps.push({
      id: "conflicts",
      label: "Examining conflicts",
      status: conflictsStatus,
      detail: formatConflictDetail(conflictCounts),
    });
  }

  return {
    supported: steps.length > 0,
    steps,
  };
}

function summarizeIngest(raw: string): ParsedSummary {
  const lines = splitLines(raw);
  let total: number | null = null;
  let processed = 0;
  let sawCompletionLine = false;
  const seen = new Set<string>();
  const seenArchivePaths = new Set<string>();

  lines.forEach((line) => {
    const found = matchNumber(line, /found\s+(\d+)\s+download row\(s\)/i);
    if (found !== null) {
      total = found;
      return;
    }

    // If the script prints a processed summary line, prefer it as final value
    if (matches(line, /processed\s+\d+\s+archive/i)) {
      const summary = matchNumber(line, /processed\s+(\d+)\s+archive/i);
      if (summary !== null) {
        processed = summary;
      }
      sawCompletionLine = true;
      return;
    }

    // Look for explicit extraction/processing lines. Prefer a bracketed name when
    // available ("[Name] Extracting archive -> path"); otherwise extract the path
    // and use the basename as a stable key to avoid double-counting.
    // For ingest logs also capture the bracketed name closest to the keyword
    const bracketExtract = line.match(
      /.*\[(.*?)\]\s+Extracting archive\s*->\s*(.+)/i
    );
    if (bracketExtract) {
      const name = bracketExtract[1]?.trim();
      const path = bracketExtract[2]?.trim();
      if (name && !seen.has(name)) {
        seen.add(name);
        processed = seen.size;
        return;
      }
      if (path) {
        const key = path.toLowerCase();
        if (!seenArchivePaths.has(key)) {
          seenArchivePaths.add(key);
          processed = Math.max(processed, seen.size + seenArchivePaths.size);
        }
        return;
      }
    }

    // Fallback: processing folder lines without bracketed name
    const folderExtract = line.match(
      /Processing folder \(already extracted\)\s*->\s*(.+)/i
    );
    if (folderExtract) {
      const path = folderExtract[1]?.trim();
      if (path) {
        const key = path.toLowerCase();
        if (!seenArchivePaths.has(key)) {
          seenArchivePaths.add(key);
          processed = Math.max(processed, seen.size + seenArchivePaths.size);
        }
      }
      return;
    }

    // If the script logs "Found X pak(s) in archive" the log is usually prefixed
    // by the bracketed name as well; try to use that to increment progress.
    if (matches(line, /Found\s+\d+\s+pak\(s\) in archive/i)) {
      const name = extractBracketName(line);
      if (name && !seen.has(name)) {
        seen.add(name);
        processed = seen.size;
      } else {
        // No bracket name available; bump using archive path set size
        processed = Math.max(processed, seen.size + seenArchivePaths.size);
      }
      return;
    }
  });

  if (processed === 0 && total === null) {
    return { supported: false, steps: [] };
  }

  const completed =
    total !== null ? processed >= total && total >= 0 : sawCompletionLine;
  const status: StepStatus = completed ? "done" : "active";
  const label = status === "done" ? "Extracted mods" : "Extracting mods";

  const boundedProcessed =
    total !== null ? Math.min(processed, total) : processed;

  const detail = (() => {
    if (total !== null) {
      return `Processed ${boundedProcessed} of ${total} archive(s)`;
    }
    if (sawCompletionLine) {
      return boundedProcessed > 0
        ? `Processed ${boundedProcessed} archive(s)`
        : "No archives required processing";
    }
    return boundedProcessed > 0
      ? `Processed ${boundedProcessed} archive(s)`
      : undefined;
  })();

  return {
    supported: true,
    steps: [
      {
        id: "ingest",
        label,
        status,
        current: boundedProcessed || undefined,
        total: total ?? undefined,
        detail,
      },
    ],
  };
}

function summarizeActiveScan(raw: string): ParsedSummary {
  const lines = splitLines(raw);
  const scanLine = lines.find((line) =>
    matches(line, /active scan: discovered/i)
  );
  if (!scanLine) {
    return { supported: false, steps: [] };
  }

  const count = matchNumber(scanLine, /discovered\s+(\d+)/i);
  return {
    supported: true,
    steps: [
      {
        id: "scan",
        label:
          count !== null
            ? `Active scan found ${count} pak(s)`
            : "Scanning active mods",
        status: "done",
      },
    ],
  };
}

function summarizeConflicts(raw: string): ParsedSummary {
  const lines = splitLines(raw);
  if (lines.length === 0) {
    return { supported: false, steps: [] };
  }

  let status: StepStatus = "pending";
  let counts: ConflictCounts | null = null;

  lines.forEach((line) => {
    if (!line) return;
    if (matches(line, /rebuild results/i)) {
      status = "done";
      counts = mergeConflictCounts(counts, extractConflictCounts(line));
      return;
    }
    if (matches(line, /conflict tables rebuilt/i)) {
      status = "done";
      counts = mergeConflictCounts(counts, extractConflictCounts(line));
      return;
    }
    // Standalone _task_rebuild_conflicts prints e.g. "asset_conflicts: 42"
    // After collecting those counts, the next print is the completion line.
    if (
      matches(line, /asset_conflicts\s*:\s*\d+/i) ||
      matches(line, /asset_conflicts_active\s*:\s*\d+/i)
    ) {
      counts = mergeConflictCounts(counts, extractConflictCounts(line));
      // The presence of these count lines means the rebuild ran and completed
      status = "done";
      return;
    }
    // Standalone task: "Rebuild conflicts completed with no reported changes."
    if (matches(line, /rebuild conflicts completed/i)) {
      status = "done";
      return;
    }
    // Mark done when the task prints a finished line with any exit code
    const finishedMatch = line.match(/finished with exit code\s*(\d+)/i);
    if (finishedMatch) {
      status = "done";
      counts = mergeConflictCounts(counts, extractConflictCounts(line));
      return;
    }
    if (status !== "done") {
      if (
        matches(line, /rebuild conflicts/i) ||
        matches(line, /sample asset_conflicts/i) ||
        matches(line, /active conflicts/i) ||
        matches(line, /examining conflicts/i)
      ) {
        status = "active";
      }
    }
  });

  if (status === "pending" && lines.length > 0) {
    status = "active";
  }

  if (status === "pending") {
    return { supported: false, steps: [] };
  }

  return {
    supported: true,
    steps: [
      {
        id: "conflicts",
        label: "Examining conflicts",
        status,
        detail: formatConflictDetail(counts),
      },
    ],
  };
}

function summarizeTags(raw: string): ParsedSummary {
  const lines = splitLines(raw);

  // Walk all lines to derive the most specific label and status
  let assetCount: number | null = null;
  let pakCount: number | null = null;
  let isDone = false;
  let hasStarted = false;

  for (const line of lines) {
    // Asset tag count: "Tagged N asset path(s)."
    const assetMatch = matchNumber(line, /tagged\s+(\d+)\s+asset/i);
    if (assetMatch !== null) {
      assetCount = assetMatch;
      hasStarted = true;
      continue;
    }
    // Pak tag count: "Upserted tags for N paks."
    const pakMatch = matchNumber(line, /upserted tags for\s+(\d+)/i);
    if (pakMatch !== null) {
      pakCount = pakMatch;
      hasStarted = true;
      continue;
    }
    if (matches(line, /rebuilding (asset_tags|pak_tags)|rebuilding pak_tags_json/i)) {
      hasStarted = true;
      continue;
    }
    if (
      matches(line, /tag rebuild complete|tag artifacts rebuilt/i) ||
      matches(line, /task '.+' finished with exit code 0/i)
    ) {
      isDone = true;
      hasStarted = true;
      continue;
    }
  }

  // Only treat as "done" when we saw either the explicit completion line OR the asset+pak counts
  if (assetCount !== null || pakCount !== null) {
    isDone = true;
  }

  if (!hasStarted) {
    return { supported: false, steps: [] };
  }

  const status: StepStatus = isDone ? "done" : "active";
  const label = (() => {
    if (assetCount !== null && pakCount !== null) {
      return `Tagged ${assetCount} asset path(s), ${pakCount} pak(s)`;
    }
    if (assetCount !== null) return `Tagged ${assetCount} asset path(s)`;
    if (pakCount !== null) return `Upserted tags for ${pakCount} pak(s)`;
    return isDone ? "Tags rebuilt" : "Building tags";
  })();

  return {
    supported: true,
    steps: [{ id: "tags", label, status }],
  };
}

function summarizeDeleteOutdated(raw: string): ParsedSummary {
  const lines = splitLines(raw);
  if (lines.length === 0) return { supported: false, steps: [] };

  let outdatedStatus: StepStatus = "pending";
  let outdatedCount: number | null = null;
  let filesCount: number | null = null;

  let orphanStatus: StepStatus = "pending";
  let orphanCount: number | null = null;

  let tagsStatus: StepStatus = "pending";
  let conflictsStatus: StepStatus = "pending";

  for (const line of lines) {
    if (!line) continue;

    // Detecting outdated variants
    if (matches(line, /queueing outdated version for deletion/i)) {
      outdatedStatus = "active";
      const n = matchNumber(line, /id:\s*(\d+)/i);
      if (n !== null) outdatedCount = (outdatedCount ?? 0) + 1;
      continue;
    }
    if (matches(line, /no outdated tracked versions found/i)) {
      outdatedStatus = "done";
      outdatedCount = 0;
      continue;
    }
    const removedMatch = matchNumber(line, /successfully removed\s+(\d+)\s+outdated/i);
    if (removedMatch !== null) {
      outdatedStatus = "done";
      outdatedCount = removedMatch;
      const f = matchNumber(line, /(\d+)\s+files? deleted/i);
      if (f !== null) filesCount = f;
      continue;
    }

    // Orphan scanning
    if (matches(line, /scanning for untracked\/orphaned/i)) {
      orphanStatus = "active";
      continue;
    }
    if (matches(line, /deleted orphaned file/i)) {
      orphanStatus = "active";
      continue;
    }
    if (matches(line, /successfully deleted\s+\d+\s+untracked/i)) {
      orphanStatus = "done";
      orphanCount = matchNumber(line, /deleted\s+(\d+)\s+untracked/i);
      continue;
    }
    if (matches(line, /no untracked mod files found/i)) {
      orphanStatus = "done";
      orphanCount = 0;
      continue;
    }

    // Tags rebuilt after deletion
    if (matches(line, /rebuilding (asset_tags|pak_tags)|rebuild.*tags/i)) {
      tagsStatus = "active";
      continue;
    }
    if (
      matches(line, /upserted tags for \d+|tag rebuild complete|tag artifacts rebuilt/i) ||
      matches(line, /tagged \d+ asset/i)
    ) {
      tagsStatus = "done";
      continue;
    }

    // Conflicts
    if (matches(line, /rebuild.*conflicts|examining conflicts/i)) {
      conflictsStatus = "active";
      continue;
    }
    if (matches(line, /conflict tables rebuilt|rebuild results/i)) {
      conflictsStatus = "done";
      continue;
    }

    // Catch-all: task finished with exit code 0
    if (matches(line, /task '.+' finished with exit code 0/i)) {
      if (outdatedStatus === "active") outdatedStatus = "done";
      if (orphanStatus === "active") orphanStatus = "done";
      if (tagsStatus === "active") tagsStatus = "done";
      if (conflictsStatus === "active") conflictsStatus = "done";
      continue;
    }
  }

  const steps: ParsedStep[] = [];

  if (outdatedStatus !== "pending") {
    const detail = (() => {
      if (outdatedCount === 0) return "No outdated versions found";
      if (outdatedCount !== null && filesCount !== null)
        return `Removed ${outdatedCount} variant(s), ${filesCount} file(s) deleted`;
      if (outdatedCount !== null) return `Removed ${outdatedCount} outdated variant(s)`;
      return undefined;
    })();
    steps.push({
      id: "outdated",
      label: outdatedStatus === "done" ? "Outdated versions cleaned" : "Scanning for outdated versions",
      status: outdatedStatus,
      detail,
    });
  }

  if (orphanStatus !== "pending") {
    const detail = (() => {
      if (orphanCount === 0) return "No orphaned files found";
      if (orphanCount !== null) return `Deleted ${orphanCount} orphaned file(s)`;
      return undefined;
    })();
    steps.push({
      id: "orphans",
      label: orphanStatus === "done" ? "Orphaned files cleaned" : "Scanning for orphaned files",
      status: orphanStatus,
      detail,
    });
  }

  if (tagsStatus !== "pending") {
    steps.push({
      id: "tags",
      label: tagsStatus === "done" ? "Tags rebuilt" : "Rebuilding tags",
      status: tagsStatus,
    });
  }

  if (conflictsStatus !== "pending") {
    steps.push({
      id: "conflicts",
      label: conflictsStatus === "done" ? "Conflicts rebuilt" : "Rebuilding conflicts",
      status: conflictsStatus,
    });
  }

  // If no specific steps parsed yet, but we have raw output → show as active/done
  if (steps.length === 0 && lines.length > 0) {
    const finished = lines.some((l) =>
      matches(l, /task '.+' finished with exit code 0|no outdated tracked versions found/i)
    );
    steps.push({
      id: "scan",
      label: finished ? "Cleanup complete" : "Running cleanup",
      status: finished ? "done" : "active",
    });
  }

  return { supported: steps.length > 0, steps };
}

function summarizeCharacterData(raw: string): ParsedSummary {
  const lines = splitLines(raw);
  if (lines.length === 0) return { supported: false, steps: [] };

  let step1: StepStatus = "pending";
  let step2: StepStatus = "pending";
  let step3: StepStatus = "pending";
  let step4: StepStatus = "pending";
  let isDone = false;
  let hasStarted = false;

  for (const line of lines) {
    if (!line) continue;
    // Top-level wrapper message from _task_rebuild_character_data()
    if (matches(line, /extracting character and skin data from pak files/i)) {
      hasStarted = true;
      step1 = "active";
      continue;
    }
    // Step markers from core/extraction/service.py extract_character_and_skin_data()
    if (matches(line, /\[1\/4\]|extracting character names/i)) {
      step1 = "active"; hasStarted = true; continue;
    }
    if (matches(line, /\[2\/4\]|extracting skin ids/i)) {
      step1 = "done"; step2 = "active"; hasStarted = true; continue;
    }
    if (matches(line, /\[3\/4\]|extracting skin names/i)) {
      step2 = "done"; step3 = "active"; hasStarted = true; continue;
    }
    if (matches(line, /\[4\/4\]|building final database/i)) {
      step3 = "done"; step4 = "active"; hasStarted = true; continue;
    }
    // Completion markers
    if (matches(line, /extraction and ingestion complete!|total characters:|success! extracted/i)) {
      if (step1 !== "pending") step1 = "done";
      if (step2 !== "pending") step2 = "done";
      if (step3 !== "pending") step3 = "done";
      step4 = "done"; isDone = true; hasStarted = true; continue;
    }
    // Wrapper-level completion: "Character data rebuild complete!"
    if (matches(line, /character data rebuild complete/i)) {
      if (step1 === "active") step1 = "done";
      if (step2 === "active") step2 = "done";
      if (step3 === "active") step3 = "done";
      if (step4 === "active") step4 = "done";
      isDone = true; hasStarted = true; continue;
    }
    // Catch-all
    if (matches(line, /task '.+' finished with exit code 0/i)) {
      if (step1 === "active") step1 = "done";
      if (step2 === "active") step2 = "done";
      if (step3 === "active") step3 = "done";
      if (step4 === "active") step4 = "done";
      isDone = true;
      continue;
    }
  }

  if (!hasStarted) {
    return { supported: false, steps: [] };
  }

  const steps: ParsedStep[] = [];

  // If we only saw top-level wrapper messages (no [1/4] steps), show a single step
  if (step1 === "pending" && step2 === "pending" && step3 === "pending" && step4 === "pending") {
    steps.push({
      id: "extract",
      label: isDone ? "Character data rebuilt" : "Extracting character data",
      status: isDone ? "done" : "active",
    });
  } else {
    if (step1 !== "pending") steps.push({ id: "step1", label: "Extracting character names", status: step1 });
    if (step2 !== "pending") steps.push({ id: "step2", label: "Scanning skin variants", status: step2 });
    if (step3 !== "pending") steps.push({ id: "step3", label: "Reading localization", status: step3 });
    if (step4 !== "pending") steps.push({ id: "step4", label: "Finalizing database", status: step4 });
  }

  if (isDone && steps.length === 0) {
    steps.push({ id: "done", label: "Character data rebuilt", status: "done" });
  }

  return { supported: steps.length > 0, steps };
}

function summarizeSync(raw: string): ParsedSummary {
  const lines = splitLines(raw);
  let total: number | null = null;
  let current = 0;
  const seen = new Set<string>();
  let nothingToSync = false;

  lines.forEach((line) => {
    // Server-level summary: "Synced N mod(s) from Nexus API."
    const serverSummary = matchNumber(line, /synced\s+(\d+)\s+mod\(s\)/i);
    if (serverSummary !== null) {
      total = serverSummary;
      current = Math.max(current, total);
      return;
    }
    // Per-mod line: "Synced mod 12345: info=200 ..."
    if (matches(line, /synced mod\s+[0-9]+/i)) {
      const id = extractMatch(line, /synced mod\s+([0-9]+)/i);
      if (id && !seen.has(id)) {
        seen.add(id);
        current = seen.size;
      }
      return;
    }
    // 0-mods case: "No Nexus-linked mods found; nothing to sync."
    if (matches(line, /no nexus-linked mods found|nothing to sync/i)) {
      nothingToSync = true;
      return;
    }
    // Nexus API key not configured
    if (matches(line, /nexus api key not configured/i)) {
      nothingToSync = true;
      return;
    }
  });

  if (nothingToSync) {
    return {
      supported: true,
      steps: [
        {
          id: "sync",
          label: "No mods to sync",
          status: "done",
          detail: "No Nexus-linked mods found in database",
        },
      ],
    };
  }

  if (current === 0 && total === null) {
    return { supported: false, steps: [] };
  }

  const status: StepStatus =
    total !== null && current >= total ? "done" : "active";
  const label = status === "done" ? "Synced mods" : "Syncing mods";

  return {
    supported: true,
    steps: [
      {
        id: "sync",
        label,
        status,
        current: current || undefined,
        total: total ?? undefined,
      },
    ],
  };
}

function mergeConflictCounts(
  existing: ConflictCounts | null,
  incoming: ConflictCounts | null
): ConflictCounts | null {
  if (!incoming) return existing;
  if (!existing) return { ...incoming };
  const merged: ConflictCounts = { ...existing };
  if (typeof incoming.total === "number") {
    merged.total = incoming.total;
  }
  if (typeof incoming.active === "number") {
    merged.active = incoming.active;
  }
  return merged;
}

function formatConflictDetail(
  counts: ConflictCounts | null
): string | undefined {
  if (!counts) return undefined;
  const parts: string[] = [];
  if (typeof counts.total === "number") {
    parts.push(`${counts.total} total`);
  }
  if (typeof counts.active === "number") {
    parts.push(`${counts.active} active`);
  }
  if (parts.length === 0) return undefined;
  return `Conflicts: ${parts.join(" · ")}`;
}

function extractConflictCounts(line: string): ConflictCounts | null {
  const totalMatch = line.match(/asset_conflicts["']?\s*:\s*(\d+)/i);
  const activeMatch = line.match(/asset_conflicts_active["']?\s*:\s*(\d+)/i);
  if (!totalMatch && !activeMatch) {
    return null;
  }
  const counts: ConflictCounts = {};
  if (totalMatch) {
    const parsed = parseInt(totalMatch[1] ?? "", 10);
    if (Number.isFinite(parsed)) {
      counts.total = parsed;
    }
  }
  if (activeMatch) {
    const parsed = parseInt(activeMatch[1] ?? "", 10);
    if (Number.isFinite(parsed)) {
      counts.active = parsed;
    }
  }
  return counts;
}

function splitLines(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function matches(line: string, pattern: RegExp): boolean {
  return pattern.test(line);
}

function matchNumber(line: string, pattern: RegExp): number | null {
  const match = line.match(pattern);
  if (!match) return null;
  const value = parseInt(match[match.length - 1] ?? "", 10);
  return Number.isFinite(value) ? value : null;
}

function extractBracketName(line: string): string | null {
  // Return the last bracketed token on the line. Many log lines include an
  // earlier logger tag like `[ingest_download_assets]` followed by the
  // archive name in a second bracket; prefer the archive name.
  const matches = Array.from(line.matchAll(/\[(.*?)\]/g));
  if (!matches || matches.length === 0) return null;
  const last = matches[matches.length - 1];
  return last && last[1] ? last[1].trim() : null;
}

function extractMatch(line: string, pattern: RegExp): string | null {
  const match = line.match(pattern);
  if (!match) return null;
  return match[1]?.trim() ?? null;
}
