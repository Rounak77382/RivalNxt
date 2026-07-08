import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
// AppHeader migrated into TabHeader; remove separate AppHeader import
import { GetStartedDialog } from "./components/GetStartedDialog";
import {
  SettingsDialog,
  type SettingsFormValues,
} from "./components/SettingsDialog";
import { TabHeader } from "./components/TabHeader";
import { DownloadsSidebar } from "./components/DownloadsSidebar";
import { DownloadsPage } from "./components/DownloadsPage";
import { ActiveModsView } from "./components/ActiveModsView";
import { CollectionsPage } from "./components/CollectionsPage";
import { BackupModal } from "./components/BackupModal";
import { AssignModIdModal } from "./components/AssignModIdModal";
import { ServerStartupOverlay } from "./components/ServerStartupOverlay";
import { NxmBackgroundListener } from "./components/NxmBackgroundListener";
import { GameUpdateModal, type GameUpdateStep, type GameUpdatePhase } from "./components/GameUpdateModal";
import { CrashDetectorModal } from "./components/CrashDetectorModal";
import { parseCrashContext, type CrashInfo } from "./lib/crashParser";
import { toast } from "sonner";
import { Toaster } from "./components/ui/sonner";
import { ThemeProvider } from "./components/ThemeProvider";
import { NSFWFilterProvider } from "./components/NSFWFilterProvider";
import { openInBrowser } from "./lib/tauri-utils";
import { initializeIcons } from "./lib/iconManager";
import {
  waitForMatchingHandoff,
  createNxmProgressController,
  type NxmProgressController,
} from "./lib/nxmHelpers";
import {
  refreshConflicts,
  listDownloads,
  listCollections,
  deleteLocalDownloads,
  updateMod,
  checkModUpdate,
  listNxmHandoffs,
  previewNxmHandoff,
  setActivePaks,
  disableAllMods,
  scanActive,
  getLocalDownload,
  getPakAssets,
  ApiError,
  type ApiDownload,
  type ApiPakAsset,
  type ApiNxmHandoffSummary,
  type ApiNxmPreview,
  getSettings,
  updateSettings,
  runSettingsTask,
  getSettingsTaskJob,
  getBootstrapStatus,
  getHealth,
  getModCustomImagePreviews,
  toggleFavourite,
  fetchFavourites,
  getGameVersionCheck,
  type ApiSettings,
  type ApiSettingsTaskResponse,
  type SettingsTask,
  type ApiUpdateSettingsRequest,
  type ApiBootstrapStatus,
} from "./lib/api";
import {
  deriveCategoryTags,
  categoriesMatchTag,
  getCategoryTokenSet,
} from "./lib/categoryUtils";

const CATEGORY_KEYWORD_SET = getCategoryTokenSet();


const GET_STARTED_STORAGE_KEY = "modmanager:get-started-complete";
const GAME_VERSION_STORAGE_KEY = "modmanager:game-paks-last-modified";

const SETTINGS_TASK_LABELS: Record<SettingsTask, string> = {
  ingest_download_assets: "Rebuild Local Downloads",
  scan_active_mods: "Rescan Active Mods",
  sync_nexus: "Sync Nexus API",
  rebuild_tags: "Rebuild Tags",
  rebuild_conflicts: "Rebuild Conflicts",
  bootstrap_rebuild: "Initial Database Build",
  rebuild_character_data: "Rebuild Character Data",
  delete_outdated_versions: "Delete Outdated Versions",
};

const PROGRESS_STAGE_FILTERS = [
  /downloading/i,
  /processing/i,
  /resolving/i,
  /queued/i,
];

const SUPPRESSED_BACKEND_ERROR_PATTERNS = [
  "failed to fetch",
  "networkerror when attempting to fetch resource",
  "network error when attempting to fetch resource",
  "load failed",
];

function shouldSuppressBackendError(value?: string | null): boolean {
  if (!value) {
    return false;
  }
  const normalized = value.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  return SUPPRESSED_BACKEND_ERROR_PATTERNS.some((pattern) =>
    normalized.includes(pattern),
  );
}

function sanitizeProgressDescription(value?: string): string | undefined {
  if (!value) {
    return undefined;
  }
  const segments = value
    .split("·")
    .map((segment) => segment.trim())
    .filter(Boolean)
    .filter(
      (segment) =>
        !PROGRESS_STAGE_FILTERS.some((pattern) => pattern.test(segment)),
    );
  const sanitized = segments.join(" · ").trim();
  return sanitized.length > 0 ? sanitized : undefined;
}

type NxmEntry = {
  summary: ApiNxmHandoffSummary;
  preview?: ApiNxmPreview | null;
  error?: string | null;
};

type BackendStatusState = {
  state: "starting" | "ready";
  lastError?: string | null;
};

