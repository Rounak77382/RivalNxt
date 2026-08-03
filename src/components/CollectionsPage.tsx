import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { SearchHeader } from "./SearchHeader";
import {
  Download,
  FolderOpen,
  Search,
  Calendar,
  Users,
  ChevronDown,
  Power,
  Archive,
  RotateCcw,
  Trash2,
  Clock,
} from "lucide-react";
import { openInBrowser } from "../lib/tauri-utils";
import { ModCard } from "./ModCard";
import { VirtualizedModList, useGridColumns } from "./VirtualizedModList";
import type { Mod } from "./ModCard";
import { toast } from "sonner";
import { LazyModModal as ModModal } from "./LazyModModal";
import { BackupRestoreModal } from "./BackupRestoreModal";
import {
  loadBackupMetas,
  removeBackupMeta,
  type BackupMeta,
} from "../lib/backupUtils";
import {
  setActivePaks,
  scanActive,
  refreshConflicts,
  getLocalDownload,
  listCollections,
  getCollection,
  deleteCollection,
  ApiCollection,
  ApiCollectionModFile,
  ApiNxmHandoffSummary,
  listNxmHandoffs,
  listDownloads,
} from "../lib/api";
import { anyHandoffInFlight, nextPollDelay } from "../lib/pollingHelpers";
import {
  pruneRecentlyCompleted,
  useAdaptivePoll,
  useGatedInterval,
} from "../lib/intervalHelpers";

const normalizeFilename = (filename: string): string => {
  if (!filename) return "";
  let base = filename.split(/[/\\]/).pop() || filename;
  base = base.toLowerCase().replace(/\.[a-z0-9]+$/i, "").trim();
  // Strip trailing duplicate/copy suffixes (e.g., "-7", "_1", " (2)")
  base = base.replace(/-[0-9]{1,3}$/g, "");
  base = base.replace(/_[0-9]{1,3}$/g, "");
  base = base.replace(/\s*\([0-9]{1,3}\)$/g, "");
  return base.replace(/[^a-z0-9]/g, "");
};

const idsEqual = (id1: any, id2: any): boolean => {
  if (id1 == null || id2 == null) return false;
  return String(id1).trim() === String(id2).trim();
};

interface InstalledModsIndex {
  fileIdToMod: Map<string, any>;
  backendModIdToMod: Map<string, any>;
  normPathToMod: Map<string, any>;
  sourceFileIdsSet: Set<string>;
  modIdVersionMap: Map<string, Set<string>>;
  normalizedPathsSet: Set<string>;
}

const isCollectionModDownloadedIndexed = (
  v: { file_id: number; mod_id: number | null; version: string; file_uri: string },
  index: InstalledModsIndex,
  recentlyCompletedFileIds?: Map<string, number>
): boolean => {
  if (recentlyCompletedFileIds && recentlyCompletedFileIds.has(String(v.file_id).trim())) {
    return true;
  }
  
  const fileIdStr = String(v.file_id).trim();
  if (index.sourceFileIdsSet.has(fileIdStr)) {
    return true;
  }

  if (v.mod_id != null) {
    const modIdStr = String(v.mod_id).trim();
    const versionSet = index.modIdVersionMap.get(modIdStr);
    if (versionSet && v.version && versionSet.has(String(v.version).trim())) {
      return true;
    }
  }

  const normFileUri = normalizeFilename(v.file_uri);
  if (normFileUri && index.normalizedPathsSet.has(normFileUri)) {
    return true;
  }

  return false;
};


interface CollectionsPageProps {
  installedMods: Mod[];
  onFavorite: (modId: string) => void;
  onToggleMod: (modId: string) => void;
  onRefreshMods?: () => void;
  viewMode: "grid" | "list";
  onViewModeChange: (mode: "grid" | "list") => void;
  onCollectionsCountChange?: (count: number) => void;
  backupsRefreshTrigger?: number;
}

