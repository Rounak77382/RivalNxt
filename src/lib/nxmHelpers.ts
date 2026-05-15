import { toast } from "sonner";

import {
  listNxmHandoffs,
  getNxmHandoff,
  getModDetails,
  type ApiNxmHandoffSummary,
  type ApiNxmDownloadProgress,
  ApiError,
} from "./api";

/**
 * Extract a human-readable mod label from a handoff object.
 *
 * Resolution order:
 * 1. `metadata.mod_info.name` (set by backend at registration or after ingest)
 * 2. `getModDetails(mod_id)` API call (fetches from local DB / Nexus)
 * 3. Fallback to `Mod #<id>` / "Nexus download"
 */
export async function getModLabel(
  handoff: ApiNxmHandoffSummary,
): Promise<string> {
  // 1) Try metadata already attached to the handoff
  const modInfo = handoff.metadata?.mod_info;
  if (modInfo && typeof modInfo === "object") {
    const name = (modInfo as Record<string, unknown>)["name"];
    if (typeof name === "string" && name.trim()) {
      return name.trim();
    }
  }

  // 2) Try fetching from the backend mod details API
  const modId = handoff.request?.mod_id;
  if (modId != null) {
    try {
      const details = await getModDetails(modId);
      const name = details?.mod?.name;
      if (typeof name === "string" && name.trim()) {
        return name.trim();
      }
    } catch {
      // ignore – fall through to numeric fallback
    }
    return `Mod #${modId}`;
  }
  return "Nexus download";
}

/**
 * Synchronous variant – uses only data already present in the handoff
 * object (no network calls).  Useful when an async call is impractical.
 */
export function getModLabelSync(handoff: ApiNxmHandoffSummary): string {
  const modInfo = handoff.metadata?.mod_info;
  if (modInfo && typeof modInfo === "object") {
    const name = (modInfo as Record<string, unknown>)["name"];
    if (typeof name === "string" && name.trim()) {
      return name.trim();
    }
  }
  if (handoff.request?.mod_id != null) {
    return `Mod #${handoff.request.mod_id}`;
  }
  return "Nexus download";
}

export type WaitForHandoffOptions = {
  timeoutMs?: number;
  intervalMs?: number;
};

export function formatBytes(size?: number | null): string {
  if (typeof size !== "number" || !Number.isFinite(size) || size <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(
    Math.floor(Math.log(size) / Math.log(1024)),
    units.length - 1
  );
  const value = size / Math.pow(1024, exponent);
  const precision = value >= 10 || exponent === 0 ? 0 : 1;
  return `${value.toFixed(precision)} ${units[exponent]}`;
}

function parseNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export async function waitForMatchingHandoff(
  modId: number,
  fileId: number | null,
  options: WaitForHandoffOptions = {}
): Promise<ApiNxmHandoffSummary | null> {
  const timeoutMs = options.timeoutMs ?? 45_000;
  const intervalMs = options.intervalMs ?? 1_500;
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    let handoffs: ApiNxmHandoffSummary[] = [];
    try {
      handoffs = await listNxmHandoffs();
    } catch (err) {
      console.warn("Failed to list Nexus handoffs", err);
    }

    if (handoffs.length > 0) {
      const matches = handoffs.filter((handoff) => {
        const request = handoff.request;
        const requestModId = parseNumber(request?.mod_id);
        if (requestModId !== modId) return false;
        if (fileId == null) return true;
        const requestFileId = parseNumber(request?.file_id);
        if (requestFileId == null) return false;
        return requestFileId === fileId;
      });

      if (matches.length > 0) {
        const sorted = [...matches].sort(
          (a, b) => (b.created_at ?? 0) - (a.created_at ?? 0)
        );
        return sorted[0];
      }
    }

    await new Promise<void>((resolve) => setTimeout(resolve, intervalMs));
  }

  return null;
}

export type MonitorNxmProgressOptions = {
  pollIntervalMs?: number;
  onError?: (error: unknown) => void;
};

export type MonitorNxmProgressHandle = {
  stop: () => void;
};

export function monitorNxmDownloadProgress(
  handoffId: string,
  onUpdate: (
    progress: ApiNxmDownloadProgress | null,
    meta: { done: boolean }
  ) => void,
  options: MonitorNxmProgressOptions = {}
): MonitorNxmProgressHandle {
  if (typeof window === "undefined") {
    return { stop: () => undefined };
  }

  let cancelled = false;
  let timer: number | null = null;

  const stop = () => {
    if (cancelled) return;
    cancelled = true;
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  };

  const poll = async () => {
    if (cancelled) return;
    try {
      const response = await getNxmHandoff(handoffId);
      const progress = response?.handoff?.progress ?? null;
      onUpdate(progress, { done: false });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        onUpdate(null, { done: true });
        stop();
        return;
      }
      options.onError?.(err);
    }
  };

  void poll();
  const interval = Math.max(250, options.pollIntervalMs ?? 750);
  timer = window.setInterval(() => {
    void poll();
  }, interval);

  return { stop };
}