export default function App() {
  // State management
  const [mods, setMods] = useState<any[]>([]);
  const [activeTab, setActiveTab] = useState<
    "downloads" | "active" | "collections"
  >("downloads");
  const [assignModIdTarget, setAssignModIdTarget] = useState<any | null>(null);
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [selectedCharacters, setSelectedCharacters] = useState<string[]>([]);
  const [selectedCustomTags, setSelectedCustomTags] = useState<string[]>([]);
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [nxmEntries, setNxmEntries] = useState<Record<string, NxmEntry>>({});
  const nxmEntriesRef = useRef<Record<string, NxmEntry>>({});
  // Track (mod_id, file_id) pairs being managed by update flow to prevent background listener from processing them
  const updateManagedPairsRef = useRef<Set<string>>(new Set());
  // Ref to always hold the latest fetchServerMods so event listeners don't go stale
  const fetchServerModsRef = useRef<() => Promise<any[]>>(async () => []);
  // Ref to always hold the latest refreshMods so event listeners don't go stale
  const refreshModsRef = useRef<() => Promise<void>>(async () => {});
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsData, setSettingsData] = useState<ApiSettings | null>(null);
  const [settingsTaskBusy, setSettingsTaskBusy] = useState<SettingsTask | null>(
    null,
  );
  const [settingsTaskJobs, setSettingsTaskJobs] = useState<
    Partial<Record<SettingsTask, ApiSettingsTaskResponse>>
  >({});
  const [conflictsReloadToken, setConflictsReloadToken] = useState(0);
  const [getStartedOpen, setGetStartedOpen] = useState(false);
  const [bootstrapStatus, setBootstrapStatus] =
    useState<ApiBootstrapStatus | null>(null);
  const [bootstrapJob, setBootstrapJob] =
    useState<ApiSettingsTaskResponse | null>(null);
  const [bootstrapRunning, setBootstrapRunning] = useState(false);
  const [backendStatus, setBackendStatus] = useState<BackendStatusState>({
    state: "starting",
    lastError: null,
  });
  // Game update detection state
  const [gameUpdateModalOpen, setGameUpdateModalOpen] = useState(false);
  const [gameUpdatePhase, setGameUpdatePhase] = useState<GameUpdatePhase>("checking");
  const [gameUpdateSteps, setGameUpdateSteps] = useState<GameUpdateStep[]>([]);
  const [gameUpdateLatestFile, setGameUpdateLatestFile] = useState<string | null>(null);
  const [gameUpdateNewCharacters, setGameUpdateNewCharacters] = useState<string[]>([]);
  const [gameUpdateNewSkins, setGameUpdateNewSkins] = useState<string[]>([]);
  const [backupOpen, setBackupOpen] = useState(false);
  const [collectionsCount, setCollectionsCount] = useState(0);
  const [backupsRefreshTrigger, setBackupsRefreshTrigger] = useState(0);

  // ── Crash Detector state ──────────────────────────────────────────────
  const [crashDetectorOpen, setCrashDetectorOpen] = useState(false);
  const [crashInfo, setCrashInfo] = useState<CrashInfo | null>(null);
  const [crashAllDownloads, setCrashAllDownloads] = useState<ApiDownload[]>([]);
  const [crashPakAssets, setCrashPakAssets] = useState<ApiPakAsset[]>([]);
  const crashWatcherStartedRef = useRef(false);

  const backendReady = backendStatus.state === "ready";

  const notifyConflictsDirty = useCallback(() => {
    setConflictsReloadToken((token) => token + 1);
  }, []);

  useEffect(() => {
    nxmEntriesRef.current = nxmEntries;
  }, [nxmEntries]);

  // Listen for author assignment events from AuthorPopover
  useEffect(() => {
    if (!backendReady) return;
    const handler = async () => {
      await refreshModsRef.current().catch(() => null);
    };
    window.addEventListener("refresh-downloads", handler);
    return () => window.removeEventListener("refresh-downloads", handler);
  // Only need to register/unregister on backendReady change; ref keeps fn current
  }, [backendReady]);

  useEffect(() => {
    let isCancelled = false;
    let attempts = 0;
    let timeoutId: number | null = null;

    const pollHealth = async () => {
      if (isCancelled) {
        return;
      }
      attempts += 1;
      try {
        const health = await getHealth();
        if (isCancelled) {
          return;
        }
        if (health?.ok) {
          if (timeoutId != null) {
            window.clearTimeout(timeoutId);
            timeoutId = null;
          }
          setBackendStatus({ state: "ready", lastError: null });
          return;
        }
        const rawError = typeof health?.error === "string" ? health.error : "";
        const trimmedError = rawError.trim();
        const suppress = shouldSuppressBackendError(trimmedError);
        setBackendStatus({
          state: "starting",
          lastError: suppress ? null : trimmedError || null,
        });
      } catch (error) {
        if (isCancelled) {
          return;
        }
        const rawMessage =
          error instanceof Error
            ? error.message
            : typeof error === "string"
              ? error
              : "";
        const trimmedMessage = rawMessage.trim();
        const suppress = shouldSuppressBackendError(trimmedMessage);
        setBackendStatus({
          state: "starting",
          lastError: suppress
            ? null
            : trimmedMessage || "Unable to reach backend",
        });
      }

      if (isCancelled) {
        return;
      }

      const delay = Math.min(2500, 600 + attempts * 200);
      if (timeoutId != null) {
        window.clearTimeout(timeoutId);
      }
      timeoutId = window.setTimeout(pollHealth, delay);
    };

    pollHealth();

    return () => {
      isCancelled = true;
      if (timeoutId != null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, []);

  const updateNxmEntry = useCallback((id: string, patch: Partial<NxmEntry>) => {
    setNxmEntries((prev) => {
      if (!prev[id]) {
        return prev;
      }
      const nextEntry = { ...prev[id], ...patch };
      const next = { ...prev, [id]: nextEntry };
      nxmEntriesRef.current = next;
      return next;
    });
  }, []);

  const fetchSettings = useCallback(async (showToast: boolean = true) => {
    setSettingsLoading(true);
    try {
      const data = await getSettings();
      setSettingsData(data);
    } catch (err) {
      const message =
        err instanceof Error && err.message
          ? err.message
          : String(err ?? "Failed to load settings");
      if (showToast) {
        toast.error(`Failed to load settings: ${message}`);
      } else {
        console.error("Failed to load settings", err);
      }
    } finally {
      setSettingsLoading(false);
    }
  }, []);

  const fetchCollectionsCount = useCallback(async () => {
    try {
      const summaries = await listCollections();
      setCollectionsCount(summaries.length);
    } catch (err) {
      console.error("Failed to fetch collections count", err);
    }
  }, []);

  const fetchBootstrapStatus = useCallback(async () => {
    try {
      const status = await getBootstrapStatus();
      setBootstrapStatus(status);
      return status;
    } catch (err) {
      console.error("Failed to fetch bootstrap status", err);
      return null;
    }
  }, []);

  const saveSettings = useCallback(
    async (values: SettingsFormValues): Promise<boolean> => {
      setSettingsSaving(true);
      try {
        const payload: ApiUpdateSettingsRequest = {
          allow_direct_api_downloads: values.allow_direct_api_downloads,
          nexus_api_key: values.nexus_api_key.trim(),
          aes_key_hex: values.aes_key_hex.trim(),
          marvel_rivals_root: values.marvel_rivals_root.trim() || null,
          marvel_rivals_local_downloads_root:
            values.marvel_rivals_local_downloads_root.trim() || null,
          seven_zip_bin: values.seven_zip_bin.trim() || null,
        };
        const dataDir = values.data_dir.trim();
        if (dataDir) {
          payload.data_dir = dataDir;
        }
        const updated = await updateSettings(payload);
        setSettingsData(updated);
        toast.success("Settings updated");
        // Auto-refresh after settings change
        void refreshMods({ includeConflicts: true });
        return true;
      } catch (err) {
        const message =
          err instanceof Error && err.message
            ? err.message
            : String(err ?? "Failed to save settings");
        toast.error(`Failed to save settings: ${message}`);
        return false;
      } finally {
        setSettingsSaving(false);
      }
    },
    [],
  );

  const handleSettingsSubmit = useCallback(
    async (values: SettingsFormValues) => {
      const success = await saveSettings(values);
      if (success) {
        setSettingsOpen(false);
      }
    },
    [saveSettings],
  );

  const handleSettingsRefresh = useCallback(() => {
    void fetchSettings();
  }, [fetchSettings]);

  const handleOpenSettings = useCallback(() => {
    if (!settingsLoading) {
      void fetchSettings();
    }
    setSettingsOpen(true);
  }, [fetchSettings, settingsLoading]);

  const handleOpenBootstrap = useCallback(() => {
    if (!settingsLoading && settingsData == null) {
      void fetchSettings(false);
    }
    void fetchBootstrapStatus();
    setGetStartedOpen(true);
  }, [fetchBootstrapStatus, fetchSettings, settingsData, settingsLoading]);

  const handleSettingsOpenChange = useCallback(
    (isOpen: boolean) => {
      setSettingsOpen(isOpen);
      if (isOpen) {
        if (!settingsLoading && settingsData == null) {
          void fetchSettings();
        }
        return;
      }
      setSettingsTaskBusy(null);
    },
    [fetchSettings, settingsData, settingsLoading],
  );

  const fetchNxmQueue = useCallback(async () => {
    try {
      const handoffs = await listNxmHandoffs();
      const next: Record<string, NxmEntry> = {};
      for (const handoff of handoffs) {
        const previous = nxmEntriesRef.current[handoff.id];
        next[handoff.id] = {
          summary: handoff,
          preview: previous?.preview ?? null,
          error: previous?.error ?? null,
        };
      }
      setNxmEntries(next);
      nxmEntriesRef.current = next;
      for (const handoff of handoffs) {
        const entry = next[handoff.id];
        if (!entry || entry.preview || entry.error || handoff.progress?.stage === "failed") {
          continue;
        }
        try {
          const preview = await previewNxmHandoff(handoff.id);
          updateNxmEntry(handoff.id, { preview, error: null });
        } catch (err) {
          const message =
            err instanceof Error
              ? err.message
              : String(err ?? "Preview failed");
          updateNxmEntry(handoff.id, { error: message });
        }
      }
    } catch (err) {
      console.error("Failed to fetch Nexus handoffs", err);
    }
  }, [updateNxmEntry]);

  useEffect(() => {
    if (!backendReady) {
      return undefined;
    }
    void fetchNxmQueue();
    const interval = window.setInterval(() => {
      void fetchNxmQueue();
    }, 10000);
    return () => window.clearInterval(interval);
  }, [backendReady, fetchNxmQueue]);

  useEffect(() => {
    if (!backendReady) {
      return undefined;
    }
    void fetchCollectionsCount();
    const interval = window.setInterval(() => {
      void fetchCollectionsCount();
    }, 5000);
    return () => window.clearInterval(interval);
  }, [backendReady, fetchCollectionsCount]);

  // ── Crash Detector: start watcher & listen for events ────────────────
  useEffect(() => {
    if (!backendReady) return;
    if (crashWatcherStartedRef.current) return;
    crashWatcherStartedRef.current = true;

    // Start the Rust background crash-dir watcher
    invoke("watch_crash_dir").catch((e: unknown) =>
      console.warn("[CrashDetector] Failed to start watcher:", e)
    );

    let unlisten: (() => void) | null = null;

    listen<string>("crash-detected", async (event) => {
      console.log("[CrashDetector] crash-detected event received");
      const xmlContent = event.payload;
      const parsed = parseCrashContext(xmlContent);

      // Fetch all downloads so the modal can show active mods
      let downloads: ApiDownload[] = [];
      let pakAssets: ApiPakAsset[] = [];
      try {
        downloads = await listDownloads();
        const activeIds = downloads
          .filter((d) => d.active_paks && d.active_paks.length > 0)
          .map((d) => d.id);
        if (activeIds.length > 0) {
          pakAssets = await getPakAssets(activeIds);
        }
      } catch (e) {
        console.warn("[CrashDetector] Failed to fetch downloads/assets:", e);
      }

      setCrashInfo(parsed);
      setCrashAllDownloads(downloads);
      setCrashPakAssets(pakAssets);
      setCrashDetectorOpen(true);
    }).then((fn) => {
      unlisten = fn;
    }).catch((e: unknown) =>
      console.warn("[CrashDetector] Failed to listen for crash events:", e)
    );

    return () => {
      unlisten?.();
    };
  }, [backendReady]);

  useEffect(() => {
    if (!backendReady) {
      return undefined;
    }
    let cancelled = false;
    (async () => {
      console.log("[App] Checking bootstrap status...");
      const status = await fetchBootstrapStatus();
      console.log("[App] Bootstrap status:", status);

      if (cancelled || !status) {
        console.log("[App] Cancelled or no status, skipping modal check");
        return;
      }

      const storedFlag =
        typeof window !== "undefined"
          ? window.localStorage.getItem(GET_STARTED_STORAGE_KEY)
          : null;

      console.log("[App] Get Started storage flag:", storedFlag);
      console.log("[App] needs_bootstrap:", status.needs_bootstrap);
      console.log("[App] db_exists:", status.db_exists);
      console.log("[App] downloads_count:", status.downloads_count);
      console.log("[App] mods_count:", status.mods_count);

      if (status.needs_bootstrap && storedFlag !== "true") {
        console.log("[App] Bootstrap needed - preparing to show modal");
        if (!settingsLoading && settingsData == null) {
          console.log("[App] Loading settings first...");
          await fetchSettings(false);
        }
        if (!cancelled) {
          console.log("[App] Opening Get Started modal");
          setGetStartedOpen(true);
        }
      } else {
        console.log("[App] Modal not needed:", {
          needs_bootstrap: status.needs_bootstrap,
          storedFlag,
          willOpen: status.needs_bootstrap && storedFlag !== "true",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    backendReady,
    fetchBootstrapStatus,
    fetchSettings,
    settingsData,
    settingsLoading,
  ]);

  // ── Game version update detection ──────────────────────────────────
  const runGameUpdateRebuild = useCallback(
    async (latestModified: string) => {
      const sleep = (ms: number) =>
        new Promise<void>((resolve) => {
          setTimeout(resolve, ms);
        });

      const STEPS: { key: SettingsTask; label: string }[] = [
        {
          key: "rebuild_character_data",
          label: "Rebuild Character & Skin Data",
        },
        { key: "rebuild_tags", label: "Rebuild Tags" },
      ];

      const initialSteps: GameUpdateStep[] = STEPS.map((s) => ({
        key: s.key,
        label: s.label,
        status: "pending" as const,
      }));
      setGameUpdateSteps(initialSteps);
      setGameUpdatePhase("updating");

      let allSucceeded = true;

      for (let i = 0; i < STEPS.length; i++) {
        const { key } = STEPS[i];

        // Mark current step as running
        setGameUpdateSteps((prev) =>
          prev.map((s, idx) =>
            idx === i ? { ...s, status: "running" } : s,
          ),
        );

        try {
          const job = await runSettingsTask(key);

          const terminalStatuses: Array<ApiSettingsTaskResponse["status"]> = [
            "succeeded",
            "failed",
          ];
          let currentJob = job;
          let delay = 400;
          while (!terminalStatuses.includes(currentJob.status)) {
            await sleep(delay);
            delay = Math.min(delay + 250, 2000);
            currentJob = await getSettingsTaskJob(currentJob.id);
          }

          const succeeded =
            currentJob.status === "succeeded" && Boolean(currentJob.ok);

          setGameUpdateSteps((prev) =>
            prev.map((s, idx) =>
              idx === i
                ? {
                    ...s,
                    status: succeeded ? "succeeded" : "failed",
                    error: succeeded ? null : (currentJob.error || "Task failed"),
                  }
                : s,
            ),
          );

          if (succeeded && key === "rebuild_character_data" && currentJob.metadata) {
            const meta = currentJob.metadata as any;
            if (Array.isArray(meta.new_characters)) {
              setGameUpdateNewCharacters(meta.new_characters);
            }
            if (Array.isArray(meta.new_skins)) {
              setGameUpdateNewSkins(meta.new_skins);
            }
          }

          if (!succeeded) {
            allSucceeded = false;
            break;
          }
        } catch (err) {
          const message =
            err instanceof Error ? err.message : String(err ?? "Task failed");
          setGameUpdateSteps((prev) =>
            prev.map((s, idx) =>
              idx === i
                ? { ...s, status: "failed", error: message }
                : s,
            ),
          );
          allSucceeded = false;
          break;
        }
      }

      setGameUpdatePhase("done");

      if (allSucceeded) {
        // Save the new timestamp so we don't trigger again
        window.localStorage.setItem(
          GAME_VERSION_STORAGE_KEY,
          latestModified,
        );
        // Refresh mod data
        void refreshMods({ quiet: true, includeConflicts: true });
      }
    },
    [],
  );

  useEffect(() => {
    if (!backendReady) return;
    let cancelled = false;

    (async () => {
      // Show modal immediately in "checking" state
      setGameUpdatePhase("checking");
      setGameUpdateSteps([]);
      setGameUpdateModalOpen(true);

      try {
        const result = await getGameVersionCheck();
        if (cancelled) return;

        if (!result.ok || !result.latest_modified) {
          // Can't check – just close the modal
          console.warn("[GameUpdate] Version check returned not ok:", result.error);
          setGameUpdateModalOpen(false);
          return;
        }

        const stored = window.localStorage.getItem(GAME_VERSION_STORAGE_KEY);
        console.log(
          "[GameUpdate] Latest PAK modified:",
          result.latest_modified,
          "| Stored:",
          stored,
          "| File:",
          result.latest_file,
        );

        if (!stored) {
          // First run – store timestamp and show up to date
          window.localStorage.setItem(
            GAME_VERSION_STORAGE_KEY,
            result.latest_modified,
          );
          console.log("[GameUpdate] First run – stored initial timestamp");
          setGameUpdatePhase("uptodate");
          return;
        }

        // Compare timestamps
        const storedDate = new Date(stored).getTime();
        const latestDate = new Date(result.latest_modified).getTime();

        if (latestDate > storedDate) {
          console.log("[GameUpdate] Game files are newer – triggering rebuild");
          setGameUpdateLatestFile(result.latest_file);
          await runGameUpdateRebuild(result.latest_modified);
        } else {
          console.log("[GameUpdate] Game files unchanged – no update needed");
          setGameUpdatePhase("uptodate");
        }
      } catch (err) {
        console.warn("[GameUpdate] Version check failed:", err);
        setGameUpdateModalOpen(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [backendReady, runGameUpdateRebuild]);

  // Get counts for header
  const installedMods = mods.filter((mod) => mod.isInstalled);
  const activeMods = installedMods.filter((mod) => mod.isActive !== false);

  // Compute unique update count: dedupe by backend mod id when present, otherwise by normalized name
  const updatesCount = (() => {
    const seen = new Set<string>();
    for (const mod of installedMods) {
      if (!mod.hasUpdate) continue;

      if (
        typeof mod.backendModId === "number" &&
        Number.isFinite(mod.backendModId)
      ) {
        seen.add(`id:${String(mod.backendModId)}`);
      } else if (mod.name) {
        seen.add(`name:${String(mod.name).toLowerCase().trim()}`);
      } else {
        // fallback to the internal id to avoid losing track
        seen.add(`internal:${String(mod.id)}`);
      }
    }
    return seen.size;
  })();

  // Get counts by category for sidebar
  const modMatchesCategory = (mod: any, categoryId: string) => {
    if (Array.isArray(mod?.categoryTags)) {
      return mod.categoryTags.includes(categoryId);
    }
    return categoriesMatchTag(mod?.tags, categoryId);
  };
  const installedCounts = {
    all: installedMods.length,
    characters: installedMods.filter((mod) =>
      modMatchesCategory(mod, "characters"),
    ).length,
    ui: installedMods.filter((mod) => modMatchesCategory(mod, "ui")).length,
    maps: installedMods.filter((mod) => modMatchesCategory(mod, "maps")).length,
    audio: installedMods.filter((mod) => modMatchesCategory(mod, "audio"))
      .length,
  };

  // Event handlers
  async function fetchServerMods(): Promise<any[]> {
    // Keep the ref updated so the refresh-downloads event handler always uses the latest version
    fetchServerModsRef.current = fetchServerMods;
    // Start fetching downloads and favourites in parallel
    const [downloads, favouritedIds] = await Promise.all([
      listDownloads(),
      fetchFavourites().catch((err) => {
        console.warn("[fetchServerMods] Failed to fetch favourites:", err);
        return [] as number[];
      }),
    ]);

    const grouped = groupDownloadsByMod(downloads);

    // Extract mod_ids to fetch the newly optimized (downscaled) custom previews
    const modIds: number[] = [];
    for (const d of grouped) {
      if (d.mod_id != null) {
        modIds.push(d.mod_id);
      } else if (d.id != null) {
        modIds.push(-d.id);
      }
    }

    const customImages =
      modIds.length > 0
        ? await getModCustomImagePreviews(modIds).catch(() => ({}))
        : {};

    const favSet = new Set(favouritedIds);
    const mapped = grouped.map((d) => toUiMod(d, customImages));

    // Restore favourites from backend
    for (const mod of mapped) {
      if (mod.backendModId != null && favSet.has(mod.backendModId)) {
        mod.isFavorited = true;
      }
    }



    // Debug: Check NSFW content after mapping
    const nsfwMapped = mapped.filter((m) => m.containsAdultContent);
    console.log(
      "[fetchServerMods] Mapped NSFW count:",
      nsfwMapped.length,
      "of",
      mapped.length,
    );
    if (nsfwMapped.length > 0) {
      console.log(
        "[fetchServerMods] NSFW mods mapped:",
        nsfwMapped.map((m) => ({
          name: m.name,
          containsAdultContent: m.containsAdultContent,
        })),
      );
    }


    // Debug: Log all mods that are flagged as having updates so we can detect false positives
    const updateMods = mapped.filter((m) => m.hasUpdate);
    if (updateMods.length > 0) {
      console.group(`[fetchServerMods] ${updateMods.length} mods flagged hasUpdate=true`);
      for (const m of updateMods) {
        const grouped_d = grouped.find((g) => String(g.mod_id) === m.id || String(g.id) === m.id);
        console.log(`  ${m.name}: installedVersion="${m.installedVersion}" latestVersion="${m.latestVersion}" hasUpdate=${m.hasUpdate}`, {
          d_version: grouped_d?.version,
          d_latest_version: grouped_d?.latest_version,
          d_needs_update: grouped_d?.needs_update,
          d_local_version_key: grouped_d?.local_version_key,
          d_latest_version_key: grouped_d?.latest_version_key,
        });
      }
      console.groupEnd();
    }

    return dedupeById(mapped);
  }

  const handleUninstall = async (modId: string) => {
    const mod = mods.find((m) => String(m.id) === String(modId));
    if (!mod) {
      return;
    }

    const sourceIds = Array.isArray(mod.sourceDownloadIds)
      ? mod.sourceDownloadIds
      : [];
    const numericIds = sourceIds
      .map((value: unknown) => {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : undefined;
      })
      .filter(
        (value: number | undefined): value is number =>
          typeof value === "number",
      );
    const downloadIds = Array.from(new Set<number>(numericIds));

    let backendModId: number | undefined;
    if (
      typeof mod.backendModId === "number" &&
      Number.isFinite(mod.backendModId)
    ) {
      backendModId = mod.backendModId;
    } else {
      const parsed = Number(modId);
      backendModId = Number.isFinite(parsed) ? parsed : undefined;
    }

    if (downloadIds.length === 0 && backendModId == null) {
      toast.error(`Can't delete ${mod.name}: missing download reference`);
      return;
    }

    try {
      // Step 1: Deactivate all active paks first if the mod is active
      if (mod.isActive !== false && downloadIds.length > 0) {
        // Update UI state optimistically
        setMods((prev) =>
          prev.map((m) => (m.id === modId ? { ...m, isActive: false } : m)),
        );

        // Actually deactivate on backend for each download
        for (const downloadId of downloadIds) {
          try {
            await setActivePaks(downloadId, []);
          } catch (deactivateError) {
            console.warn(
              `[App] Failed to deactivate download_id=${downloadId}`,
              deactivateError,
            );
            // Continue with other downloads and deletion even if one fails
          }
        }

        // Scan to update file system state
        try {
          await scanActive();
        } catch (scanError) {
          console.warn("[App] scanActive after deactivation failed", scanError);
        }

        toast.info(`${mod.name} deactivated before removal`);
      }

      // Step 2: Delete the mod
      await deleteLocalDownloads(downloadIds, backendModId);
      const deduped = await fetchServerMods();
      setMods(deduped);
      toast.success(`${mod.name} removed from local downloads`);
      // Auto-refresh after mod deletion
      void refreshMods({ includeConflicts: true });
    } catch (e: any) {
      const message = e?.message ?? String(e);
      toast.error(`Failed to delete ${mod.name}: ${message}`);
    }
  };

  const handleUpdate = async (modId: string, targetFileId?: number) => {
    const target = mods.find((m) => m.id === modId);
    if (!target) {
      return;
    }

    const displayName = target.name ?? `Mod ${modId}`;
    let backendModId: number | undefined;
    if (
      typeof target.backendModId === "number" &&
      Number.isFinite(target.backendModId)
    ) {
      backendModId = target.backendModId;
    } else {
      const parsed = Number(modId);
      backendModId = Number.isFinite(parsed) ? parsed : undefined;
    }

    if (backendModId == null) {
      toast.error(`Can't update ${displayName}: missing Nexus mod reference`);
      return;
    }

    const shouldActivate = target.isActive !== false;

    setMods((prev) =>
      prev.map((mod) =>
        mod.id === modId
          ? {
              ...mod,
              isUpdating: true,
              updateError: null,
            }
          : mod,
      ),
    );

    let responseLatestVersion = target.latestVersion || target.version || "";

    const applyUpdateSuccess = async (
      result: any,
      options: { toastId?: string | number; progressDescription?: string } = {},
    ) => {
      responseLatestVersion = result.latest_version || responseLatestVersion;
      await refreshMods({ quiet: true });
      // Auto-refresh after update completes
      void refreshMods({ includeConflicts: true });
      // Force sidebar summary refresh (downloads summary / last-check)
      try {
        setConflictsReloadToken((t) => t + 1);
      } catch (e) {
        // ignore if token state isn't available
      }
      const message = result.already_latest
        ? `${displayName} is already on the latest version (${
            responseLatestVersion || "unknown"
          })`
        : `${displayName} updated to v${responseLatestVersion || "latest"}`;
      const hasWarning =
        typeof result.activation_warning === "string" &&
        result.activation_warning.trim().length > 0;
      const warningText = hasWarning
        ? result.activation_warning?.trim()
        : undefined;
      const progressDescription = sanitizeProgressDescription(
        options.progressDescription,
      );
      const description =
        warningText && warningText.length > 0
          ? warningText
          : progressDescription;
      toast.success(message, {
        description,
        id: options.toastId,
        duration: 4000,
      });
      setMods((prev) =>
        prev.map((mod) =>
          mod.id === modId
            ? {
                ...mod,
                isUpdating: false,
                updateError: null,
              }
            : mod,
        ),
      );
    };

    try {
      const response = await updateMod(backendModId, {
        activate: shouldActivate,
        ...(targetFileId != null ? { fileId: targetFileId } : {}),
      });
      await applyUpdateSuccess(response);
      return;
    } catch (error) {
      const setUpdateError = (message: string) => {
        setMods((prev) =>
          prev.map((mod) =>
            mod.id === modId
              ? {
                  ...mod,
                  isUpdating: false,
                  updateError: message,
                  hasUpdate: true,
                }
              : mod,
          ),
        );
      };

      if (error instanceof ApiError) {
        const detail = error.detail as Record<string, unknown> | undefined;
        if (
          detail &&
          typeof detail === "object" &&
          detail["requires_nxm_handoff"]
        ) {
          const instructions =
            typeof detail["message"] === "string" &&
            detail["message"].trim().length > 0
              ? (detail["message"] as string).trim()
              : "Nexus Mods requires a browser-initiated handoff before the download can continue.";
          const nexusGame =
            typeof detail["game"] === "string" &&
            detail["game"].trim().length > 0
              ? (detail["game"] as string)
              : "marvelrivals";
          const nexusModId =
            typeof detail["mod_id"] === "number"
              ? (detail["mod_id"] as number)
              : (backendModId ?? undefined);
          const fileIdText =
            typeof detail["file_id"] === "number"
              ? `File #${detail["file_id"] as number}`
              : typeof detail["file_id"] === "string" &&
                  detail["file_id"].trim()
                ? `File #${detail["file_id"] as string}`
                : "the desired file";
          const nexusUrl =
            nexusModId != null
              ? (() => {
                  const base = `https://www.nexusmods.com/${encodeURIComponent(
                    nexusGame,
                  )}/mods/${encodeURIComponent(String(nexusModId))}`;
                  const params = new URLSearchParams();
                  params.set("tab", "files");
                  const fileIdValue = detail["file_id"];
                  if (
                    (typeof fileIdValue === "number" &&
                      Number.isFinite(fileIdValue)) ||
                    (typeof fileIdValue === "string" &&
                      fileIdValue.trim().length > 0)
                  ) {
                    params.set("file_id", String(fileIdValue).trim());
                    params.set("nmm", "1"); // Ensure Nexus shows the Mod Manager (nmm) UI when possible
                  }
                  return `${base}?${params.toString()}`;
                })()
              : undefined;

          toast.warning(`Action needed for ${displayName}`, {
            description: `${instructions} We've opened the Nexus Mods page so you can click "Mod Manager Download" for ${fileIdText}. We'll watch for the handoff and finish the update automatically once it appears.`,
          });

          if (nexusUrl) {
            try {
              await openInBrowser(nexusUrl);
            } catch (openErr) {
              console.warn("Failed to open Nexus Mods page", openErr);
            }
          }

          void fetchNxmQueue();

          const fileIdRaw = detail["file_id"];
          let expectedFileId: number | null = null;
          if (typeof fileIdRaw === "number" && Number.isFinite(fileIdRaw)) {
            expectedFileId = fileIdRaw;
          } else if (typeof fileIdRaw === "string" && fileIdRaw.trim()) {
            const parsed = Number.parseInt(fileIdRaw.trim(), 10);
            if (Number.isFinite(parsed)) {
              expectedFileId = parsed;
            }
          }

          let controller: NxmProgressController | null = null;
          try {
            const expectedModId = nexusModId ?? backendModId;
            if (expectedModId == null) {
              throw new Error("Missing Nexus mod id for the handoff.");
            }

            // Mark this (mod_id, file_id) pair as managed BEFORE waiting for handoff
            // This prevents NxmBackgroundListener from processing it first
            const trackingKey =
              expectedFileId != null
                ? `${expectedModId}:${expectedFileId}`
                : `${expectedModId}:*`;
            updateManagedPairsRef.current.add(trackingKey);

            const handoff = await waitForMatchingHandoff(
              expectedModId,
              expectedFileId,
            );
            if (!handoff) {
              // Remove tracking on timeout
              updateManagedPairsRef.current.delete(trackingKey);
              throw new Error(
                "Timed out waiting for the RivalNxt download handoff.",
              );
            }

            if (handoff.id) {
              controller = createNxmProgressController(handoff.id, {
                label: `Updating ${displayName}`,
                initialMessage: instructions,
              });
            }

            const followUp = await updateMod(backendModId, {
              activate: shouldActivate,
              handoffId: handoff.id,
              ...(expectedFileId != null ? { fileId: expectedFileId } : {}),
            });
            const progressDescription = controller?.getLastDescription();
            const toastId = controller?.toastId;
            controller?.stop();

            // Remove from managed set since processing is complete
            updateManagedPairsRef.current.delete(trackingKey);

            await applyUpdateSuccess(followUp, {
              toastId,
              progressDescription,
            });
            void fetchNxmQueue();
            return;
          } catch (handoffErr) {
            const message =
              handoffErr instanceof Error && handoffErr.message
                ? handoffErr.message
                : String(handoffErr ?? "Unknown handoff error");

            // Check if this is a duplicate download error
            const isDuplicate =
              message.toLowerCase().includes("already exists") ||
              message.toLowerCase().includes("duplicate") ||
              (typeof handoffErr === "object" &&
                handoffErr !== null &&
                "status" in handoffErr &&
                (handoffErr as any).status === 409);

            const toastId = controller?.toastId;
            const description =
              controller?.getLastDescription() || instructions || undefined;
            controller?.stop();

            if (isDuplicate) {
              // Extract better message from error detail if available
              let duplicateMessage = message;
              if (
                typeof handoffErr === "object" &&
                handoffErr !== null &&
                "body" in handoffErr &&
                typeof (handoffErr as any).body === "object"
              ) {
                const detail = (handoffErr as any).body?.detail;
                if (typeof detail === "object" && detail?.message) {
                  duplicateMessage = detail.message;
                } else if (typeof detail === "string") {
                  duplicateMessage = detail;
                }
              }

              // Clear update state
              setMods((prev) =>
                prev.map((mod) =>
                  mod.id === modId
                    ? {
                        ...mod,
                        isUpdating: false,
                        updateError: null,
                      }
                    : mod,
                ),
              );

              // Show info toast instead of error
              if (toastId != null) {
                toast.info(`${displayName} already up to date`, {
                  id: toastId,
                  description: duplicateMessage,
                  duration: 4000,
                });
              } else {
                toast.info(`${displayName} already up to date`, {
                  description: duplicateMessage,
                  duration: 4000,
                });
              }

              void fetchNxmQueue();
              return;
            }

            // Not a duplicate - handle as error
            setUpdateError(`${instructions} (${message})`);
            if (toastId != null) {
              toast.error(
                `Failed to resume Nexus download for ${displayName}: ${message}`,
                {
                  id: toastId,
                  description,
                  duration: 5000,
                },
              );
            } else {
              toast.error(
                `Failed to resume Nexus download for ${displayName}: ${message}`,
              );
            }
            void fetchNxmQueue();
            return;
          }
        }
      }

      let message: string;
      if (error instanceof Error && error.message) {
        message = error.message;
      } else if (typeof error === "string") {
        message = error;
      } else {
        try {
          message = JSON.stringify(error);
        } catch {
          message = String(error);
        }
      }
      setUpdateError(message);
      toast.error(`Failed to update ${displayName}: ${message}`);
      return;
    }
  };

  const handleCheckUpdate = async (modId: string) => {
    const target = mods.find((m) => m.id === modId);
    if (!target) {
      return;
    }

    const displayName = target.name ?? `Mod ${modId}`;
    let backendModId: number | undefined;
    if (
      typeof target.backendModId === "number" &&
      Number.isFinite(target.backendModId)
    ) {
      backendModId = target.backendModId;
    } else {
      const parsed = Number(modId);
      backendModId = Number.isFinite(parsed) ? parsed : undefined;
    }

    if (backendModId == null) {
      toast.error(
        `Can't check updates for ${displayName}: missing Nexus mod reference`,
      );
      return;
    }

    try {
      const result = await checkModUpdate(backendModId);
      if (result.needs_update) {
        toast.info(`Update available for ${displayName}`);
        await refreshMods({ quiet: true });
        // Force sidebar summary refresh to update the "needs update" count
        setConflictsReloadToken((t) => t + 1);
      } else {
        toast.success(`${displayName} is up to date`);
      }
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : String(error ?? "Failed to check for updates");
      toast.error(`Failed to check updates for ${displayName}: ${message}`);
    }
  };

  const handleFavorite = async (modId: string) => {
    const mod = mods.find((m) => m.id === modId);
    if (!mod) return;

    // Optimistic UI update
    const wasFavorited = mod.isFavorited;
    setMods((prev) =>
      prev.map((m) =>
        m.id === modId ? { ...m, isFavorited: !wasFavorited } : m,
      ),
    );

    // Persist to backend
    const backendId = mod.backendModId;
    if (backendId != null) {
      try {
        await toggleFavourite(backendId);
      } catch (err) {
        // Revert on failure
        setMods((prev) =>
          prev.map((m) =>
            m.id === modId ? { ...m, isFavorited: wasFavorited } : m,
          ),
        );
        toast.error("Failed to update favourite status");
        return;
      }
    }

    toast.success(
      wasFavorited
        ? `${mod.name} removed from favorites`
        : `${mod.name} added to favorites!`,
    );
  };


  const handleToggleMod = async (modId: string) => {
    const mod = mods.find((m) => m.id === modId);
    if (!mod || !mod.isInstalled) return;

    const willActivate = !mod.isActive;

    // Optimistically update React state
    setMods((prev) =>
      prev.map((m) =>
        m.id === modId ? { ...m, isActive: willActivate } : m
      )
    );

    const toastId = `toggle-${modId}`;
    toast.loading(willActivate ? `Enabling ${mod.name}...` : `Disabling ${mod.name}...`, { id: toastId });

    try {
      const downloadIds = mod.sourceDownloadIds || [];
      if (downloadIds.length > 0) {
        for (const dlId of downloadIds) {
          if (willActivate) {
            const dl = await getLocalDownload(Number(dlId));
            const paks = (dl.contents || []).filter((f: string) => f.toLowerCase().endsWith(".pak"));
            if (paks.length === 0) {
              // Mod file on disk is missing / orphaned — give a clear error
              toast.error(`Cannot enable ${mod.name}: no PAK files found on disk.`, {
                id: toastId,
                description: 'The mod file may have been moved or deleted. Go to Settings → "Rebuild Local Downloads" to rescan.',
                duration: 8000,
              });
              // Revert optimistic state
              setMods((prev) =>
                prev.map((m) =>
                  m.id === modId ? { ...m, isActive: mod.isActive } : m
                )
              );
              return;
            }
            await setActivePaks(Number(dlId), paks);
          } else {
            await setActivePaks(Number(dlId), []);
          }
        }
      }
      await scanActive();
      toast.success(willActivate ? `${mod.name} has been enabled!` : `${mod.name} has been disabled`, { id: toastId });
    } catch (error: any) {
      toast.error(error?.message || `Failed to toggle ${mod.name}`, { id: toastId });
      // Revert React state
      setMods((prev) =>
        prev.map((m) =>
          m.id === modId ? { ...m, isActive: mod.isActive } : m
        )
      );
    } finally {
      void refreshMods({ includeConflicts: true });
    }
  };

  const handleDisableAll = async () => {
    const toastId = "disable-all";
    toast.loading("Disabling active mods...", { id: toastId });

    try {
      await scanActive();
      const allDownloads = await listDownloads();
      const activeDownloads = allDownloads.filter(
        (dl) => dl.active_paks && dl.active_paks.length > 0
      );

      let deactivatedCount = 0;
      for (const dl of activeDownloads) {
        await setActivePaks(Number(dl.id), []);
        deactivatedCount++;
      }

      // If nothing was deactivated via complete sweep, try falling back to legacy list to be safe
      if (deactivatedCount === 0) {
        const activeModsToDisable = installedMods.filter((mod) => mod.isActive !== false);
        for (const mod of activeModsToDisable) {
          const downloadIds = mod.sourceDownloadIds || [];
          for (const dlId of downloadIds) {
            await setActivePaks(Number(dlId), []);
            deactivatedCount++;
          }
        }
      }

      await scanActive();
      if (deactivatedCount === 0) {
        toast.info("No active mods to disable", { id: toastId });
      } else {
        toast.success(`Successfully disabled all active mods (${deactivatedCount} deactivated)`, { id: toastId });
      }
    } catch (error: any) {
      toast.error(error?.message || "Failed to disable mods", { id: toastId });
    } finally {
      void refreshMods({ includeConflicts: true });
    }
  };

  const handleEnableAll = async () => {
    const inactiveModsToEnable = installedMods.filter((mod) => mod.isActive === false);
    const inactiveCount = inactiveModsToEnable.length;
    if (inactiveCount === 0) {
      toast.info("No disabled mods to enable");
      return;
    }

    const toastId = "enable-all";
    toast.loading(`Enabling ${inactiveCount} mod(s)...`, { id: toastId });

    try {
      for (const mod of inactiveModsToEnable) {
        const downloadIds = mod.sourceDownloadIds || [];
        for (const dlId of downloadIds) {
          const dl = await getLocalDownload(Number(dlId));
          const paks = (dl.contents || []).filter((f: string) => f.toLowerCase().endsWith(".pak"));
          await setActivePaks(Number(dlId), paks);
        }
      }
      await scanActive();
      toast.success(`${inactiveCount} mod${inactiveCount !== 1 ? "s" : ""} enabled`, { id: toastId });
    } catch (error: any) {
      toast.error(error?.message || "Failed to enable mods", { id: toastId });
    } finally {
      void refreshMods({ includeConflicts: true });
    }
  };

  const refreshMods = async (
    options: { quiet?: boolean; includeConflicts?: boolean } = {},
  ) => {
    // Keep ref updated so event listener always calls the latest version
    refreshModsRef.current = () => refreshMods({ quiet: true });
    const { quiet = false, includeConflicts = false } = options;
    try {
      try {
        await scanActive();
      } catch (scanError) {
        console.warn("[App] scanActive during refresh failed", scanError);
      }
      if (includeConflicts) {
        await refreshConflicts();
      }
      const deduped = await fetchServerMods();
      if (!quiet) {
        toast.success(`Refreshed from DB: ${deduped.length} local downloads`);
      }
      setMods(deduped);
      void fetchCollectionsCount();
    } catch (e: any) {
      if (quiet) {
        console.error("Auto refresh failed", e);
      } else {
        toast.error(`Refresh failed: ${e?.message || e}`);
      }
    }
  };

  // -- Mod ID Assignment Handlers --
  const handleAssignModId = useCallback((modId: string) => {
    const mod = mods.find(m => m.id === modId);
    if (mod) {
      setAssignModIdTarget(mod);
    }
  }, [mods]);

  const handleAssignModIdSuccess = useCallback((_modId: string, _newNexusId: number) => {
    refreshMods({ quiet: true });
  }, [refreshMods]);

  const handleBootstrapTask = useCallback(async (): Promise<boolean> => {
    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        setTimeout(resolve, ms);
      });

    console.log("[Bootstrap] Starting bootstrap task...");
    setBootstrapRunning(true);
    setBootstrapJob(null);
    try {
      console.log("[Bootstrap] Calling runSettingsTask API...");
      const job = await runSettingsTask("bootstrap_rebuild");
      console.log("[Bootstrap] Initial job response:", job);
      setBootstrapJob(job);
      const terminalStatuses: Array<ApiSettingsTaskResponse["status"]> = [
        "succeeded",
        "failed",
      ];
      let currentJob = job;
      let delay = 400;
      let pollCount = 0;
      while (!terminalStatuses.includes(currentJob.status)) {
        pollCount++;
        console.log(
          `[Bootstrap] Polling ${pollCount}: status=${currentJob.status}, waiting ${delay}ms...`,
        );
        await sleep(delay);
        delay = Math.min(delay + 250, 2000);
        const latest = await getSettingsTaskJob(currentJob.id);
        console.log(
          `[Bootstrap] Poll ${pollCount} result:`,
          latest.status,
          "ok:",
          latest.ok,
        );
        currentJob = latest;
        setBootstrapJob(latest);
      }
      console.log("[Bootstrap] Final job state:", currentJob);
      setBootstrapJob(currentJob);
      const ok = currentJob.status === "succeeded" && Boolean(currentJob.ok);
      console.log("[Bootstrap] Task completed, ok:", ok);
      if (ok) {
        console.log("[Bootstrap] Success! Refreshing data...");
        toast.success("Initial database build completed");
        if (typeof window !== "undefined") {
          window.localStorage.setItem(GET_STARTED_STORAGE_KEY, "true");
        }
        await refreshMods({ quiet: false, includeConflicts: true });
        await fetchSettings(false);
        console.log("[Bootstrap] Data refresh complete");
      } else {
        const exitSuffix =
          typeof currentJob.exit_code === "number"
            ? ` (exit ${currentJob.exit_code})`
            : "";
        toast.error(`Initial database build failed${exitSuffix}`, {
          description:
            currentJob.error && currentJob.error.trim().length > 0
              ? currentJob.error
              : undefined,
        });
      }
      await fetchBootstrapStatus();
      console.log("[Bootstrap] Bootstrap status refreshed");
      return ok;
    } catch (err) {
      console.error("[Bootstrap] Error during bootstrap:", err);
      const message =
        err instanceof Error && err.message
          ? err.message
          : String(err ?? "Task failed");
      toast.error(`Failed to run initial build: ${message}`);
      return false;
    } finally {
      console.log("[Bootstrap] Setting bootstrapRunning to false");
      setBootstrapRunning(false);
    }
  }, [fetchBootstrapStatus, fetchSettings, refreshMods]);

  const handleRunSettingsTask = useCallback(
    async (task: SettingsTask) => {
      const sleep = (ms: number) =>
        new Promise<void>((resolve) => {
          setTimeout(resolve, ms);
        });

      setSettingsTaskJobs((prev) => {
        const next = { ...prev };
        delete next[task];
        return next;
      });
      setSettingsTaskBusy(task);
      try {
        const job = await runSettingsTask(task);
        setSettingsTaskJobs((prev) => ({ ...prev, [task]: job }));

        const terminalStatuses: Array<ApiSettingsTaskResponse["status"]> = [
          "succeeded",
          "failed",
        ];
        let currentJob = job;
        let delay = 400;
        while (!terminalStatuses.includes(currentJob.status)) {
          await sleep(delay);
          delay = Math.min(delay + 250, 2000);
          const latest = await getSettingsTaskJob(currentJob.id);
          currentJob = latest;
          setSettingsTaskJobs((prev) => ({ ...prev, [task]: latest }));
        }

        const finalJob = currentJob;
        setSettingsTaskJobs((prev) => ({ ...prev, [task]: finalJob }));

        const taskLabel = SETTINGS_TASK_LABELS[task] ?? task;
        if (finalJob.status === "succeeded" && finalJob.ok) {
          toast.success(`${taskLabel} completed`);
          await refreshMods({ quiet: true, includeConflicts: true });
        } else {
          const exitSuffix =
            typeof finalJob.exit_code === "number"
              ? ` (exit ${finalJob.exit_code})`
              : "";
          const description =
            finalJob.error && finalJob.error.trim().length > 0
              ? finalJob.error
              : undefined;
          toast.error(`${taskLabel} failed${exitSuffix}`, {
            description,
          });
        }
      } catch (err) {
        const message =
          err instanceof Error && err.message
            ? err.message
            : String(err ?? "Task failed");
        toast.error(`Failed to run task: ${message}`);
      } finally {
        setSettingsTaskBusy(null);
      }
    },
    [refreshMods],
  );

  const handleRefresh = () => {
    void refreshMods({ includeConflicts: true });
    void fetchCollectionsCount();
  };

  const handleDisableAllMods = async () => {
    try {
      await disableAllMods();
      toast.success("All mods have been disabled");
      handleRefresh();
    } catch (err) {
      toast.error(`Failed to disable all mods: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleModAdded = () =>
    refreshMods({ quiet: true, includeConflicts: true });

  // Callback to check if a handoff is being managed by the update flow
  // Checks by (mod_id, file_id) pair since we track updates before handoff appears
  const isHandoffManagedByUpdate = useCallback(
    (handoff: ApiNxmHandoffSummary) => {
      const modId = handoff.request?.mod_id;
      const fileId = handoff.request?.file_id;
      if (modId == null) return false;

      // Create key: "modId:fileId" or "modId:*" if no specific file
      const key = fileId != null ? `${modId}:${fileId}` : `${modId}:*`;
      return updateManagedPairsRef.current.has(key);
    },
    [],
  );

  // On mount, try to get mods from API (doesn't replace mock cards yet, just signals connectivity)
  useEffect(() => {
    if (!backendReady) {
      return;
    }
    (async () => {
      try {
        const deduped = await fetchServerMods();
        // Always reflect server state, even if empty (replaces mock data)
        setMods(deduped);
      } catch (e) {
        // ignore, stay on mock data
      }
    })();
  }, [backendReady]);

  // Initialize icons on app startup (only in Tauri environment)
  useEffect(() => {
    if (!backendReady) {
      return;
    }
    (async () => {
      try {
        await initializeIcons();
      } catch (error) {
        console.warn("Failed to initialize icons:", error);
      }
    })();
  }, [backendReady]);

  function extractMemberId(value: unknown): number | undefined {
    if (value == null) return undefined;
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (!trimmed) return undefined;
      if (/^\d+$/.test(trimmed)) {
        const direct = Number(trimmed);
        return Number.isFinite(direct) ? direct : undefined;
      }
      const match = trimmed.match(/(\d+)(?:\/?(?:\?.*)?)?$/);
      if (match) {
        const parsed = Number(match[1]);
        return Number.isFinite(parsed) ? parsed : undefined;
      }
    }
    return undefined;
  }

  function deriveAuthorAvatar(download: ApiDownload): string | undefined {
    if (download.mod_author_avatar_url) {
      if (typeof window !== "undefined") {
        console.debug("[avatar] using API-provided avatar", {
          downloadId: download.id,
          modId: download.mod_id,
          source: download.mod_author_avatar_url,
        });
      }
      return download.mod_author_avatar_url;
    }
    const memberId =
      extractMemberId(download.mod_author_member_id) ??
      extractMemberId(download.mod_author_profile_url);
    if (memberId !== undefined) {
      const fallback = `https://avatars.nexusmods.com/${memberId}/100`;
      if (typeof window !== "undefined") {
        console.debug("[avatar] derived Nexus avatar", {
          downloadId: download.id,
          modId: download.mod_id,
          memberId,
          fallback,
        });
      }
      return fallback;
    }
    if (typeof window !== "undefined") {
      console.warn("[avatar] unable to derive avatar", {
        downloadId: download.id,
        modId: download.mod_id,
        mod_author_member_id: download.mod_author_member_id,
        mod_author_profile_url: download.mod_author_profile_url,
      });
    }
    return undefined;
  }

  function toUiMod(d: ApiDownload, customImages: Record<number, string> = {}) {
    // Consolidate tags and remove any stray tokens like 'data' and generic categories for robustness
    const rawTags = (d.tags || []).filter(
      (t) => t && !["data"].includes(t.toLowerCase()),
    );
    // Merge in user-created custom tags so the search filter picks them up without extra fetches
    const customTagNames: string[] = Array.isArray((d as any).custom_tag_names)
      ? (d as any).custom_tag_names
      : [];
    const seen = new Set<string>(rawTags.map((t) => t.toLowerCase()));
    for (const ct of customTagNames) {
      if (ct && !seen.has(ct.toLowerCase())) {
        rawTags.push(ct);
        seen.add(ct.toLowerCase());
      }
    }
    const cleanTags = rawTags;
    const categoryTags = deriveCategoryTags(cleanTags);


    // Priority: Nexus picture_url > Custom image > Fallback
    let images: string[];
    if (d.picture_url) {
      let thumbUrl = d.picture_url;
      // Nexus staticdelivery supports a /thumbnails/ path which is much smaller (e.g. 385px vs 1920px)
      if (
        d.picture_url.includes("staticdelivery.nexusmods.com") &&
        d.picture_url.includes("/images/") &&
        !d.picture_url.includes("/thumbnails/")
      ) {
        thumbUrl = d.picture_url.replace("/images/", "/images/thumbnails/");
      }
      images = [thumbUrl];
    } else {
      // Try to get custom image
      let customImage: string | undefined;
      if (d.mod_id != null && customImages[d.mod_id]) {
        customImage = customImages[d.mod_id];
      } else if (d.mod_id == null && d.id != null) {
        // For local mods, use synthetic ID (negative download ID)
        const syntheticId = -d.id;
        customImage = customImages[syntheticId];
      }

      if (customImage) {
        images = [customImage];
      } else {
        images = [
          "https://i.pinimg.com/1200x/44/da/5e/44da5e6d9dd75cb753ab5925aff4ce4c.jpg",
        ];
      }
    }
    const installedVersion = d.version || undefined;
    const localVersionKey = d.local_version_key ?? null;
    const latestVersionKey = d.latest_version_key ?? null;
    const latestVersion = d.latest_version || installedVersion || d.version || "";
    const hasUpdate = Boolean(d.needs_update);
    const isActive = d.active_paks && d.active_paks.length > 0;
    const releaseDate = d.mod_created_time || null;
    const rawUpdatedAt = d.latest_uploaded_at || d.mod_updated_at || null;
    const hasUpdateTimestamp = Boolean(rawUpdatedAt);
    const installDate = d.created_at ?? null;
    const hasInstallDate = Boolean(installDate);
    const displayUpdatedAt = rawUpdatedAt ?? installDate ?? null;
    const authorMemberId =
      extractMemberId(d.mod_author_member_id) ??
      extractMemberId(d.mod_author_profile_url);
    const authorProfileUrl = d.mod_author_profile_url || undefined;
    const authorAvatar = deriveAuthorAvatar(d);
    return {
      id: d.mod_id != null ? String(d.mod_id) : String(d.id),
      backendModId: d.mod_id,
      sourceDownloadIds: d.source_download_ids || [d.id],
      sourceFileIds: d.source_file_ids || (d.latest_file_id ? [d.latest_file_id] : []),
      sourcePaths: d.source_paths || (d.path ? [d.path] : []),
      name: d.mod_name || d.name,
      description: "",
      author: d.mod_author || "",
      authorAvatar,
      authorMemberId,
      authorProfileUrl,
      category: categoryTags[0] || inferCategoryFromTags(cleanTags) || "",
      categoryTags,
      character: inferCharacterFromTags(cleanTags),
      tags: cleanTags,
      downloads: (d.mod_downloads as number | null) ?? 0,
      rating: d.endorsement_count != null ? d.endorsement_count : 0,
      images,
      version: installedVersion || "",
      lastUpdated: displayUpdatedAt ?? "",
      lastUpdatedRaw: rawUpdatedAt,
      releaseDate,
      isInstalled: true,
      isFavorited: false,
      hasUpdate,
      installedVersion,
      latestVersion: latestVersion,
      latestVersionKey,
      localVersionKey,
      latestUploadedAt: d.latest_uploaded_at ?? null,
      latestFileId: d.latest_file_id ?? null,
      latestFileName: d.latest_file_name ?? null,
      installDate,
      hasInstallDate,
      hasUpdateTimestamp,
      isActive,
      defaultActivePaks: d.active_paks || [],
      contents: d.contents || [],
      performanceImpact: undefined,
      needsUpdate: hasUpdate,
      updateVariantName: (d as any).updateVariantName ?? null,
      updateVariantLocalVersion: (d as any).updateVariantLocalVersion ?? null,
      updateVariantLatestVersion: (d as any).updateVariantLatestVersion ?? null,
      isUpdating: false,
      updateError: null,
      containsAdultContent: Boolean(d.contains_adult_content),
      needsManualModId: Boolean(d.needs_manual_mod_id),
      renameStatus: d.rename_status as "idle" | "verifying" | "renamed" | "failed" | undefined,
      renameError: d.rename_error ?? null,
      customAuthorName: (d as any).custom_author_name ?? null,
      customAuthorType: (d as any).custom_author_type ?? null,
      customAuthorId: (d as any).custom_author_id ?? null,
      customAuthorAvatar: (() => {
        const base64 = (d as any).custom_author_avatar ?? null;
        if (base64) return base64;
        // For Nexus-type custom authors, synthesize avatar URL from member ID
        const nexusMemberId = (d as any).custom_nexus_member_id ?? null;
        if (nexusMemberId) return `https://avatars.nexusmods.com/${nexusMemberId}/100`;
        return null;
      })(),
      modKey: d.mod_id != null ? `mod:${d.mod_id}` : `local:${d.id}`,
    } as any;
  }

  /**
   * Remove duplicate downloads with the same mod+version.
   * When duplicates exist, keeps only the latest one (by created_at).
   */
  function deduplicateDownloads(downloads: ApiDownload[]): ApiDownload[] {
    // Group by mod_id (for Nexus mods) or by name (for local mods)
    const byKey = new Map<string, ApiDownload[]>();

    for (const download of downloads) {
      const modKey =
        download.mod_id != null
          ? `mod:${download.mod_id}`
          : `name:${(download.mod_name || download.name || "").toLowerCase().trim()}`;

      if (!byKey.has(modKey)) {
        byKey.set(modKey, []);
      }
      byKey.get(modKey)!.push(download);
    }

    const deduplicated: ApiDownload[] = [];

    // For each group, check for version duplicates
    for (const group of byKey.values()) {
      // Group by version AND name within this mod to preserve variants
      // Different variants (e.g., different skins) may share the same version
      // but have different file names - treat these as distinct entries
      const byVersionAndName = new Map<string, ApiDownload[]>();

      for (const download of group) {
        // Include file name in the key to distinguish variants with same version
        const versionKey = `${(download.version || "").trim().toLowerCase()}::${(download.name || "").trim().toLowerCase()}`;
        if (!byVersionAndName.has(versionKey)) {
          byVersionAndName.set(versionKey, []);
        }
        byVersionAndName.get(versionKey)!.push(download);
      }

      // For each version+name combo, keep only the latest download
      for (const versionGroup of byVersionAndName.values()) {
        if (versionGroup.length === 1) {
          // No duplicates for this version+name combo
          deduplicated.push(versionGroup[0]);
        } else {
          // Multiple downloads with same mod+version+name - keep the latest
          const sorted = versionGroup.sort((a, b) => {
            const dateA = new Date(a.created_at || 0).getTime();
            const dateB = new Date(b.created_at || 0).getTime();
            return dateB - dateA; // Descending - latest first
          });

          // Keep the latest
          deduplicated.push(sorted[0]);

          // Log duplicates being filtered out
          if (sorted.length > 1) {
            console.info(
              `[Dedup] Found ${sorted.length} duplicates of "${sorted[0].mod_name || sorted[0].name}" v${sorted[0].version}. Keeping latest (id=${sorted[0].id}), filtering out:`,
              sorted.slice(1).map((d) => `id=${d.id}`),
            );
          }
        }
      }
    }

    return deduplicated;
  }

  function groupDownloadsByMod(downloads: ApiDownload[]): ApiDownload[] {
    // STEP 1: Remove duplicate downloads (same mod+version) BEFORE grouping
    // Keep only the latest download when duplicates exist
    const deduplicated = deduplicateDownloads(downloads);

    const out: ApiDownload[] = [];
    const byMod = new Map<number, ApiDownload>();
    const byName = new Map<string, ApiDownload>();



    const mergeMetadata = (target: ApiDownload, incoming: ApiDownload) => {
      if (!target.latest_version && incoming.latest_version)
        target.latest_version = incoming.latest_version;
      if (!target.latest_version_key && incoming.latest_version_key)
        target.latest_version_key = incoming.latest_version_key;
      if (!target.latest_uploaded_at && incoming.latest_uploaded_at)
        target.latest_uploaded_at = incoming.latest_uploaded_at;
      if (target.latest_version_key && incoming.latest_version_key) {
        if (incoming.latest_version_key > target.latest_version_key) {
          target.latest_version_key = incoming.latest_version_key;
          if (incoming.latest_version)
            target.latest_version = incoming.latest_version;
          if (incoming.latest_uploaded_at)
            target.latest_uploaded_at = incoming.latest_uploaded_at;
          if (incoming.latest_file_id != null)
            target.latest_file_id = incoming.latest_file_id;
          if (incoming.latest_file_name)
            target.latest_file_name = incoming.latest_file_name;
        }
      } else {
        if (incoming.latest_file_id != null && target.latest_file_id == null)
          target.latest_file_id = incoming.latest_file_id;
        if (!target.latest_file_name && incoming.latest_file_name)
          target.latest_file_name = incoming.latest_file_name;
      }

      // Track whether incoming has a higher local version key (i.e., it's the "newer" variant)
      const incomingWinsVersion =
        incoming.local_version_key &&
        (!target.local_version_key || incoming.local_version_key > target.local_version_key);

      if (incomingWinsVersion) {
        target.local_version_key = incoming.local_version_key!;
        if (incoming.version) target.version = incoming.version;
        if (incoming.created_at) target.created_at = incoming.created_at;
      }
      // We handle needs_update recalculation at the end using local_variants.
    };

    for (const d of deduplicated) {
      if (d.mod_id == null) {
        const key = (d.mod_name || d.name || "").toLowerCase().trim();
        if (!key) {
          out.push({
            ...d,
            source_download_ids: [d.id],
            source_file_ids: d.latest_file_id ? [d.latest_file_id] : [],
            source_paths: d.path ? [d.path] : [],
            contents: [...(d.contents || [])],
            active_paks: [...(d.active_paks || [])],
            tags: [...(d.tags || [])],
            local_version_key: d.local_version_key ?? null,
            latest_version: d.latest_version ?? null,
            latest_version_key: d.latest_version_key ?? null,
            latest_uploaded_at: d.latest_uploaded_at ?? null,
            latest_file_id: d.latest_file_id ?? null,
            latest_file_name: d.latest_file_name ?? null,
            needs_update: Boolean(d.needs_update),
          });
          continue;
        }
        const prev = byName.get(key);
        if (!prev) {
          let initialNeedsUpdate = Boolean(d.needs_update);
          byName.set(key, {
            ...d,
            contents: [...(d.contents || [])],
            active_paks: [...(d.active_paks || [])],
            tags: [...(d.tags || [])],
            source_download_ids: [d.id],
            source_file_ids: d.latest_file_id ? [d.latest_file_id] : [],
            source_paths: d.path ? [d.path] : [],
            local_version_key: d.local_version_key ?? null,
            latest_version: d.latest_version ?? null,
            latest_version_key: d.latest_version_key ?? null,
            latest_uploaded_at: d.latest_uploaded_at ?? null,
            latest_file_id: d.latest_file_id ?? null,
            latest_file_name: d.latest_file_name ?? null,
            needs_update: initialNeedsUpdate,
            local_variants: [d],
          } as any);
          continue;
        }
        // merge into prev by name
        const merged = prev;
        merged.mod_name =
          merged.mod_name || d.mod_name || merged.name || d.name;
        merged.name = merged.mod_name || merged.name || d.name;
        if (!merged.picture_url) merged.picture_url = d.picture_url;
        if (!merged.mod_author) merged.mod_author = d.mod_author;
        if (
          merged.mod_author_member_id == null &&
          d.mod_author_member_id != null
        )
          merged.mod_author_member_id = d.mod_author_member_id;
        if (!merged.mod_author_profile_url && d.mod_author_profile_url)
          merged.mod_author_profile_url = d.mod_author_profile_url;
        if (!merged.mod_author_avatar_url && d.mod_author_avatar_url)
          merged.mod_author_avatar_url = d.mod_author_avatar_url;
        const cset = new Set<string>(merged.contents || []);
        (d.contents || []).forEach((c) => c && cset.add(c));
        merged.contents = Array.from(cset);
        const aset = new Set<string>(merged.active_paks || []);
        (d.active_paks || []).forEach((a) => a && aset.add(a));
        merged.active_paks = Array.from(aset);
        const tset = new Set<string>(merged.tags || []);
        (d.tags || []).forEach((t) => t && tset.add(t));
        merged.tags = Array.from(tset).sort();
        merged.source_download_ids = [
          ...new Set([...(merged.source_download_ids || []), d.id]),
        ];
        merged.source_file_ids = [
          ...new Set([
            ...(merged.source_file_ids || []),
            ...(d.latest_file_id ? [d.latest_file_id] : []),
          ]),
        ];
        merged.source_paths = [
          ...new Set([...(merged.source_paths || []), ...(d.path ? [d.path] : [])]),
        ];
        if (
          new Date(d.created_at).getTime() >
          new Date(merged.created_at).getTime()
        ) {
          merged.created_at = d.created_at;
          merged.version = d.version;
        }
        if (merged.mod_downloads == null && d.mod_downloads != null)
          merged.mod_downloads = d.mod_downloads;
        if (merged.endorsement_count == null && d.endorsement_count != null)
          merged.endorsement_count = d.endorsement_count;
        mergeMetadata(merged, d);
        (merged as any).local_variants.push(d);
        continue;
      }
      const prev = byMod.get(d.mod_id);
      if (!prev) {
        let initialNeedsUpdate = Boolean(d.needs_update);
        byMod.set(d.mod_id, {
          ...d,
          contents: [...(d.contents || [])],
          active_paks: [...(d.active_paks || [])],
          tags: [...(d.tags || [])],
          source_download_ids: [d.id],
          source_file_ids: d.latest_file_id ? [d.latest_file_id] : [],
          source_paths: d.path ? [d.path] : [],
          local_version_key: d.local_version_key ?? null,
          latest_version: d.latest_version ?? null,
          latest_version_key: d.latest_version_key ?? null,
          latest_uploaded_at: d.latest_uploaded_at ?? null,
          latest_file_id: d.latest_file_id ?? null,
          latest_file_name: d.latest_file_name ?? null,
          needs_update: initialNeedsUpdate,
          local_variants: [d],
        } as any);
        continue;
      }
      // merge into prev
      const merged = prev;
      // prefer mod_name, but keep something displayable
      merged.mod_name = merged.mod_name || d.mod_name || merged.name || d.name;
      merged.name = merged.mod_name || merged.name || d.name;
      if (!merged.picture_url) merged.picture_url = d.picture_url;
      if (!merged.mod_author) merged.mod_author = d.mod_author;
      if (merged.mod_author_member_id == null && d.mod_author_member_id != null)
        merged.mod_author_member_id = d.mod_author_member_id;
      if (!merged.mod_author_profile_url && d.mod_author_profile_url)
        merged.mod_author_profile_url = d.mod_author_profile_url;
      if (!merged.mod_author_avatar_url && d.mod_author_avatar_url)
        merged.mod_author_avatar_url = d.mod_author_avatar_url;
      const cset = new Set<string>(merged.contents || []);
      (d.contents || []).forEach((c) => c && cset.add(c));
      merged.contents = Array.from(cset);
      const aset = new Set<string>(merged.active_paks || []);
      (d.active_paks || []).forEach((a) => a && aset.add(a));
      merged.active_paks = Array.from(aset);
      const tset = new Set<string>(merged.tags || []);
      (d.tags || []).forEach((t) => t && tset.add(t));
      merged.tags = Array.from(tset).sort();
      merged.source_download_ids = [
        ...new Set([...(merged.source_download_ids || []), d.id]),
      ];
      merged.source_file_ids = [
        ...new Set([
          ...(merged.source_file_ids || []),
          ...(d.latest_file_id ? [d.latest_file_id] : []),
        ]),
      ];
      merged.source_paths = [
        ...new Set([...(merged.source_paths || []), ...(d.path ? [d.path] : [])]),
      ];
      // latest timestamp wins for date/version
      if (
        new Date(d.created_at).getTime() > new Date(merged.created_at).getTime()
      ) {
        merged.created_at = d.created_at;
        merged.version = d.version;
      }
      if (merged.mod_downloads == null && d.mod_downloads != null)
        merged.mod_downloads = d.mod_downloads;
      if (merged.endorsement_count == null && d.endorsement_count != null)
        merged.endorsement_count = d.endorsement_count;
      mergeMetadata(merged, d);
      (merged as any).local_variants.push(d);
    }

    byMod.forEach((v) => out.push(v));
    byName.forEach((v) => out.push(v));

    // Final pass: Re-calculate needs_update for all grouped mods based on full variant knowledge
    for (const merged of out) {
      const variants = (merged as any).local_variants || [merged];
      let hasRealUpdate = false;
      for (const variant of variants) {
        if (variant.needs_update) {
          hasRealUpdate = true;
          (merged as any).updateVariantName = variant.name || variant.mod_name || "";
          (merged as any).updateVariantLocalVersion = variant.version || "";
          (merged as any).updateVariantLatestVersion = variant.latest_version || "";
          break;
        }
      }
      merged.needs_update = hasRealUpdate;
    }

    return out;
  }

  function dedupeById<T extends { id: string }>(arr: T[]): T[] {
    const seen = new Set<string>();
    const dups = new Set<string>();
    const out: T[] = [];
    for (const m of arr) {
      const k = String(m.id);
      if (seen.has(k)) {
        dups.add(k);
        continue;
      }
      seen.add(k);
      out.push(m);
    }
    if (dups.size > 0) {
      // Helpful during development; safe in production consoles too
      console.warn("Deduped duplicate mod ids:", Array.from(dups));
    }
    return out;
  }

  function inferCategoryFromTags(tags: string[]): string {
    const derived = deriveCategoryTags(tags);
    if (derived.length > 0) return derived[0];
    // if any tag resembles a character name token (not a category), treat as characters
    if (tags.some((t) => t && !CATEGORY_KEYWORD_SET.has(t.toLowerCase())))
      return "characters";
    return ""; // Return empty string when no meaningful tags can be generated
  }

  function inferCharacterFromTags(tags: string[]): string | undefined {
    // Heuristic: if tags contain words beyond category set, pick the first as character label
    const candidate = tags.find(
      (t) => t && !CATEGORY_KEYWORD_SET.has(t.toLowerCase()),
    );
    return candidate;
  }

  // Character/Skin Toggle Handler
  // When a skin is clicked, both the character and skin tags are added to the filter.
  // The filter logic uses .every() to ensure ALL selected tags must be present.
  // Example: Clicking "default" under "emma frost" adds both to selectedCharacters,
  // so only mods with BOTH "emma frost" AND "default" will show.
  const handleCharacterToggle = (character: string) => {
    setSelectedCharacters((prev) =>
      prev.includes(character)
        ? prev.filter((c) => c !== character)
        : [...prev, character],
    );
  };

  const handleCustomTagToggle = (tag: string) => {
    setSelectedCustomTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  };

  const handleCategoryChange = (category: string) => {
    setSelectedCategory(category);
    // Clear character filters when switching away from characters
    if (category !== "characters") {
      setSelectedCharacters([]);
    }
  };

  return (
    <NSFWFilterProvider>
      <ThemeProvider defaultTheme="dark">
        <div className="relative h-screen bg-background flex flex-col">
          {/* Header - AppHeader UI migrated into TabHeader (see TabHeader props below) */}

          {/* Main Content */}
          <div className="flex-1 overflow-hidden flex">
            {/* Left Sidebar - Always the same */}
            <DownloadsSidebar
              selectedCategory={selectedCategory}
              onCategoryChange={handleCategoryChange}
              installedCounts={installedCounts}
              updatesCount={updatesCount}
              selectedCharacters={selectedCharacters}
              onCharacterToggle={handleCharacterToggle}
              selectedCustomTags={selectedCustomTags}
              onCustomTagToggle={handleCustomTagToggle}
              mods={mods}
              conflictsReloadToken={conflictsReloadToken}
              onRefreshMods={handleRefresh}
              onUpdateMod={handleUpdate}
            />

            {/* Main Content Area */}
            <div className="flex-1 flex flex-col">
              {/* Tab Header */}
              <TabHeader
                activeTab={activeTab}
                onTabChange={setActiveTab}
                downloadsCount={installedMods.length}
                activeCount={activeMods.length}
                collectionsCount={collectionsCount}
                updatesCount={updatesCount}
                activeModsCount={activeMods.length}
                onRefresh={handleRefresh}
                onOpenSettings={handleOpenSettings}
                onOpenBootstrap={handleOpenBootstrap}
                onOpenBackup={() => setBackupOpen(true)}
                onDisableAllMods={handleDisableAllMods}
                hasLastCrash={crashInfo !== null}
                onViewLastCrash={() => setCrashDetectorOpen(true)}
              />

              {/* Tab Content */}
              <div className="flex-1 overflow-hidden">
                {activeTab === "downloads" ? (
                  <DownloadsPage
                    mods={mods}
                    onUpdate={handleUpdate}
                    onCheckUpdate={handleCheckUpdate}
                    onUninstall={handleUninstall}
                    onFavorite={handleFavorite}
                    selectedCategory={selectedCategory}
                    selectedCharacters={selectedCharacters}
                    selectedCustomTags={selectedCustomTags}
                    onModAdded={handleModAdded}
                    onConflictStateChanged={notifyConflictsDirty}
                    viewMode={viewMode}
                    onViewModeChange={setViewMode}
                    onRefresh={handleRefresh}
                    onAssignModId={handleAssignModId}
                  />
                ) : activeTab === "active" ? (
                  <ActiveModsView
                    mods={mods}
                    onToggleMod={handleToggleMod}
                    onDisableAll={handleDisableAll}
                    onEnableAll={handleEnableAll}
                    onUpdate={handleUpdate}
                    onCheckUpdate={handleCheckUpdate}
                    onUninstall={handleUninstall}
                    onFavorite={handleFavorite}
                    selectedCategory={selectedCategory}
                    selectedCharacters={selectedCharacters}
                    selectedCustomTags={selectedCustomTags}
                    onConflictStateChanged={notifyConflictsDirty}
                    viewMode={viewMode}
                    onViewModeChange={setViewMode}
                    onRefresh={handleRefresh}
                    onAssignModId={handleAssignModId}
                  />
                ) : (
                  <CollectionsPage
                    installedMods={mods}
                    onFavorite={handleFavorite}
                    viewMode={viewMode}
                    onViewModeChange={setViewMode}
                    onToggleMod={handleToggleMod}
                    onRefreshMods={() => refreshMods({ includeConflicts: true })}
                    onCollectionsCountChange={setCollectionsCount}
                    backupsRefreshTrigger={backupsRefreshTrigger}
                  />
                )}
              </div>
            </div>
          </div>

          <GetStartedDialog
            open={getStartedOpen}
            loadingSettings={settingsLoading}
            savingSettings={settingsSaving}
            settings={settingsData}
            bootstrapStatus={bootstrapStatus}
            job={bootstrapJob}
            jobRunning={bootstrapRunning}
            onOpenChange={(isOpen) => {
              const canDismiss =
                !bootstrapRunning ||
                !!(
                  bootstrapJob &&
                  bootstrapJob.status === "succeeded" &&
                  bootstrapJob.ok
                );
              if (!isOpen && !canDismiss) {
                return;
              }
              setGetStartedOpen(isOpen);
              if (isOpen) {
                if (!settingsLoading && settingsData == null) {
                  void fetchSettings(false);
                }
                void fetchBootstrapStatus();
              }
            }}
            onSubmit={saveSettings}
            onRunBootstrap={handleBootstrapTask}
            onRefreshSettings={handleSettingsRefresh}
            onRefreshStatus={() => {
              void fetchBootstrapStatus();
            }}
          />

          {/* Toast Notifications */}
          <SettingsDialog
            open={settingsOpen}
            loading={settingsLoading}
            saving={settingsSaving}
            settings={settingsData}
            taskBusy={settingsTaskBusy}
            taskJobs={settingsTaskJobs}
            onOpenChange={handleSettingsOpenChange}
            onRefresh={handleSettingsRefresh}
            onSubmit={handleSettingsSubmit}
            onRunTask={handleRunSettingsTask}
          />
          <GameUpdateModal
            open={gameUpdateModalOpen}
            phase={gameUpdatePhase}
            steps={gameUpdateSteps}
            latestFile={gameUpdateLatestFile}
            newCharacters={gameUpdateNewCharacters}
            newSkins={gameUpdateNewSkins}
            onDismiss={() => {
              setGameUpdateModalOpen(false);
              setGameUpdateNewCharacters([]);
              setGameUpdateNewSkins([]);
            }}
          />
          <ServerStartupOverlay
            visible={!backendReady}
            lastError={backendStatus.lastError}
          />
          <Toaster />
          <NxmBackgroundListener
            enabled={backendReady}
            onModAdded={handleModAdded}
            isHandoffExcluded={isHandoffManagedByUpdate}
          />
          <BackupModal
            open={backupOpen}
            onClose={() => setBackupOpen(false)}
            mods={mods}
            onToggleMod={handleToggleMod}
            onBackupCreated={() => setBackupsRefreshTrigger((t) => t + 1)}
            onBackupRestored={() => refreshMods({ quiet: true, includeConflicts: true })}
          />
          <AssignModIdModal
            open={!!assignModIdTarget}
            onOpenChange={(open) => {
              if (!open) setAssignModIdTarget(null);
            }}
            mod={assignModIdTarget}
            onSuccess={handleAssignModIdSuccess}
          />
          <CrashDetectorModal
            open={crashDetectorOpen}
            crashInfo={crashInfo}
            allDownloads={crashAllDownloads}
            pakAssets={crashPakAssets}
            onDismiss={() => setCrashDetectorOpen(false)}
            onDeactivated={() => {
              setCrashDetectorOpen(false);
              setCrashInfo(null);
              void refreshMods({ quiet: true, includeConflicts: true });
            }}
          />
        </div>
      </ThemeProvider>
    </NSFWFilterProvider>
  );
}