export function CollectionsPage({
  installedMods,
  onFavorite,
  onToggleMod,
  onRefreshMods,
  viewMode,
  onViewModeChange,
  onCollectionsCountChange,
  backupsRefreshTrigger,
}: CollectionsPageProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<"name" | "date">("name");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");

  // Each collection's expanded body is a separate list inside the page's own
  // scroll container. Note the accordion wrapper already has overflow-hidden,
  // so hover-scale was clipped there before virtualization too -- this changes
  // nothing about that.
  const scrollRef = useRef<HTMLDivElement>(null);
  const gridColumns = useGridColumns(viewMode);


  const [selectedMod, setSelectedMod] = useState<Mod | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalInitialTab, setModalInitialTab] = useState<
    "overview" | "files" | "changelog" | "images" | "assets"
  >("overview");

  // Backups
  const [backupMetas, setBackupMetas] = useState<BackupMeta[]>(() =>
    loadBackupMetas()
  );
  const [backupsExpanded, setBackupsExpanded] = useState(false);
  const [restoringBackup, setRestoringBackup] = useState<BackupMeta | null>(
    null
  );

  // Collections state
  const [collectionsData, setCollectionsData] = useState<ApiCollection[]>([]);
  const [expandedCollections, setExpandedCollections] = useState<Record<number, boolean>>({});
  const [hoveredEnableIds, setHoveredEnableIds] = useState<Record<number, boolean>>({});
  const [downloadingCollectionIds, setDownloadingCollectionIds] = useState<Record<number, boolean>>({});

  // Active handoffs polling
  const [activeHandoffs, setActiveHandoffs] = useState<any[]>([]);
  const [recentlyCompletedFileIds, setRecentlyCompletedFileIds] = useState<Map<string, number>>(new Map());
  const [failedFileIds, setFailedFileIds] = useState<Map<string, string>>(new Map());
  const prevHandoffsRef = useRef<any[]>([]);

  const installedModsIndex = useMemo(() => {
    const fileIdToMod = new Map<string, any>();
    const backendModIdToMod = new Map<string, any>();
    const normPathToMod = new Map<string, any>();

    const sourceFileIdsSet = new Set<string>();
    const modIdVersionMap = new Map<string, Set<string>>();
    const normalizedPathsSet = new Set<string>();

    (installedMods || []).forEach(m => {
      if (m.backendModId != null) {
        backendModIdToMod.set(String(m.backendModId).trim(), m);
      }
      
      const fileIds = [...(m.sourceDownloadIds || []), ...(m.sourceFileIds || [])];
      if (m.latestFileId != null) {
        fileIds.push(m.latestFileId);
      }
      fileIds.forEach(id => {
        if (id != null) {
          const idStr = String(id).trim();
          fileIdToMod.set(idStr, m);
          sourceFileIdsSet.add(idStr);
        }
      });

      const normPaths = (m.sourcePaths || []).map(path => normalizeFilename(path));
      normPaths.forEach(normPath => {
        if (normPath) {
          normPathToMod.set(normPath, m);
          normalizedPathsSet.add(normPath);
        }
      });

      if (m.backendModId != null) {
        const modIdStr = String(m.backendModId).trim();
        if (!modIdVersionMap.has(modIdStr)) {
          modIdVersionMap.set(modIdStr, new Set());
        }
        const versionSet = modIdVersionMap.get(modIdStr)!;
        if (m.installedVersion) versionSet.add(String(m.installedVersion).trim());
        if (m.version) versionSet.add(String(m.version).trim());
      }
    });

    return {
      fileIdToMod,
      backendModIdToMod,
      normPathToMod,
      sourceFileIdsSet,
      modIdVersionMap,
      normalizedPathsSet
    };
  }, [installedMods]);

  useEffect(() => {
    const prev = prevHandoffsRef.current;
    const currentIds = new Set(activeHandoffs.map(h => h.id));
    
    const vanished = prev.filter(h => !currentIds.has(h.id));
    if (vanished.length > 0) {
      setRecentlyCompletedFileIds(prevMap => {
        const nextMap = new Map(prevMap);
        const now = Date.now();
        vanished.forEach(h => {
          const fileId = h.request?.file_id;
          if (fileId != null) {
            nextMap.set(String(fileId).trim(), now);
          }
        });
        return nextMap;
      });
    }
    
    prevHandoffsRef.current = activeHandoffs;
  }, [activeHandoffs]);

  // Prune the "recently completed" flags.
  //
  // Was a flat setInterval(…, 2000) with [installedModsIndex] as its dependency:
  // it woke every 2s for the whole life of the page even when the map was empty,
  // and every change to the installed mod list tore the timer down and restarted
  // it. Now it only runs while there is something to prune, and the callback lives
  // in a ref so changing installedModsIndex no longer resubscribes.
  const hasRecentlyCompleted = recentlyCompletedFileIds.size > 0;
  const installedIndexRef = useRef(installedModsIndex);
  installedIndexRef.current = installedModsIndex;

  useGatedInterval(
    () => {
      const now = Date.now();
      setRecentlyCompletedFileIds((prevMap) =>
        pruneRecentlyCompleted(prevMap, now, (fileId) =>
          installedIndexRef.current.sourceFileIdsSet.has(fileId),
        ),
      );
    },
    hasRecentlyCompleted ? 2000 : null,
  );

  // Adaptive handoff polling. Previously a flat setInterval(…, 1000) with an
  // empty dep array, so it hammered the backend once a second for as long as
  // this page was mounted -- even with nothing downloading. Now it polls fast
  // only while a handoff is in flight and backs off to IDLE_POLL_MS otherwise.
  // Uses a self-rescheduling timeout so the cadence can change between ticks.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const tick = async () => {
      let handoffs: ApiNxmHandoffSummary[] = [];
      try {
        handoffs = await listNxmHandoffs();
        if (cancelled) return;
        setActiveHandoffs(handoffs);
      } catch (err) {
        console.error("Failed to fetch handoffs in CollectionsPage:", err);
      }
      if (cancelled) return;
      timer = setTimeout(tick, nextPollDelay(handoffs));
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    setFailedFileIds(prev => {
      let changed = false;
      const next = new Map(prev);

      activeHandoffs.forEach(h => {
        const fileId = h.request?.file_id;
        if (fileId != null) {
          const fileIdStr = String(fileId).trim();
          const isFailed = h.progress?.stage === "failed" || h.progress?.error != null;
          
          if (isFailed) {
            // Don't mark as failed if the mod is already recognized as downloaded.
            // A "duplicate download detected" failure means the file IS present — treat
            // it as downloaded rather than as a real failure.
            if (installedModsIndex.sourceFileIdsSet.has(fileIdStr)) {
              // Auto-remove stale failure for this file_id
              if (next.has(fileIdStr)) {
                next.delete(fileIdStr);
                changed = true;
              }
            } else {
              const errorMsg = h.progress?.error || h.progress?.message || "Download failed";
              if (next.get(fileIdStr) !== errorMsg) {
                next.set(fileIdStr, errorMsg);
                changed = true;
              }
            }
          } else {
            if (next.has(fileIdStr)) {
              next.delete(fileIdStr);
              changed = true;
            }
          }
        }
      });

      return changed ? next : prev;
    });
  }, [activeHandoffs, installedModsIndex]);


  useEffect(() => {
    setFailedFileIds(prev => {
      let changed = false;
      const next = new Map(prev);

      for (const fileId of prev.keys()) {
        const isDownloaded = isCollectionModDownloadedIndexed(
          { file_id: Number(fileId), mod_id: null, version: "", file_uri: "" },
          installedModsIndex,
          recentlyCompletedFileIds
        );
        if (isDownloaded) {
          next.delete(fileId);
          changed = true;
        }
      }

      return changed ? next : prev;
    });
  }, [installedModsIndex, recentlyCompletedFileIds]);

  useEffect(() => {
    if (onCollectionsCountChange) {
      onCollectionsCountChange(collectionsData.length);
    }
  }, [collectionsData, onCollectionsCountChange]);

  useEffect(() => {
    if (backupsRefreshTrigger !== undefined) {
      setBackupMetas(loadBackupMetas());
      fetchCollections(true);
    }
  }, [backupsRefreshTrigger]);

  // The fast cadence is only justified while a download is actually running.
  const hasInFlightDownloads = useCallback(
    () => anyHandoffInFlight(activeHandoffs),
    [activeHandoffs],
  );

  // Collections refresh.
  //
  // Was setInterval(…, 4000) with [] deps: every 4s, forever, it ran
  // listCollections() and then getCollection() for EVERY collection — an N+1
  // request burst — regardless of whether the window was even visible.
  //
  // Now: fast only while a download is in flight, slow otherwise, and fully
  // paused while the tab is hidden (with an immediate refresh on return).
  useAdaptivePoll(
    () => fetchCollections(true),
    {
      activeMs: 4000,
      idleMs: 30_000,
      isActive: hasInFlightDownloads,
    },
  );

  const fetchCollections = useCallback(async (_silent = false) => {
    try {
      const summaries = await listCollections();
      const detailed = await Promise.all(
        summaries.map(async (c) => {
          try {
            return await getCollection(c.id);
          } catch (e) {
            console.error(`Failed to load details for collection ${c.id}:`, e);
            // Fall back to creating a dummy detailed shape based on summary to prevent failures
            return {
              ...c,
              summary: c.summary || "",
              author: c.author || "Unknown",
              total_mods: c.total_mods || 0,
              total_size: c.total_size || 0,
              game: c.game || "marvelrivals",
              revision_id: null,
              created_at: null,
              mod_files: []
            } as ApiCollection;
          }
        })
      );
      setCollectionsData(detailed);
    } catch (err) {
      console.error("Error fetching collections:", err);
    }
  }, []);

  const handleDeleteCollection = async (id: number) => {
    if (!confirm("Are you sure you want to remove this collection?")) return;
    try {
      await deleteCollection(id);
      toast.success("Collection removed");
      await fetchCollections(false);
    } catch (err) {
      toast.error("Failed to delete collection");
    }
  };

  // Prepare collections display data in a vertical stack representation
  const collectionsWithDisplayData = useMemo(() => {
    return collectionsData.map((coll) => {
      const mappedModsArr: { modFile: ApiCollectionModFile; modObj: Mod }[] = [];

      // Filter mod files by search query
      const filteredFiles = coll.mod_files.filter((f) => {
        if (!searchQuery) return true;
        const q = searchQuery.toLowerCase();
        const name = (f.mod_name || f.file_name || "").toLowerCase();
        return name.includes(q);
      });

      // Group files by modId (or file_id if mod_id is null)
      const groupedFiles = new Map<string, ApiCollectionModFile[]>();
      filteredFiles.forEach((f) => {
        const midStr = String(f.mod_id || f.file_id);
        if (!groupedFiles.has(midStr)) groupedFiles.set(midStr, []);
        groupedFiles.get(midStr)!.push(f);
      });

      Array.from(groupedFiles.entries()).forEach(([modIdStr, variants]) => {
        const mainVariant = variants[0];
        
        const downloadedVariants = variants.filter((v) =>
          isCollectionModDownloadedIndexed(v, installedModsIndex, recentlyCompletedFileIds)
        );
        
        const downloadingVariants = variants.filter((v) => {
          if (downloadedVariants.includes(v)) return false;
          return activeHandoffs.some(h => {
            const reqModId = h.request?.mod_id;
            const reqFileId = h.request?.file_id;
            const fileIdMatches = idsEqual(reqFileId, v.file_id);
            const modIdMatches = idsEqual(reqModId, v.mod_id);
            const isHandoffActive = h.progress?.stage !== "failed" && h.progress?.error == null;
            if (fileIdMatches && isHandoffActive) return true;
            if (reqFileId == null && modIdMatches && isHandoffActive) return true;
            return false;
          });
        });

        const activeProgresses = downloadingVariants.map(v => {
          const handoff = activeHandoffs.find(h => {
            const reqModId = h.request?.mod_id;
            const reqFileId = h.request?.file_id;
            const fileIdMatches = idsEqual(reqFileId, v.file_id);
            const modIdMatches = idsEqual(reqModId, v.mod_id);
            const isHandoffActive = h.progress?.stage !== "failed" && h.progress?.error == null;
            if (fileIdMatches && isHandoffActive) return true;
            if (reqFileId == null && modIdMatches && isHandoffActive) return true;
            return false;
          });
          return handoff?.progress?.percent ?? 0;
        });
        const downloadProgress = activeProgresses.length > 0 ? Math.max(...activeProgresses) : 0;
        
        const failedVariants = variants.filter((v) => {
          if (downloadedVariants.includes(v)) return false;
          
          const isCurrentlyDownloading = activeHandoffs.some(h => {
            const reqModId = h.request?.mod_id;
            const reqFileId = h.request?.file_id;
            const fileIdMatches = idsEqual(reqFileId, v.file_id);
            const modIdMatches = idsEqual(reqModId, v.mod_id);
            const isHandoffActive = h.progress?.stage !== "failed" && h.progress?.error == null;
            if (fileIdMatches && isHandoffActive) return true;
            if (reqFileId == null && modIdMatches && isHandoffActive) return true;
            return false;
          });
          
          return !isCurrentlyDownloading && failedFileIds.has(String(v.file_id).trim());
        });

        const isFailed = failedVariants.length > 0 && downloadingVariants.length === 0;
        const failureReason = isFailed ? failedFileIds.get(String(failedVariants[0].file_id).trim()) : undefined;

        const installed =
          installedModsIndex.backendModIdToMod.get(modIdStr) ||
          downloadedVariants.reduce((found, dv) => {
            if (found) return found;
            const byFileId = installedModsIndex.fileIdToMod.get(String(dv.file_id).trim());
            if (byFileId) return byFileId;
            const normUri = normalizeFilename(dv.file_uri);
            return installedModsIndex.normPathToMod.get(normUri) || null;
          }, null as any);

        const isInstalled = downloadedVariants.length === variants.length;

        // Detect incompatible mods: installed but contains no .pak files
        const isIncompatible = !!installed && Array.isArray(installed.contents) &&
          installed.contents.length > 0 &&
          !installed.contents.some((f: string) => f.toLowerCase().endsWith(".pak"));

        const totalBytes = variants.reduce(
          (sum, v) => sum + (v.size_in_bytes || 0),
          0
        );
        const displaySize =
          totalBytes > 0
            ? totalBytes >= 1024 * 1024 * 1024
              ? `${(totalBytes / 1024 / 1024 / 1024).toFixed(2)} GB`
              : `${(totalBytes / 1024 / 1024).toFixed(1)} MB`
            : undefined;

        const modObj: Mod = {
          id: modIdStr,
          backendModId: mainVariant.mod_id,
          name: mainVariant.mod_name || mainVariant.file_name || "Unknown",
          description: mainVariant.file_name || "",
          author: installed?.author || "Nexus User",
          category: "",
          tags: mainVariant.optional ? ["Optional"] : ["Required"],
          downloads: 0,
          rating: 0,
          images: [mainVariant.picture_url].filter(Boolean),
          version: mainVariant.version || "1.0",
          lastUpdated: coll.updated_at || new Date().toISOString(),
          isInstalled,
          isActive: installed?.isActive || false,
          isFavorited: installed?.isFavorited || false,
          size: displaySize,
          hideMetrics: true,
          collectionVariantsCount: variants.length,
          collectionDownloadedCount: downloadedVariants.length,
          collectionVariants: variants as any,
          isDownloading: downloadingVariants.length > 0,
          downloadProgress,
          isFailed,
          failureReason,
          ...(installed
            ? {
                ...installed,
                tags: mainVariant.optional ? ["Optional"] : ["Required"],
                hideMetrics: true,
                collectionVariantsCount: variants.length,
                collectionDownloadedCount: downloadedVariants.length,
                collectionVariants: variants as any,
                isInstalled,
                isDownloading: downloadingVariants.length > 0,
                downloadProgress,
                isFailed,
                failureReason,
              }
            : {}),
          isIncompatible,
        };

        mappedModsArr.push({ modFile: mainVariant, modObj });
      });

      mappedModsArr.sort((a, b) => {
        const nameA = a.modObj.name || "";
        const nameB = b.modObj.name || "";
        return sortOrder === "asc"
          ? nameA.localeCompare(nameB)
          : nameB.localeCompare(nameA);
      });

      const isFullyDownloaded =
        mappedModsArr.length > 0 &&
        mappedModsArr.every(({ modObj }) => modObj.isInstalled);

      const hasInstalledMods = mappedModsArr.some(({ modObj }) => modObj.isInstalled);
      const canEnableCollection =
        hasInstalledMods &&
        mappedModsArr.every(
          ({ modObj }) =>
            modObj.isInstalled || (modObj.isFailed && !modObj.isInstalled)
        );

      const missingFiles = coll.mod_files.filter((f) => {
        return !isCollectionModDownloadedIndexed(f, installedModsIndex, recentlyCompletedFileIds);
      });

      return {
        collection: coll,
        mappedMods: mappedModsArr,
        isFullyDownloaded,
        canEnableCollection,
        missingFiles,
      };
    });
  }, [
    collectionsData,
    searchQuery,
    installedModsIndex,
    recentlyCompletedFileIds,
    activeHandoffs,
    failedFileIds,
    sortOrder,
  ]);

  const handleDownloadCollection = async (coll: ApiCollection) => {
    const pending = coll.mod_files.filter((f) => {
      const isDownloaded = isCollectionModDownloadedIndexed(f, installedModsIndex, recentlyCompletedFileIds);
      const isFailed = failedFileIds.has(String(f.file_id).trim());
      
      const isActivelyDownloading = activeHandoffs.some(h => {
        const reqModId = h.request?.mod_id;
        const reqFileId = h.request?.file_id;
        const fileIdMatches = idsEqual(reqFileId, f.file_id);
        const modIdMatches = idsEqual(reqModId, f.mod_id);
        const isHandoffActive = h.progress?.stage !== "failed" && h.progress?.error == null;
        
        if (fileIdMatches && isHandoffActive) return true;
        if (reqFileId == null && modIdMatches && isHandoffActive) return true;
        return false;
      });

      return !isDownloaded && !isFailed && !isActivelyDownloading;
    });

    if (pending.length === 0) {
      const onlyFailedPending = coll.mod_files.filter((f) => {
        const isDownloaded = isCollectionModDownloadedIndexed(f, installedModsIndex, recentlyCompletedFileIds);
        const isFailed = failedFileIds.has(String(f.file_id).trim());
        return !isDownloaded && isFailed;
      });

      if (onlyFailedPending.length > 0) {
        toast.info("Remaining pending downloads have failed. Please click 'Failed (Retry)' on the mod cards to retry them.");
      } else {
        toast.info("All mods are already downloaded.");
      }
      return;
    }

    setDownloadingCollectionIds(prev => ({ ...prev, [coll.id]: true }));
    const BATCH = 5;
    const batch = pending.slice(0, BATCH);

    toast.info(`Download assistant started! Opening first ${batch.length} mods...`, {
      description: "Click 'Slow Download' on the opened browser tabs.",
    });

    await Promise.all(
      batch.map((f) => {
        const game = coll.game || "marvelrivals";
        const url = `https://www.nexusmods.com/${game}/mods/${f.mod_id}?tab=files&file_id=${f.file_id}&nmm=1`;
        return openInBrowser(url);
      })
    );

    if (pending.length <= BATCH) {
      setDownloadingCollectionIds(prev => ({ ...prev, [coll.id]: false }));
      toast.success("All download links have been sent to your browser!");
    }
  };

  const handleDownloadNextBatch = async (coll: ApiCollection) => {
    const pending = coll.mod_files.filter((f) => {
      const isDownloaded = isCollectionModDownloadedIndexed(f, installedModsIndex, recentlyCompletedFileIds);
      const isFailed = failedFileIds.has(String(f.file_id).trim());
      
      const isActivelyDownloading = activeHandoffs.some(h => {
        const reqModId = h.request?.mod_id;
        const reqFileId = h.request?.file_id;
        const fileIdMatches = idsEqual(reqFileId, f.file_id);
        const modIdMatches = idsEqual(reqModId, f.mod_id);
        const isHandoffActive = h.progress?.stage !== "failed" && h.progress?.error == null;
        
        if (fileIdMatches && isHandoffActive) return true;
        if (reqFileId == null && modIdMatches && isHandoffActive) return true;
        return false;
      });

      return !isDownloaded && !isFailed && !isActivelyDownloading;
    });

    if (pending.length === 0) {
      const onlyFailedPending = coll.mod_files.filter((f) => {
        const isDownloaded = isCollectionModDownloadedIndexed(f, installedModsIndex, recentlyCompletedFileIds);
        const isFailed = failedFileIds.has(String(f.file_id).trim());
        return !isDownloaded && isFailed;
      });

      setDownloadingCollectionIds(prev => ({ ...prev, [coll.id]: false }));
      if (onlyFailedPending.length > 0) {
        toast.warning("Remaining pending downloads have failed. Please click 'Failed (Retry)' on the mod cards to retry them.");
      } else {
        toast.success("All mods are already downloaded!");
      }
      return;
    }

    const BATCH = 5;
    const batch = pending.slice(0, BATCH);

    toast.info(`Opening next batch of ${batch.length} mods...`, {
      description: "Click 'Slow Download' on the opened browser tabs.",
    });

    await Promise.all(
      batch.map((f) => {
        const game = coll.game || "marvelrivals";
        const url = `https://www.nexusmods.com/${game}/mods/${f.mod_id}?tab=files&file_id=${f.file_id}&nmm=1`;
        return openInBrowser(url);
      })
    );

    if (pending.length <= BATCH) {
      setDownloadingCollectionIds(prev => ({ ...prev, [coll.id]: false }));
      toast.success("All download links have been sent to your browser!");
    }
  };

  /**
   * Shared core: disable all currently-active mods NOT in targetModIds,
   * then activate only the correct pak variant for each mod in targetModIds.
   */
  const applyCollectionLoadout = async (
    targetModObjs: Mod[],
    label: string
  ) => {
    // Step 1 – Disable every active installed mod first to ensure a clean sweep of the ~mods folder
    let deactivatedCount = 0;
    try {
      await scanActive();
      const allDownloads = await listDownloads();
      const activeDownloads = allDownloads.filter(
        (dl) => dl.active_paks && dl.active_paks.length > 0
      );
      for (const dl of activeDownloads) {
        await setActivePaks(Number(dl.id), []);
        deactivatedCount++;
      }
    } catch (err) {
      console.warn("Failed to perform complete cleanup sweep, falling back to installedMods filter:", err);
      const toDeactivate = installedMods.filter(
        (m) => m.isActive !== false && m.isInstalled
      );
      for (const mod of toDeactivate) {
        for (const dlId of mod.sourceDownloadIds || []) {
          await setActivePaks(Number(dlId), []);
        }
      }
      deactivatedCount = toDeactivate.length;
    }

    // Step 2 – Enable each target mod with its currently-active pak variant
    let enabledCount = 0;
    for (const mod of targetModObjs) {
      if (!mod.isInstalled) continue;
      const downloadIds: number[] = mod.sourceDownloadIds || [];
      for (const dlId of downloadIds) {
        const dl = await getLocalDownload(Number(dlId));
        const paks = (dl.contents || []).filter((f: string) =>
          f.toLowerCase().endsWith(".pak")
        );
        const activeBases = new Set(
          (mod.defaultActivePaks || []).map((p: string) => {
            const parts = p.split(/[\/\\]/);
            return parts[parts.length - 1].toLowerCase();
          })
        );
        const targetPaks =
          activeBases.size > 0
            ? paks.filter((p: string) => {
                const parts = p.split(/[\/\\]/);
                return activeBases.has(parts[parts.length - 1].toLowerCase());
              })
            : paks;
        await setActivePaks(Number(dlId), targetPaks);
      }
      enabledCount++;
    }

    // Step 3 – Single filesystem sync
    await scanActive();
    await refreshConflicts();
    if (onRefreshMods) {
      await (onRefreshMods() as any);
    }
    await fetchCollections(true);

    toast.success(
      `${label}: ${enabledCount} mod${
        enabledCount !== 1 ? "s" : ""
      } enabled, ${deactivatedCount} deactivated`
    );
  };

  const handleEnableAll = async (coll: ApiCollection, mappedMods: any[], canEnableCollection: boolean) => {
    if (!canEnableCollection) {
      toast.error("Please download all available mods first");
      return;
    }
    const toastId = `collection-enable-all-${coll.id}`;
    toast.loading("Applying collection loadout...", { id: toastId });
    try {
      const targets = mappedMods
        .filter(({ modObj }) => modObj.isInstalled && !modObj.isIncompatible)
        .map(({ modObj }) => modObj);
      await applyCollectionLoadout(targets, `Collection "${coll.name || coll.slug}" — All`);
      toast.dismiss(toastId);
    } catch (e: any) {
      toast.error(e?.message || "Failed to apply collection loadout", {
        id: toastId,
      });
    }
  };

  const handleEnableRequired = async (coll: ApiCollection, mappedMods: any[], canEnableCollection: boolean) => {
    if (!canEnableCollection) {
      toast.error("Please download all available mods first");
      return;
    }
    const toastId = `collection-enable-required-${coll.id}`;
    toast.loading("Applying required mods...", { id: toastId });
    try {
      const targets = mappedMods
        .filter(
          ({ modObj, modFile }) => modObj.isInstalled && !modObj.isIncompatible && !modFile.optional
        )
        .map(({ modObj }) => modObj);
      await applyCollectionLoadout(targets, `Collection "${coll.name || coll.slug}" — Required`);
      toast.dismiss(toastId);
    } catch (e: any) {
      toast.error(e?.message || "Failed to apply required mods", {
        id: toastId,
      });
    }
  };

  const handleView = (mod: Mod) => {
    setSelectedMod(mod);
    setModalInitialTab("overview");
    setIsModalOpen(true);
  };

  const handleViewFilesTab = (mod: Mod) => {
    setSelectedMod(mod);
    setModalInitialTab("files");
    setIsModalOpen(true);
  };

  const formatDate = (dateString?: string | null) => {
    if (!dateString) return "Unknown";
    try {
      return new Date(dateString).toLocaleDateString("en-US", {
        year: "numeric",
        month: "long",
        day: "numeric",
      });
    } catch {
      return "Unknown";
    }
  };

  const formatDateTime = (isoString: string) => {
    try {
      return new Date(isoString).toLocaleString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "Unknown";
    }
  };

  const handleApplyBackup = async (meta: BackupMeta) => {
    setRestoringBackup(meta);
  };

  const handleDeleteBackup = (id: string) => {
    removeBackupMeta(id);
    setBackupMetas((prev) => prev.filter((m) => m.id !== id));
    toast.success("Backup removed from list");
  };


  return (
    <>
      <div className="flex flex-col h-full">
        <SearchHeader
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          viewMode={viewMode}
          onViewModeChange={onViewModeChange}
          sortBy={sortBy}
          onSortChange={(val) => setSortBy(val as "name" | "date")}
          sortOrder={sortOrder}
          onSortOrderChange={(val) => setSortOrder(val as "asc" | "desc")}
        />

        {/* Absolute Parity Styles */}
        <style>{`.custom-scrollbar::-webkit-scrollbar {
            width: 8px;
          }
          .custom-scrollbar::-webkit-scrollbar-track {
            background: transparent;
          }
          .custom-scrollbar::-webkit-scrollbar-thumb {
            background: rgba(100, 100, 100, 0.5);
            border-radius: 4px;
          }
          .custom-scrollbar::-webkit-scrollbar-thumb:hover {
            background: rgba(100, 100, 100, 0.7);
          }
          .custom-scrollbar {
            scrollbar-color: rgba(100, 100, 100, 0.5) transparent;
            scrollbar-width: thin;
          }
          .mods-grid {
            display: grid;
            gap: 1.5rem;
            grid-template-columns: 1fr;
          }
          @media (min-width: 768px) {
            .mods-grid {
              grid-template-columns: repeat(2, 1fr);
            }
          }
          @media (min-width: 1024px) {
            .mods-grid {
              grid-template-columns: repeat(3, 1fr);
            }
          }
          @media (min-width: 1280px) {
            .mods-grid {
              grid-template-columns: repeat(4, 1fr);
            }
          }
          @media (min-width: 1500px) {
            .mods-grid {
              grid-template-columns: repeat(5, 1fr);
            }
          }
          @keyframes pulltab-heartbeat {
            0%, 100% {
              transform: translateX(-50%) scale(1);
            }
            30% {
              transform: translateX(-50%) scale(1.15);
            }
            60% {
              transform: translateX(-50%) scale(0.9);
            }
            80% {
              transform: translateX(-50%) scale(1.1);
            }
          }
          .animate-pulltab-heartbeat {
            animation: pulltab-heartbeat 0.8s ease-in-out 2;
          }
          `}</style>

        <div className="flex flex-1 overflow-hidden">
          {/* MAIN CONTENT AREA */}
          <div
            ref={scrollRef}
            className="flex-1 overflow-auto custom-scrollbar p-6"
            style={{ overflowY: "auto" }}
          >
            {/* ── My Backups Section ── */}
            <div
              className="relative border border-border/50 rounded-xl bg-card/30 backdrop-blur-sm shadow-sm"
              style={{
                display: "flex",
                flexDirection: "column",
                marginBottom: "15px",
              }}
            >
              {/* Backups Header */}
              <div
                className="hover:bg-card/40 transition-colors select-none rounded-xl"
                onClick={() => setBackupsExpanded((v) => !v)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "15px",
                  cursor: "pointer",
                }}
              >
                <div
                  style={{ display: "flex", alignItems: "center", gap: "12px" }}
                >
                  <div
                    style={{
                      width: "36px",
                      height: "36px",
                      borderRadius: "10px",
                      background:
                        "linear-gradient(135deg, rgba(139,92,246,0.2), rgba(99,102,241,0.2))",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    <Archive className="w-4 h-4 text-violet-400" />
                  </div>
                  <div>
                    <h2
                      className="text-lg font-bold tracking-tight"
                      style={{ margin: 0 }}
                    >
                      My Backups
                    </h2>
                    <p
                      className="text-xs text-muted-foreground"
                      style={{ margin: "2px 0 0 0" }}
                    >
                      Saved mod loadout snapshots
                    </p>
                  </div>
                  <Badge
                    variant="secondary"
                    className="text-xs"
                    style={{ marginLeft: "6px" }}
                  >
                    {backupMetas.length}
                  </Badge>
                </div>
                <ChevronDown
                  className="w-4 h-4 text-muted-foreground"
                  style={{
                    transform: backupsExpanded
                      ? "rotate(180deg)"
                      : "rotate(0deg)",
                    transition: "transform 220ms ease-in-out",
                  }}
                />
              </div>

              {/* Backups collapsible content */}
              <div
                style={{
                  display: "grid",
                  gridTemplateRows: backupsExpanded ? "1fr" : "0fr",
                  transition:
                    "grid-template-rows 220ms cubic-bezier(0.4, 0, 0.2, 1)",
                }}
              >
                <div className="overflow-hidden">
                  <div
                    style={{
                      paddingLeft: "10px",
                      paddingRight: "10px",
                      paddingBottom: "10px",
                      borderTop: "1px solid rgba(255, 255, 255, 0.05)",
                    }}
                  >
                    {backupMetas.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-10 text-center">
                        <div
                          style={{
                            width: "52px",
                            height: "52px",
                            borderRadius: "14px",
                            background: "rgba(139,92,246,0.1)",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            marginBottom: "12px",
                          }}
                        >
                          <Archive className="w-6 h-6 text-violet-400/60" />
                        </div>
                        <p className="text-sm font-medium text-muted-foreground">
                          No backups yet
                        </p>
                        <p className="text-xs text-muted-foreground/60 mt-1">
                          Use the <strong>Backup</strong> button in the header
                          to create a snapshot.
                        </p>
                      </div>
                    ) : (
                      <div style={{ display: "flex", flexDirection: "column" }}>
                        {backupMetas.map((meta) => (
                          <div
                            key={meta.id}
                            style={{
                              padding: "14px 16px",
                              borderRadius: "12px",
                              background: "hsl(var(--card) / 0.6)",
                              border: "1px solid hsl(var(--border) / 0.5)",
                              display: "flex",
                              alignItems: "center",
                              gap: "14px",
                              transition: "border-color 0.2s",
                            }}
                            className="hover:border-violet-500/30"
                          >
                            <div
                              style={{
                                width: "40px",
                                height: "40px",
                                borderRadius: "10px",
                                background:
                                  "linear-gradient(135deg, rgba(139,92,246,0.15), rgba(99,102,241,0.15))",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                flexShrink: 0,
                              }}
                            >
                              <Archive className="w-4 h-4 text-violet-400" />
                            </div>

                            <div style={{ flex: 1, minWidth: 0 }}>
                              <p
                                className="text-sm font-semibold text-foreground truncate"
                                style={{ margin: 0 }}
                              >
                                {meta.name}
                              </p>
                              <div
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "12px",
                                  marginTop: "6px",
                                  flexWrap: "wrap",
                                }}
                              >
                                <span
                                  className="text-xs text-muted-foreground"
                                  style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: "4px",
                                  }}
                                >
                                  <Clock className="w-3 h-3" />
                                  {formatDateTime(meta.createdAt)}
                                </span>
                                <Badge
                                  variant="secondary"
                                  className="text-xs py-0"
                                  style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: "4px",
                                  }}
                                >
                                  <Power className="w-2.5 h-2.5" />{" "}
                                  {meta.activeMods} active
                                </Badge>
                                <Badge
                                  variant="outline"
                                  className="text-xs py-0"
                                >
                                  {meta.totalMods} total
                                </Badge>
                              </div>
                            </div>

                            <div
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: "8px",
                                flexShrink: 0,
                              }}
                            >
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={restoringBackup?.id === meta.id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleApplyBackup(meta);
                                }}
                                className="h-8 text-xs"
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "6px",
                                  borderColor: "rgba(245,158,11,0.4)",
                                  color: "#f59e0b",
                                }}
                              >
                                <RotateCcw className="w-3 h-3" />
                                {restoringBackup?.id === meta.id
                                  ? "Applying…"
                                  : "Apply"}
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteBackup(meta.id);
                                }}
                                className="h-8 text-xs text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
                                style={{
                                  display: "flex",
                                  alignItems: "center",
                                  gap: "6px",
                                }}
                              >
                                <Trash2 className="w-3 h-3" />
                              </Button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {collectionsData.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center max-w-xl mx-auto border border-dashed border-border/60 rounded-2xl bg-card/10 backdrop-blur-sm p-8 shadow-inner mt-4">
                <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mb-6">
                  <Download className="w-8 h-8 text-primary animate-bounce" />
                </div>
                <h2 className="text-xl font-bold tracking-tight mb-2">No collections imported</h2>
                <p className="text-muted-foreground text-sm leading-relaxed mb-6">
                  To get started, navigate to any collection page on the Nexus Mods website and click the
                  <span className="font-semibold text-foreground"> "Continue"</span>,{" "}
                  <span className="font-semibold text-foreground">"Add to Mod Manager"</span>, or{" "}
                  <span className="font-semibold text-foreground">"Vortex"</span> button.{" "}
                  RivalNxt will automatically intercept the link, import all data, and display your collection here in real-time.
                </p>
                <div className="text-xs text-muted-foreground/60 border-t border-border/40 pt-4 w-full flex items-center justify-center gap-2">
                  <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 animate-ping" />
                  Deep link background listener is active and waiting for a collection
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-8">
                {/* Vertical stacked collections list */}
                {collectionsWithDisplayData.map(({ collection: c, mappedMods, canEnableCollection, missingFiles }) => {
                  const isExpanded = expandedCollections[c.id] ?? false;
                  const isEnableHovered = hoveredEnableIds[c.id] ?? false;
                  const isDownloadingCollection = downloadingCollectionIds[c.id] ?? false;

                  return (
                    <div
                      key={c.id}
                      className="relative border border-border/50 rounded-xl bg-card/30 backdrop-blur-sm shadow-sm flex flex-col mb-4"
                    >
                      {/* Header Area */}
                      <div className="group relative">
                        <div  
                          className="relative cursor-pointer hover:bg-card/40 transition-colors select-none flex justify-between items-stretch gap-8"
                          style={{
                            borderTopLeftRadius: "12px",
                            borderTopRightRadius: "12px"
                          }}
                          onClick={() =>
                            setExpandedCollections((prev) => ({
                              ...prev,
                              [c.id]: !isExpanded,
                            }))
                          }
                        >
                          {/* Left Info: Image + Details */}
                          <div className="flex-1 min-w-0 flex items-stretch">
                            {c.picture_url && (
                              <div
                                className="relative aspect-square self-stretch shrink-0"
                                style={{
                                  borderTopLeftRadius: "11px",
                                  borderBottomLeftRadius: "11px",
                                  overflow: "hidden"
                                }}
                              >
                                <img
                                  src={c.picture_url}
                                  alt={c.name || c.slug}
                                  className="absolute inset-0 w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
                                  style={{
                                    borderTopLeftRadius: "11px",
                                    borderBottomLeftRadius: "11px"
                                  }}
                                />
                                <div className="absolute inset-0 bg-gradient-to-t from-black/25 to-transparent pointer-events-none" />
                              </div>
                            )}
                            <div
                              className="flex-1 min-w-0 flex flex-col justify-between"
                              style={{
                                paddingTop: "12px",
                                paddingBottom: "20px",
                                paddingLeft: "20px",
                                paddingRight: "20px"
                              }}
                            >
                              <div>
                                <div className="text-lg font-bold tracking-tight mb-2 flex flex-wrap items-center gap-3 leading-tight text-foreground">
                                  <span>{c.name || c.slug}</span>
                                  <Badge variant="secondary" className="text-xs font-medium">
                                    Revision {c.revision_num}
                                  </Badge>
                                  <Badge
                                    variant="outline"
                                    className="text-xs font-medium capitalize bg-primary/5 border-primary/20 text-primary"
                                  >
                                    {c.status}
                                  </Badge>
                                </div>
                                <p
                                  className="text-muted-foreground max-w-3xl text-sm mt-1 mb-4"
                                  style={{
                                    display: "-webkit-box",
                                    WebkitLineClamp: 2,
                                    WebkitBoxOrient: "vertical",
                                    overflow: "hidden"
                                  }}
                                >
                                  {c.summary}
                                </p>
                              </div>
                              <div className="flex flex-col gap-2 mt-auto">
                                <div
                                  className="flex flex-wrap items-center text-xs text-muted-foreground"
                                  style={{ gap: "1rem" }}
                                >
                                  <div className="flex items-center gap-1">
                                    <Users className="h-4 w-4 opacity-70" />
                                    <span>
                                      Curated by{" "}
                                      <span className="font-medium text-foreground">
                                        {c.author || "Unknown"}
                                      </span>
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-1">
                                    <Calendar className="h-4 w-4 opacity-60" />
                                    <span>
                                      Updated:{" "}
                                      <span className="font-medium text-foreground">
                                        {formatDate(c.updated_at)}
                                      </span>
                                    </span>
                                  </div>
                                </div>
                                <div className="flex items-center gap-4 text-xs text-muted-foreground mt-1">
                                  <div className="flex items-center gap-1">
                                    <FolderOpen className="h-4 w-4 opacity-70" />
                                    <span>
                                      <span className="font-medium text-foreground">
                                        {c.total_mods}
                                      </span>{" "}
                                      Mods Included
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-1">
                                    <Download className="h-4 w-4 opacity-70" />
                                    <span>
                                      <span className="font-medium text-foreground">
                                        {((c.total_size || 0) / 1024 / 1024 / 1024).toFixed(2)} GB
                                      </span>{" "}
                                      Total
                                    </span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>

                          {/* Right Buttons: Download, Enable, Delete */}
                          <div
                            className="flex flex-col gap-3 shrink-0 min-w-[200px] justify-center"
                            style={{
                              paddingTop: "24px",
                              paddingBottom: "24px",
                              paddingRight: "24px"
                            }}
                            onClick={(e) => e.stopPropagation()}
                          >
                              {isDownloadingCollection ? (
                                <div className="flex flex-col gap-1.5 w-full p-2.5 rounded-xl border border-amber-500/30 bg-amber-500/5 backdrop-blur-sm">
                                  <div className="text-[10px] font-bold text-amber-400 uppercase tracking-wider text-center">
                                    Download Assistant Active
                                  </div>
                                  <Button
                                    size="sm"
                                    className="w-full gap-2 shadow-lg shadow-amber-500/20 font-semibold bg-amber-500 hover:bg-amber-600 text-white"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleDownloadNextBatch(c);
                                    }}
                                  >
                                    <Download className="w-3.5 h-3.5 animate-bounce" />
                                    Open Next 5 ({missingFiles.length} left)
                                  </Button>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="w-full text-muted-foreground hover:text-red-400 hover:bg-red-500/5 text-[10px] h-6"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setDownloadingCollectionIds((prev) => ({
                                        ...prev,
                                        [c.id]: false,
                                      }));
                                    }}
                                  >
                                    Reset Assistant
                                  </Button>
                                </div>
                              ) : (
                                <Button
                                  size="sm"
                                  className="w-full gap-2 shadow-lg shadow-primary/20 font-semibold"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleDownloadCollection(c);
                                  }}
                                >
                                  <Download className="w-3.5 h-3.5" />
                                  Download Collection
                                </Button>
                              )}

                              <div
                                className={`relative border rounded-lg overflow-hidden w-full transition-all cursor-pointer flex items-center ${
                                  canEnableCollection
                                    ? "border-transparent bg-primary text-primary-foreground shadow-lg shadow-primary/20 hover:bg-primary/90"
                                    : "border-border/50 bg-background/20 shadow-sm hover:border-border/80 hover:bg-background/40"
                                }`}
                                style={{ height: "36px" }}
                                onMouseEnter={() =>
                                  setHoveredEnableIds((prev) => ({ ...prev, [c.id]: true }))
                                }
                                onMouseLeave={() =>
                                  setHoveredEnableIds((prev) => ({ ...prev, [c.id]: false }))
                                }
                                onClick={(e) => e.stopPropagation()}
                              >
                                <div
                                  className={`absolute inset-0 flex items-center justify-center gap-2 font-semibold select-none transition-all duration-200 ease-in-out pointer-events-none ${
                                    canEnableCollection
                                      ? "text-primary-foreground"
                                      : "text-muted-foreground"
                                  }`}
                                  style={{ opacity: isEnableHovered ? 0 : 1 }}
                                >
                                  <Power
                                    className={
                                      canEnableCollection
                                        ? "text-primary-foreground"
                                        : "text-muted-foreground/80"
                                    }
                                    strokeWidth={2.25}
                                    style={{ width: "16px", height: "16px" }}
                                  />
                                  <span className="font-semibold" style={{ fontSize: "13px" }}>
                                    Enable
                                  </span>
                                </div>
                                <div
                                  className={`absolute inset-0 flex items-stretch transition-all duration-200 ease-in-out z-10 backdrop-blur-md ${
                                    canEnableCollection
                                      ? "bg-primary text-black"
                                      : "bg-card/95 text-muted-foreground"
                                  }`}
                                  style={{
                                    opacity: isEnableHovered ? 1 : 0,
                                    pointerEvents: isEnableHovered ? "auto" : "none",
                                  }}
                                >
                                  <button
                                    className={`flex-1 flex items-center justify-center font-semibold transition-all cursor-pointer border-none bg-transparent outline-none h-full ${
                                      canEnableCollection
                                        ? "text-black hover:text-emerald-700 hover:bg-black/10 active:bg-black/15"
                                        : "text-muted-foreground hover:text-emerald-400 hover:bg-emerald-500/10 active:bg-emerald-500/20"
                                    }`}
                                    style={{ fontSize: "12px" }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleEnableAll(c, mappedMods, canEnableCollection);
                                    }}
                                  >
                                    All
                                  </button>
                                  <div
                                    className="shrink-0"
                                    style={{
                                      width: "1px",
                                      height: "16px",
                                      backgroundColor: canEnableCollection ? "rgba(0, 0, 0, 0.15)" : "rgba(255, 255, 255, 0.15)",
                                      alignSelf: "center",
                                    }}
                                  />
                                  <button
                                    className={`flex-1 flex items-center justify-center font-semibold transition-all cursor-pointer border-none bg-transparent outline-none h-full ${
                                      canEnableCollection
                                        ? "text-black hover:text-amber-700 hover:bg-black/10 active:bg-black/15"
                                        : "text-muted-foreground hover:text-amber-400 hover:bg-amber-500/10 active:bg-amber-500/20"
                                    }`}
                                    style={{ fontSize: "12px" }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleEnableRequired(c, mappedMods, canEnableCollection);
                                    }}
                                  >
                                    Required
                                  </button>
                                </div>
                              </div>

                              <div
                                className="w-full rounded-lg border border-destructive/20 bg-destructive/5 overflow-hidden"
                                style={{ marginTop: "2px" }}
                              >
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="w-full text-destructive hover:text-destructive hover:bg-destructive/10 gap-2 h-8 font-medium"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleDeleteCollection(c.id);
                                  }}
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                  Delete Collection
                                </Button>
                              </div>
                            </div>

                          {/* Pull-Tab Trigger */}
                          <div
                            className="absolute rounded-full bg-card border border-border/60 shadow-md flex items-center justify-center text-muted-foreground hover:text-primary hover:border-primary/40 active:scale-95 group-hover:scale-105 transition-all duration-200 z-20 cursor-pointer animate-pulltab-heartbeat"
                            style={{
                              bottom: "-10px",
                              left: "50%",
                              transform: "translateX(-50%)",
                              width: "72px",
                              height: "20px",
                            }}
                            onClick={(e) => {
                              e.stopPropagation();
                              setExpandedCollections((prev) => ({
                                ...prev,
                                [c.id]: !isExpanded,
                              }));
                            }}
                          >
                            <ChevronDown
                              className="w-4 h-4"
                              style={{
                                transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
                                transition: "transform 220ms ease-in-out",
                              }}
                            />
                          </div>
                        </div>
                      </div>

                      {/* Expandable Mods List */}
                      <div
                        style={{
                          display: "grid",
                          gridTemplateRows: isExpanded ? "1fr" : "0fr",
                          transition:
                            "grid-template-rows 220ms cubic-bezier(0.4, 0, 0.2, 1)",
                        }}
                      >
                        <div className="overflow-hidden">
                          <div className="p-6 pt-4 pb-8 border-t border-border/10">
                            {mappedMods.length > 0 ? (
                              <VirtualizedModList
                                items={mappedMods}
                                scrollRef={scrollRef}
                                columns={gridColumns}
                                estimateRowHeight={viewMode === "grid" ? 320 : 96}
                                rowClassName={
                                  viewMode === "grid" ? "mods-grid" : "flex flex-col gap-0"
                                }
                                getKey={({ modObj }) => modObj.id}
                                renderItem={({ modObj }) => (
                                  <ModCard
                                    mod={modObj}
                                    viewMode={viewMode}
                                    onInstall={(_mId) => {
                                      // Just open the Files tab in-app so the user can
                                      // see the variants and click download there.
                                      handleViewFilesTab(modObj);
                                    }}
                                    onFavorite={onFavorite}
                                    onOpenFilesTab={() => handleViewFilesTab(modObj)}
                                    onToggleActive={onToggleMod}
                                    onView={handleView}
                                  />
                                )}
                              />
                            ) : (
                              <div className="flex flex-col items-center justify-center py-12 text-center">
                                <div className="bg-secondary/20 p-6 rounded-full mb-4">
                                  <Search className="w-12 h-12 text-muted-foreground/50" />
                                </div>
                                <h3 className="text-xl font-medium mb-2">No mods found</h3>
                                <p className="text-muted-foreground">
                                  Try adjusting your search query "{searchQuery}"
                                </p>
                                <Button
                                  variant="outline"
                                  className="mt-6"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setSearchQuery("");
                                  }}
                                >
                                  Clear Search
                                </Button>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
      {selectedMod && (
        <ModModal
          mod={selectedMod}
          isOpen={isModalOpen}
          onClose={() => {
            setIsModalOpen(false);
            setModalInitialTab("overview");
          }}
          onInstall={() => {}}
          onFavorite={onFavorite}
          initialTab={modalInitialTab}
          onRefresh={onRefreshMods}
        />
      )}
      <BackupRestoreModal
        meta={restoringBackup}
        installedMods={installedMods}
        onClose={() => setRestoringBackup(null)}
        onComplete={() => {
          setRestoringBackup(null);
          onRefreshMods?.();
        }}
      />
    </>
  );
}