export type NxmProgressController = {
  toastId: string | number;
  stop: () => void;
  getLastDescription: () => string;
};

export type CreateProgressControllerOptions = {
  label?: string;
  initialMessage?: string;
  pollIntervalMs?: number;
};

export function createNxmProgressController(
  handoffId: string,
  options: CreateProgressControllerOptions = {}
): NxmProgressController {
  const label = options.label ?? "Downloading mod…";
  let lastDescription = options.initialMessage ?? "Preparing download…";
  const toastId = toast.loading(label, {
    description: lastDescription,
    duration: Infinity,
  });

  let active = true;

  const handleUpdate = (
    progress: ApiNxmDownloadProgress | null,
    meta: { done: boolean }
  ) => {
    if (!active) return;
    if (!progress) {
      if (meta.done && !lastDescription) {
        lastDescription = "Download finished";
      }
      return;
    }

    const parts: string[] = [];
    if (
      typeof progress.percent === "number" &&
      Number.isFinite(progress.percent)
    ) {
      parts.push(`${Math.round(progress.percent)}%`);
    }
    if (typeof progress.bytes_downloaded === "number") {
      if (
        typeof progress.bytes_total === "number" &&
        Number.isFinite(progress.bytes_total) &&
        progress.bytes_total > 0
      ) {
        parts.push(
          `${formatBytes(progress.bytes_downloaded)} / ${formatBytes(
            progress.bytes_total
          )}`
        );
      } else {
        parts.push(formatBytes(progress.bytes_downloaded));
      }
    }
    if (progress.message) {
      parts.push(progress.message);
    } else if (progress.stage === "downloading" && parts.length === 0) {
      parts.push("Downloading…");
    }
    if (progress.error) {
      parts.push(progress.error);
    }

    const description =
      parts.join(" · ") || progress.message || lastDescription;
    lastDescription = description;
    toast.loading(label, {
      id: toastId,
      description,
      duration: Infinity,
    });
  };

  const monitor = monitorNxmDownloadProgress(handoffId, handleUpdate, {
    pollIntervalMs: options.pollIntervalMs,
    onError: (error) => {
      if (!active) return;
      console.warn("Failed to poll Nexus handoff progress", error);
    },
  });

  const stop = () => {
    if (!active) return;
    active = false;
    monitor.stop();
  };

  return {
    toastId,
    stop,
    getLastDescription: () => lastDescription,
  };
}

/**
 * Checks if a filename follows the Nexus Mods official naming convention:
 * <name>-<mod_id>-<version>-<timestamp>.<ext>
 *
 * Examples of valid Nexus naming:
 * - BodyReshape_SueRiannLandauTheDebater_Base-7814-1-1-1776061797.rar
 * - Symbiote Psylocke-4664-4-5-1761343349 (1).rar
 *
 * Examples of invalid naming:
 * - Luna-Mirae-2099_Alt.rar
 * - GoldRushThing_9999999_P.zip
 */
export function isNexusOfficialNaming(filename: string): boolean {
  // Matches the official Nexus Mods filename convention:
  // <Name (non-greedy)>-<ModID 1-7 digits>-<Version>-<Timestamp 9-11 digits>[optional " (N)"].<ext>
  // The timestamp anchor (9-11 digit Unix epoch) ensures we don't confuse
  // version/mod_id segments with each other.
  const nexusPattern = /^(.+?)-(\d{1,7})-(\d[\d-]*\d|\d)-(\d{9,11})(?:\s\(\d+\))?\.[^.]+$/;
  return nexusPattern.test(filename);
}

/**
 * Extracts mod metadata from a Nexus-style filename if possible.
 */
export function parseNexusFilename(filename: string) {
  // Same pattern as isNexusOfficialNaming — anchored on 9-11 digit timestamp
  const nexusPattern = /^(.+?)-(\d{1,7})-(\d[\d-]*\d|\d)-(\d{9,11})(?:\s\(\d+\))?\.[^.]+$/;
  const match = filename.match(nexusPattern);
  if (!match) return null;

  return {
    name: match[1].replace(/_/g, " ").trim(),
    modId: parseInt(match[2], 10),
    version: match[3].replace(/-/g, "."), // Normalize dashes to dots for display
    timestamp: parseInt(match[4], 10),
  };
}
