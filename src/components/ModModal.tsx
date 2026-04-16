import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "./ui/alert-dialog";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";
import { Separator } from "./ui/separator";
import { ScrollArea } from "./ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Textarea } from "./ui/textarea";
import {
  Download,
  Star,
  Heart,
  Calendar,
  File,
  Trash2,
  ExternalLink,
  Plus,
  X,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  FolderOpen,
  Pencil,
  Check,
  X as XIcon,
} from "lucide-react";
import type { Mod } from "./ModCard";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type SyntheticEvent,
} from "react";
import DOMPurify from "dompurify";
import {
  getModChangelogs,
  getModDetails,
  getPakAssets,
  fetchModImages,
  uploadModImages,
  deleteModImage,
  updateModDetails,
  type ApiChangelog,
  type ApiModDetails,
  type ApiPakAsset,
  type ModImage,
} from "../lib/api";
import { Switch } from "./ui/switch";
import {
  setActivePaks,
  scanActive,
  getLocalDownload,
  getPakVersionStatus,
  refreshConflicts,
  deleteLocalDownloads,
  type ApiPakVersionStatus,
} from "../lib/api";
import { toast } from "sonner";
import React from "react";

type DownloadEntry = {
  id: number;
  path: string;
  contents: string[];
  active_paks: string[];
  version: string | null;
  created_at?: string | null;
  name?: string | null;
};

type PakGroup = { primary: string; files: string[] };

function groupPakEntries(contents: string[] | null | undefined): PakGroup[] {
  if (!Array.isArray(contents)) {
    return [];
  }
  const groups = new Map<string, PakGroup>();
  for (const fileName of contents) {
    if (typeof fileName !== "string" || !fileName) continue;
    const stem = fileName.replace(/\.(pak|utoc|ucas|sig)$/i, "");
    const key = stem || fileName;
    const current = groups.get(key) ?? { primary: fileName, files: [] };
    current.files.push(fileName);
    if (/\.pak$/i.test(fileName)) {
      current.primary = fileName;
    } else if (!/\.pak$/i.test(current.primary)) {
      current.primary = current.primary || fileName;
    }
    groups.set(key, current);
  }
  return Array.from(groups.values()).filter((entry) =>
    entry.files.some((file) => /\.pak$/i.test(file)),
  );
}

/** Tree node for hierarchical file display */
type FileTreeNode = {
  name: string;
  children: FileTreeNode[];
  group?: PakGroup; // present only on leaf nodes (files)
};

/** Build a folder tree from pak groups for hierarchical display */
function buildFileTree(groups: PakGroup[]): FileTreeNode {
  const root: FileTreeNode = { name: "", children: [] };

  for (const group of groups) {
    const parts = group.primary.replace(/\\/g, "/").split("/");
    let current = root;

    // All parts except the last are folder segments
    for (let i = 0; i < parts.length - 1; i++) {
      const folderName = parts[i];
      let child = current.children.find(
        (c) => c.name === folderName && !c.group,
      );
      if (!child) {
        child = { name: folderName, children: [] };
        current.children.push(child);
      }
      current = child;
    }

    // Last part is the file (leaf node)
    current.children.push({
      name: parts[parts.length - 1],
      children: [],
      group,
    });
  }

  return collapseFileTree(root);
}

/** Collapse single-child folder chains to reduce redundant nesting */
function collapseFileTree(node: FileTreeNode): FileTreeNode {
  if (node.group) return node; // leaf node
  node.children = node.children.map(collapseFileTree);
  // Merge single-child folder chains: A > B > file => "A / B" > file
  while (
    node.children.length === 1 &&
    !node.children[0].group &&
    node.name !== ""
  ) {
    const child = node.children[0];
    node.name = `${node.name} / ${child.name}`;
    node.children = child.children;
  }
  return node;
}

/** Recursive tree renderer for hierarchical pak file display */
function FileTreeRenderer({
  nodes,
  depth,
  entryId,
  activeList,
  switchDisabled,
  handleToggle,
}: {
  nodes: FileTreeNode[];
  depth: number;
  entryId: number;
  activeList: string[];
  switchDisabled: boolean;
  handleToggle: (
    downloadId: number,
    files: string[],
    willCheck: boolean,
  ) => void;
}) {
  return (
    <>
      {nodes.map((node) => {
        if (node.group) {
          // Leaf node — render file with toggle
          const { files } = node.group;
          const checked = files.some((file) => activeList.includes(file));
          return (
            <div
              key={`${entryId}-${node.group.primary}`}
              className={`border border-border rounded-lg transition-colors ${
                checked ? "bg-green-100 dark:bg-green-900/60" : "bg-popover"
              }`}
              style={{ marginLeft: depth * 16, padding: "6px" }}
            >
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-3 min-w-0">
                  <File className="w-4 h-4 text-muted-foreground shrink-0" />
                  <div className="font-medium truncate">{node.name}</div>
                </div>
                <Switch
                  disabled={switchDisabled}
                  checked={checked}
                  onCheckedChange={(willCheck: boolean) =>
                    handleToggle(entryId, files, willCheck)
                  }
                />
              </div>
            </div>
          );
        }

        // Folder node — render collapsible section
        return (
          <FileTreeFolderNode
            key={`${entryId}-folder-${node.name}`}
            node={node}
            depth={depth}
            entryId={entryId}
            activeList={activeList}
            switchDisabled={switchDisabled}
            handleToggle={handleToggle}
          />
        );
      })}
    </>
  );
}

/** Collapsible folder node in the file tree */
function FileTreeFolderNode({
  node,
  depth,
  entryId,
  activeList,
  switchDisabled,
  handleToggle,
}: {
  node: FileTreeNode;
  depth: number;
  entryId: number;
  activeList: string[];
  switchDisabled: boolean;
  handleToggle: (
    downloadId: number,
    files: string[],
    willCheck: boolean,
  ) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const ChevronIcon = expanded ? ChevronDown : ChevronRight;

  return (
    <div style={{ marginLeft: depth * 16 }}>
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex items-center gap-2 py-1.5 px-2 rounded-md hover:bg-muted/50 transition-colors w-full text-left group"
      >
        <ChevronIcon className="w-3.5 h-3.5 text-muted-foreground shrink-0 transition-transform" />
        <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" />
        <span className="text-sm font-medium text-muted-foreground group-hover:text-foreground transition-colors truncate">
          {node.name}
        </span>
      </button>
      {expanded && (
        <div className="space-y-2 mt-1">
          <FileTreeRenderer
            nodes={node.children}
            depth={depth + 1}
            entryId={entryId}
            activeList={activeList}
            switchDisabled={switchDisabled}
            handleToggle={handleToggle}
          />
        </div>
      )}
    </div>
  );
}

function toActiveMap(entries: DownloadEntry[]): Record<number, string[]> {
  const map: Record<number, string[]> = {};
  for (const entry of entries) {
    map[entry.id] = Array.isArray(entry.active_paks)
      ? [...entry.active_paks]
      : [];
  }
  return map;
}

const toBasename = (value: string): string => {
  if (typeof value !== "string") return "";
  const parts = value.split(/[/\\]/);
  const last = parts[parts.length - 1];
  return last || value;
};

const normalizeVersion = (version?: string | null): string => {
  if (!version) return "Unknown";
  const trimmed = version.trim();
  if (!trimmed) return "Unknown";
  const dotParts = trimmed.split(".").filter(Boolean);
  if (dotParts.length > 0) {
    const limited = dotParts.slice(0, 3).map((part, index) => {
      if (index === 0) return part;
      if (part.length > 3) {
        return part.slice(0, 3);
      }
      return part;
    });
    return limited.join(".");
  }
  const numericParts = trimmed.match(/\d+/g);
  if (numericParts && numericParts.length > 0) {
    return numericParts
      .slice(0, 3)
      .map((part, index) => {
        if (index === 0) return part;
        return part.slice(0, 3);
      })
      .join(".");
  }
  return trimmed;
};

const getDownloadDisplayName = (entry: DownloadEntry): string => {
  if (!entry) {
    return "Download";
  }
  if (entry.name && entry.name.trim().length > 0) {
    return entry.name.trim();
  }
  if (entry.path && entry.path.trim().length > 0) {
    const base = toBasename(entry.path.trim());
    if (base.length > 0) {
      return base;
    }
  }
  return `Download #${entry.id}`;
};

interface ModModalProps {
  mod: Mod | null;
  isOpen: boolean;
  onClose: () => void;
  onInstall: (modId: string) => void;
  onFavorite: (modId: string) => void;
  onConflictStateChanged?: () => void;
  onRefresh?: () => void;
}

export function ModModal({
  mod,
  isOpen,
  onClose,
  onInstall,
  onFavorite,
  onConflictStateChanged,
  onRefresh,
}: ModModalProps) {
  const [details, setDetails] = useState<ApiModDetails | null>(null);
  // Files list from server is not needed for toggle UI; using local download contents instead
  // const [files, setFiles] = useState<ApiModFile[] | null>(null);
  const [changelogs, setChangelogs] = useState<ApiChangelog[] | null>(null);
  const [pakAssets, setPakAssets] = useState<ApiPakAsset[]>([]);

  // Determine effective mod ID for images (Nexus ID or negative local download ID)
  const effectiveModId = useMemo(() => {
    if (mod?.backendModId) return mod.backendModId;
    if (mod?.sourceDownloadIds && mod.sourceDownloadIds.length > 0) {
      // Use the first source download ID as a stable negative ID for local mods
      return -mod.sourceDownloadIds[0];
    }
    return null;
  }, [mod]);

  // Fetch from backend if we have a linked mods.mod_id which is strictly for Nexus details
  const serverModId = useMemo(() => mod?.backendModId ?? null, [mod]);
  const [isApplying, setIsApplying] = useState(false);
  const downloadIds = useMemo(
    () =>
      Array.isArray(mod?.sourceDownloadIds)
        ? mod.sourceDownloadIds
            .map((id) => Number(id))
            .filter((id) => Number.isFinite(id))
        : [],
    [mod?.sourceDownloadIds],
  );
  const [downloadEntries, setDownloadEntries] = useState<DownloadEntry[]>([]);
  const [activeByDownload, setActiveByDownload] = useState<
    Record<number, string[]>
  >({});
  const [pakStatusByDownload, setPakStatusByDownload] = useState<
    Record<number, Record<string, ApiPakVersionStatus>>
  >({});
  const [deletingDownloadId, setDeletingDownloadId] = useState<number | null>(
    null,
  );
  const [deleteDialogEntry, setDeleteDialogEntry] =
    useState<DownloadEntry | null>(null);

  // Images state
  const [modImages, setModImages] = useState<ModImage[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [isUploadingImages, setIsUploadingImages] = useState(false);

  // Description editing state
  const [isEditingDescription, setIsEditingDescription] = useState(false);
  const [editDescriptionValue, setEditDescriptionValue] = useState("");
  const [isSavingDescription, setIsSavingDescription] = useState(false);
  const [isBBCodeMode, setIsBBCodeMode] = useState(false);

  // Custom preset that includes standard HTML5 tags + size, font, alignment

  const overviewTags = useMemo(() => {
    const tags: string[] = [];
    const seen = new Set<string>();
    const addTag = (tag?: string | null) => {
      if (tag == null) return;
      const normalized = String(tag).trim();
      if (!normalized) return;
      const lower = normalized.toLowerCase();
      if (seen.has(lower)) return;
      seen.add(lower);
      tags.push(normalized);
    };

    if (Array.isArray(details?.tags)) {
      details?.tags.forEach(addTag);
    }
    if (Array.isArray(mod?.tags)) {
      mod?.tags.forEach(addTag);
    }
    return tags;
  }, [details, mod]);

  useEffect(() => {
    let cancelled = false;
    async function loadDetails() {
      // Allow fetching images if we have an effective ID (even synthetic)
      if (!effectiveModId) {
        setDetails(null);
        setChangelogs(null);
        setModImages([]);
        return;
      }
      try {
        const promises: Promise<any>[] = [];
        // Use effectiveModId for fetching details (works for both Nexus and local mods)
        if (effectiveModId) {
          promises.push(getModDetails(effectiveModId));
          promises.push(getModChangelogs(effectiveModId));
        } else {
          promises.push(Promise.resolve(null));
          promises.push(Promise.resolve(null));
        }
        promises.push(fetchModImages(effectiveModId));

        const [d, c, images] = await Promise.all(promises);
        const debugInfo = {
          hasDescription: !!d?.mod?.description,
          descriptionLength: d?.mod?.description?.length || 0,
          hasSummary: !!d?.mod?.summary,
          changelogsCount: c?.length || 0,
          imagesCount: images?.length || 0,
          modKeys: d?.mod ? Object.keys(d.mod) : [],
        };
        console.log(
          "[ModModal] Loaded details for mod",
          serverModId,
          debugInfo,
        );
        try {
          await fetch("http://127.0.0.1:8000/api/debug/log", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              message: `ModModal loaded details for mod ${serverModId}`,
              data: debugInfo,
              level: "INFO",
            }),
          });
        } catch (e) {
          // Ignore debug logging errors
        }
        if (!cancelled) {
          setDetails(d);
          setChangelogs(c);
          setModImages(images || []);
        }
      } catch (error) {
        console.error("[ModModal] Error loading details:", error);
        if (!cancelled) {
          setDetails(null);
          setChangelogs(null);
          setModImages([]);
        }
      }
    }
    loadDetails();
    return () => {
      cancelled = true;
    };
  }, [serverModId, effectiveModId, isOpen]);

  const hydrateDownloads = useCallback(
    async (options?: { skipScan?: boolean }) => {
      if (!downloadIds.length) {
        return [] as DownloadEntry[];
      }
      if (!options?.skipScan) {
        try {
          await scanActive();
        } catch (error) {
          console.warn("[mod-modal] scanActive failed", error);
        }
      }
      const downloads = await Promise.all(
        downloadIds.map(async (rawId) => {
          try {
            const dl = await getLocalDownload(Number(rawId));
            return {
              id: dl.id,
              path: dl.path,
              contents: Array.isArray(dl.contents) ? dl.contents : [],
              active_paks: Array.isArray(dl.active_paks) ? dl.active_paks : [],
              version:
                dl.version ??
                mod?.installedVersion ??
                mod?.version ??
                mod?.latestVersion ??
                null,
              created_at: dl.created_at ?? null,
              name: dl.name ?? null,
            } as DownloadEntry;
          } catch (error) {
            console.warn(
              "[mod-modal] failed to fetch local download",
              rawId,
              error,
            );
            return null;
          }
        }),
      );
      const valid = downloads.filter((d): d is DownloadEntry => Boolean(d));
      const idOrder = new Map<number, number>();
      downloadIds.forEach((rawId, index) => {
        const asNumber = Number(rawId);
        if (Number.isFinite(asNumber)) {
          idOrder.set(asNumber, index);
        }
      });
      valid.sort((a, b) => {
        const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
        const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
        if (aTime !== bTime) {
          return bTime - aTime;
        }
        const aIndex = idOrder.get(a.id) ?? Number.MAX_SAFE_INTEGER;
        const bIndex = idOrder.get(b.id) ?? Number.MAX_SAFE_INTEGER;
        if (aIndex !== bIndex) {
          return aIndex - bIndex;
        }
        return a.id - b.id;
      });
      return valid;
    },
    [downloadIds, mod?.installedVersion, mod?.version, mod?.latestVersion],
  );

  const fetchPakStatuses = useCallback(async () => {
    if (!isOpen) {
      return {} as Record<number, Record<string, ApiPakVersionStatus>>;
    }
    const request: {
      modId?: number;
      downloadIds?: number[];
    } = {};
    if (serverModId != null) {
      request.modId = serverModId;
    }
    if (downloadIds.length > 0) {
      request.downloadIds = downloadIds;
    }
    if (!request.modId && !request.downloadIds) {
      return {} as Record<number, Record<string, ApiPakVersionStatus>>;
    }
    try {
      const response = await getPakVersionStatus(request);
      const lookup: Record<number, Record<string, ApiPakVersionStatus>> = {};
      for (const entry of response) {
        const downloadId = entry.local_download_id;
        const pakKey = toBasename(entry.pak_name || "").toLowerCase();
        if (!downloadId || !pakKey) {
          continue;
        }
        if (!lookup[downloadId]) {
          lookup[downloadId] = {};
        }
        lookup[downloadId][pakKey] = entry;
      }
      return lookup;
    } catch (error) {
      console.warn("[mod-modal] failed to fetch pak version status", error);
      return {} as Record<number, Record<string, ApiPakVersionStatus>>;
    }
  }, [downloadIds, isOpen, serverModId]);

  useEffect(() => {
    let cancelled = false;
    async function loadDownloads() {
      if (!mod || !downloadIds.length) {
        setDownloadEntries([]);
        setActiveByDownload({});
        setPakStatusByDownload({});
        return;
      }
      const entries = await hydrateDownloads();
      if (!cancelled) {
        setDownloadEntries(entries);
        setActiveByDownload(toActiveMap(entries));
      }
    }
    loadDownloads();
    return () => {
      cancelled = true;
    };
  }, [hydrateDownloads, isOpen, mod, downloadIds.length]);

  useEffect(() => {
    let cancelled = false;
    async function loadStatuses() {
      const lookup = await fetchPakStatuses();
      if (!cancelled) {
        setPakStatusByDownload(lookup);
      }
    }
    loadStatuses();
    return () => {
      cancelled = true;
    };
  }, [fetchPakStatuses]);

  useEffect(() => {
    let cancelled = false;
    async function loadPakAssets() {
      if (!mod || !downloadIds.length) {
        setPakAssets([]);
        return;
      }
      try {
        const assets = await getPakAssets(downloadIds);
        if (!cancelled) {
          setPakAssets(assets);
        }
      } catch (error) {
        console.error("[ModModal] Error loading pak assets:", error);
        if (!cancelled) {
          setPakAssets([]);
        }
      }
    }
    loadPakAssets();
    return () => {
      cancelled = true;
    };
  }, [downloadIds, isOpen, mod]);

  const downloadSections = useMemo(
    () =>
      downloadEntries.map((entry) => ({
        entry,
        groups: groupPakEntries(entry.contents),
      })),
    [downloadEntries],
  );

  const handleToggle = useCallback(
    async (downloadId: number, files: string[], willCheck: boolean) => {
      const toastId = `apply-toggle-${downloadId}`;
      let statusLookup: Record<
        number,
        Record<string, ApiPakVersionStatus>
      > | null = null;
      let appliedSuccessfully = false;
      try {
        setIsApplying(true);
        const current = new Set<string>(activeByDownload[downloadId] || []);
        if (willCheck) {
          // Remove any same-basename variants already in the set
          // (only one variant can be active in ~mods since they share the same filename)
          const incomingBases = new Set(files.map((f) => toBasename(f)));
          for (const existing of [...current]) {
            if (
              incomingBases.has(toBasename(existing)) &&
              !files.includes(existing)
            ) {
              current.delete(existing);
            }
          }
          files.forEach((file) => current.add(file));
        } else {
          files.forEach((file) => current.delete(file));
        }
        const activeList = Array.from(current);
        const basenameTargets = new Set(files.map((file) => toBasename(file)));

        // Optimistically reflect the toggle state so the UI stays in sync while the request runs
        setActiveByDownload((prev) => {
          const next: Record<number, string[]> = {
            ...prev,
            [downloadId]: activeList,
          };
          if (willCheck && basenameTargets.size > 0) {
            for (const [key, value] of Object.entries(prev)) {
              const otherId = Number(key);
              if (otherId === downloadId) continue;
              if (!Array.isArray(value) || value.length === 0) continue;
              const filtered = value.filter(
                (name) => !basenameTargets.has(toBasename(name)),
              );
              if (filtered.length !== value.length) {
                next[otherId] = filtered;
              }
            }
          }
          return next;
        });

        setDownloadEntries((prev) =>
          prev.map((entry) => {
            if (entry.id === downloadId) {
              return { ...entry, active_paks: activeList };
            }
            if (willCheck && basenameTargets.size > 0) {
              const prevActive = Array.isArray(entry.active_paks)
                ? entry.active_paks
                : [];
              if (prevActive.length === 0) return entry;
              const filtered = prevActive.filter(
                (name) => !basenameTargets.has(toBasename(name)),
              );
              if (filtered.length !== prevActive.length) {
                return { ...entry, active_paks: filtered };
              }
            }
            return entry;
          }),
        );

        toast.loading("Applying...", { id: toastId });
        await setActivePaks(Number(downloadId), activeList);
        appliedSuccessfully = true;
        await scanActive();
        try {
          await refreshConflicts();
        } catch (refreshError) {
          console.warn("[mod-modal] refreshConflicts failed", refreshError);
        }
        const refreshed = await hydrateDownloads({ skipScan: true });
        setDownloadEntries(refreshed);
        setActiveByDownload(toActiveMap(refreshed));
        statusLookup = await fetchPakStatuses();
        toast.success(willCheck ? "Activated file" : "Deactivated file", {
          id: toastId,
          duration: 2000,
        });
      } catch (error) {
        toast.error((error as any)?.message || "Failed to apply");
        try {
          const fallback = await hydrateDownloads();
          setDownloadEntries(fallback);
          setActiveByDownload(toActiveMap(fallback));
          statusLookup = await fetchPakStatuses();
        } catch (err) {
          console.error("[mod-modal] failed to rehydrate downloads", err);
        }
      } finally {
        if (statusLookup) {
          setPakStatusByDownload(statusLookup);
        }
        setIsApplying(false);
        if (appliedSuccessfully) {
          onConflictStateChanged?.();
          // Trigger parent refresh to update mod list
          onRefresh?.();
        }
      }
    },
    [
      activeByDownload,
      fetchPakStatuses,
      hydrateDownloads,
      onConflictStateChanged,
    ],
  );

  const handleDeleteDownload = useCallback(
    async (entry: DownloadEntry): Promise<boolean> => {
      if (!entry) {
        return false;
      }
      if (isApplying && deletingDownloadId == null) {
        toast.warning("Please wait for the current operation to finish.");
        return false;
      }
      if (deletingDownloadId != null && deletingDownloadId !== entry.id) {
        toast.warning("Please wait for the current deletion to finish.");
        return false;
      }

      const downloadId = entry.id;
      const displayName = getDownloadDisplayName(entry);
      const toastId = `delete-download-${downloadId}`;
      setDeletingDownloadId(downloadId);
      setIsApplying(true);

      let success = false;
      try {
        // Step 1: Deactivate all active paks first if any are active
        const activePaks =
          activeByDownload[downloadId] ?? entry.active_paks ?? [];
        if (activePaks.length > 0) {
          toast.loading(`Deactivating ${displayName}…`, { id: toastId });
          try {
            await setActivePaks(downloadId, []);
            await scanActive();
            // Update local state to reflect deactivation
            setActiveByDownload((prev) => ({
              ...prev,
              [downloadId]: [],
            }));
            setDownloadEntries((prev) =>
              prev.map((e) =>
                e.id === downloadId ? { ...e, active_paks: [] } : e,
              ),
            );
          } catch (deactivateError) {
            console.warn(
              "[mod-modal] Failed to deactivate paks before deletion",
              deactivateError,
            );
            // Continue with deletion even if deactivation fails
          }
        }

        // Step 2: Delete the mod
        toast.loading(`Deleting ${displayName}…`, { id: toastId });
        const backendModId =
          typeof mod?.backendModId === "number" &&
          Number.isFinite(mod.backendModId)
            ? mod.backendModId
            : undefined;
        await deleteLocalDownloads([downloadId], backendModId);
        await scanActive();
        try {
          await refreshConflicts();
        } catch (refreshError) {
          console.warn(
            "[mod-modal] refreshConflicts after delete failed",
            refreshError,
          );
        }
        const refreshed = await hydrateDownloads({ skipScan: true });
        setDownloadEntries(refreshed);
        setActiveByDownload(toActiveMap(refreshed));
        const lookup = await fetchPakStatuses();
        setPakStatusByDownload(lookup);
        toast.success(`Deleted ${displayName}`, {
          id: toastId,
          duration: 2000,
        });
        onConflictStateChanged?.();
        onRefresh?.();
        success = true;
      } catch (error) {
        const message =
          error instanceof Error && error.message
            ? error.message
            : String(error ?? "Unknown error");
        toast.error(`Failed to delete ${displayName}: ${message}`, {
          id: toastId,
          duration: 4000,
        });
      } finally {
        setDeletingDownloadId(null);
        setIsApplying(false);
      }
      return success;
    },
    [
      activeByDownload,
      deletingDownloadId,
      fetchPakStatuses,
      hydrateDownloads,
      isApplying,
      mod?.backendModId,
      onConflictStateChanged,
      onRefresh,
    ],
  );

  const handleDeleteDialogChange = useCallback(
    (open: boolean) => {
      if (open) {
        return;
      }
      if (deletingDownloadId != null) {
        return;
      }
      setDeleteDialogEntry(null);
    },
    [deletingDownloadId],
  );

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteDialogEntry) {
      return;
    }
    const result = await handleDeleteDownload(deleteDialogEntry);
    if (result) {
      setDeleteDialogEntry(null);
    }
  }, [deleteDialogEntry, handleDeleteDownload]);

  // Image handlers
  const handleImageUpload = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files;
      if (!files || files.length === 0 || !effectiveModId) return;

      setIsUploadingImages(true);
      const toastId = toast.loading(`Uploading ${files.length} image(s)...`);

      try {
        const fileArray = Array.from(files);
        await uploadModImages(effectiveModId, fileArray);

        // Refresh images
        const updatedImages = await fetchModImages(effectiveModId);
        setModImages(updatedImages);

        toast.success(`Uploaded ${files.length} image(s) successfully`, {
          id: toastId,
          duration: 2000,
        });

        // Refresh the mod list to update the card image
        if (onRefresh) {
          onRefresh();
        }
      } catch (error) {
        toast.error((error as any)?.message || "Failed to upload images", {
          id: toastId,
          duration: 4000,
        });
      } finally {
        setIsUploadingImages(false);
        // Reset file input
        event.target.value = "";
      }
    },
    [effectiveModId, onRefresh],
  );

  const openLightbox = useCallback((index: number) => {
    setLightboxIndex(index);
    setLightboxOpen(true);
  }, []);

  const closeLightbox = useCallback(() => {
    setLightboxOpen(false);
  }, []);

  const nextImage = useCallback(() => {
    setLightboxIndex((prev) => (prev + 1) % modImages.length);
  }, [modImages.length]);

  const prevImage = useCallback(() => {
    setLightboxIndex(
      (prev) => (prev - 1 + modImages.length) % modImages.length,
    );
  }, [modImages.length]);

  // Keyboard navigation for lightbox
  useEffect(() => {
    if (!lightboxOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeLightbox();
      } else if (e.key === "ArrowRight") {
        nextImage();
      } else if (e.key === "ArrowLeft") {
        prevImage();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [lightboxOpen, closeLightbox, nextImage, prevImage]);

  const handleDeleteImage = useCallback(
    async (imageId: number, event: React.MouseEvent) => {
      event.stopPropagation(); // Prevent opening lightbox

      const toastId = toast.loading("Deleting image...");
      try {
        await deleteModImage(imageId);

        // Update local state
        setModImages((prev) => prev.filter((img) => img.id !== imageId));

        toast.success("Image deleted successfully", {
          id: toastId,
          duration: 2000,
        });

        // Refresh the mod list to update the card image
        if (onRefresh) {
          onRefresh();
        }
      } catch (error) {
        toast.error((error as any)?.message || "Failed to delete image", {
          id: toastId,
          duration: 4000,
        });
      }
    },
    [onRefresh],
  );

  const handleEditDescription = useCallback(() => {
    if (!mod && !details?.mod) return;
    const current =
      details?.mod?.description_bbcode || details?.mod?.description || "";
    setEditDescriptionValue(current);
    setIsEditingDescription(true);
    // Auto-detect if it looks like BBCode or if we have explicit BBCode content
    if (
      details?.mod?.description_bbcode ||
      /\[(b|i|u|url|img|color|size|font|center|quote)/i.test(current)
    ) {
      setIsBBCodeMode(true);
    } else {
      setIsBBCodeMode(false);
    }
  }, [mod, details]);

  const handleSaveDescription = useCallback(async () => {
    if (!effectiveModId) return;
    setIsSavingDescription(true);
    try {
      await updateModDetails(effectiveModId, {
        description: editDescriptionValue,
      });

      // Refetch details from server to ensure UI matches what was actually saved
      // (backend does HTML escaping and newline conversion)
      const freshDetails = await getModDetails(effectiveModId);
      setDetails(freshDetails);

      setIsEditingDescription(false);
      toast.success("Description updated");
    } catch (e) {
      console.error("Failed to save description", e);
      toast.error("Failed to save description");
    } finally {
      setIsSavingDescription(false);
    }
  }, [effectiveModId, editDescriptionValue]);

  const handleCancelEditDescription = useCallback(() => {
    setIsEditingDescription(false);
    setEditDescriptionValue("");
  }, []);

  if (!mod) return null;

  const formatNumber = (num: number) => {
    if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
    if (num >= 1000) return (num / 1000).toFixed(1) + "K";
    return num.toString();
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  };

  // Stronger client-side sanitization using DOMPurify
  // Allow img tags and necessary attributes for BBCode-generated HTML
  // usage of USE_PROFILES: { html: true } with ADD_TAGS is the recommended way to extend defaults
  const sanitizeHtml = (html: string) =>
    DOMPurify.sanitize(html, {
      USE_PROFILES: { html: true },
      ADD_TAGS: ["img"],
      ADD_ATTR: ["target"],
    });

  const resolvedChangelogs: ApiChangelog[] = changelogs ?? [];

  const toChangelogHtml = (value?: string | null): string => {
    if (typeof value !== "string") {
      return "";
    }
    const trimmed = value.trim();
    if (!trimmed) {
      return "";
    }
    const hasBreakTag = /<\s*br\s*\/?\s*>/i.test(trimmed);
    const normalized = hasBreakTag
      ? trimmed
      : trimmed.replace(/\r?\n/g, "<br />");
    return sanitizeHtml(normalized);
  };

  const pendingDeleteLabel = deleteDialogEntry
    ? getDownloadDisplayName(deleteDialogEntry)
    : "";
  const pendingDeletePath = deleteDialogEntry?.path ?? "";
  const isDeletingSelectedEntry =
    deleteDialogEntry != null && deletingDownloadId === deleteDialogEntry.id;

  // Compute if any pak files are currently activated across all downloads
  const hasAnyActivePaks = useMemo(() => {
    return Object.values(activeByDownload).some(
      (activePaks) => Array.isArray(activePaks) && activePaks.length > 0,
    );
  }, [activeByDownload]);

  // Comments tab removed per request

  // Note: we rely on local download contents for toggling, not Nexus file list.

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent
        className="!w-[1200px] !max-w-[95vw] !sm:max-w-[1200px] !md:max-w-[1200px] !lg:max-w-[1200px] !xl:max-w-[1200px] !h-[90vh] !max-h-[90vh] p-0 !flex !flex-col overflow-hidden !grid-none"
        style={{
          width: "1200px",
          maxWidth: "95vw",
          height: "90vh",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
        }}
        aria-describedby="mod-dialog-description"
        showCloseButton={false}
      >
        <div className="flex flex-col h-full min-h-0 overflow-hidden">
          {/* Fixed save/cancel buttons for description editing */}
          {isEditingDescription && (
            <div
              style={{
                position: "absolute",
                bottom: "92px",
                left: "24px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
                backgroundColor: "rgba(0, 0, 0, 0.95)",
                backdropFilter: "blur(4px)",
                padding: "8px",
                borderRadius: "6px",
                border: "1px solid rgba(255, 255, 255, 0.1)",
                boxShadow: "0 4px 6px rgba(0, 0, 0, 0.3)",
                zIndex: 100,
              }}
            >
              <Button
                variant="ghost"
                size="icon"
                onClick={handleCancelEditDescription}
                disabled={isSavingDescription}
                title="Cancel"
                className="h-8 w-8 hover:bg-destructive/10 hover:text-destructive"
              >
                <XIcon className="w-4 h-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleSaveDescription}
                disabled={isSavingDescription}
                title="Save"
                className="h-8 w-8 hover:bg-green-500/10 hover:text-green-600"
              >
                {isSavingDescription ? (
                  <div className="w-4 h-4 border-2 border-current border-t-transparent animate-spin rounded-full" />
                ) : (
                  <Check className="w-4 h-4 text-green-500" />
                )}
              </Button>
            </div>
          )}
          {/* Hidden description for accessibility to satisfy aria-describedby */}
          <p id="mod-dialog-description" className="sr-only">
            Manage and apply mod files for {mod?.name}.
          </p>
          {/* Header */}
          <DialogHeader className="p-6 pb-4 flex-shrink-0">
            <div className="flex items-start gap-4">
              <div className="w-24 h-24 bg-muted rounded-lg overflow-hidden flex-shrink-0">
                <img
                  src={details?.mod?.picture_url || mod.images[0]}
                  alt={mod.name}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    const fallback =
                      "https://i.pinimg.com/1200x/44/da/5e/44da5e6d9dd75cb753ab5925aff4ce4c.jpg";
                    if (e.currentTarget.src !== fallback) {
                      e.currentTarget.src = fallback;
                    }
                  }}
                />
              </div>

              <div className="flex-1 min-w-0">
                <DialogTitle className="text-2xl mb-2">{mod.name}</DialogTitle>
                <p className="text-muted-foreground mb-3">
                  {details?.mod?.summary || mod.description}
                </p>

                <div className="flex items-center gap-3 mb-3">
                  {mod.backendModId != null && mod.backendModId > 0 && (
                    <a
                      className="flex items-center gap-2 cursor-pointer"
                      onClick={async () => {
                        const modUrl = `https://next.nexusmods.com/profile/${
                          details?.mod?.author || mod.author || "unknown"
                        }`;
                        try {
                          const { openInBrowser } =
                            await import("../lib/tauri-utils");
                          await openInBrowser(modUrl);
                        } catch (error) {
                          console.error("Failed to open mod page:", error);
                        }
                      }}
                    >
                      <Avatar className="w-6 h-6">
                        <AvatarImage
                          src={mod.authorAvatar || undefined}
                          alt={mod.author || "Unknown author"}
                          referrerPolicy="no-referrer"
                          onError={(
                            event: SyntheticEvent<HTMLImageElement>,
                          ) => {
                            const img = event.currentTarget;
                            if (img.dataset.fallbackApplied === "1") {
                              return;
                            }
                            img.dataset.fallbackApplied = "1";
                            img.src = "";
                          }}
                        />
                        <AvatarFallback className="text-xs">
                          {(mod.author?.trim()?.[0] ?? "?").toUpperCase()}
                        </AvatarFallback>
                      </Avatar>
                      <span className="font-medium">
                        {details?.mod?.author || mod.author || "Unknown author"}
                      </span>
                    </a>
                  )}
                  {mod.categoryTags && mod.categoryTags.length > 0 && (
                    <div className="flex gap-1 flex-wrap">
                      {mod.categoryTags.map((tag) => (
                        <Badge
                          key={`modal-category-${tag}`}
                          variant="secondary"
                        >
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  )}
                  {serverModId && (
                    <div className="flex gap-1 flex-wrap bg-muted rounded-md px-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={async () => {
                          const modUrl = `https://www.nexusmods.com/marvelrivals/mods/${serverModId}`;
                          try {
                            const { openInBrowser } =
                              await import("../lib/tauri-utils");
                            await openInBrowser(modUrl);
                          } catch (error) {
                            console.error("Failed to open mod page:", error);
                          }
                        }}
                        className="h-6 w-6"
                        title="View on Nexus Mods"
                      >
                        <ExternalLink className="w-4 h-4" />
                      </Button>
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-6 text-sm text-muted-foreground">
                  {mod.backendModId != null && mod.backendModId > 0 && (
                    <>
                      <div className="flex items-center gap-1">
                        <Download className="w-6 h-4" />
                        {formatNumber(
                          (details?.mod?.mod_downloads as number | null) ??
                            mod.downloads ??
                            0,
                        )}{" "}
                        downloads
                      </div>
                      <div className="flex items-center gap-1">
                        <Star className="w-6 h-4 fill-yellow-400 text-yellow-400" />
                        {details?.mod?.endorsement_count != null
                          ? `${details.mod.endorsement_count} endorsements`
                          : `${mod.rating.toFixed(1)} rating`}
                      </div>
                    </>
                  )}
                  <div className="flex items-center gap-1">
                    <Calendar className="w-6 h-4" />
                    Updated{" "}
                    {formatDate(
                      details?.latest_file?.uploaded_at || mod.lastUpdated,
                    )}
                  </div>
                </div>
              </div>

              <div className="flex flex-col gap-2">
                <Button
                  variant={hasAnyActivePaks ? "secondary" : "default"}
                  onClick={() => onInstall(mod.id)}
                  className="gap-2"
                >
                  <Download className="w-4 h-4" />
                  {hasAnyActivePaks ? "Installed" : "Not Installed"}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => onFavorite(mod.id)}
                  className={`gap-2 ${mod.isFavorited ? "text-red-500" : ""}`}
                >
                  <Heart
                    className={`w-4 h-4 ${
                      mod.isFavorited ? "fill-current" : ""
                    }`}
                  />
                  {mod.isFavorited ? "Favorited" : "Add to Favorites"}
                </Button>
              </div>
            </div>
          </DialogHeader>

          <Separator className="flex-shrink-0" />

          {/* Content */}
          <div
            className="flex-1 min-h-0 overflow-hidden"
            style={{ height: "calc(100% - 200px)" }}
          >
            <Tabs
              defaultValue="overview"
              className="h-full flex flex-col overflow-hidden"
              style={{ height: "100%" }}
            >
              <TabsList className="mx-6 mt-4 mb-0 flex-shrink-0">
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="images">Images</TabsTrigger>
                <TabsTrigger value="files">Files</TabsTrigger>
                <TabsTrigger value="assets">Assets</TabsTrigger>
                <TabsTrigger value="changelog">Changelog</TabsTrigger>
              </TabsList>

              <div
                className="flex-1 min-h-0 overflow-hidden"
                style={{ height: "calc(100% - 52px)" }}
              >
                <TabsContent
                  value="overview"
                  className="!h-full m-0 data-[state=active]:!flex data-[state=active]:!flex-col data-[state=active]:!h-full overflow-hidden"
                  style={{ height: "100%" }}
                >
                  <ScrollArea
                    className="flex-1 min-h-0 overflow-auto"
                    style={{ height: "100%" }}
                  >
                    <div className="px-6 py-4">
                      <div className="space-y-6">
                        <div>
                          <h3 className="font-medium mb-3">Tags</h3>
                          {overviewTags.length > 0 ? (
                            <div className="flex flex-wrap gap-2">
                              {overviewTags.map((tag) => (
                                <Badge
                                  key={`overview-tag-${tag}`}
                                  variant="secondary"
                                  className="text-xs"
                                >
                                  {tag}
                                </Badge>
                              ))}
                            </div>
                          ) : (
                            <p className="text-sm text-muted-foreground italic">
                              No tags available for this mod.
                            </p>
                          )}
                        </div>

                        {/* Description */}
                        <div>
                          <div className="flex items-center justify-between mb-3">
                            <h3 className="font-medium">Description</h3>
                            <div className="flex gap-2">
                              {effectiveModId && !isEditingDescription && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-6 w-6"
                                  onClick={handleEditDescription}
                                  title="Edit Description"
                                >
                                  <Pencil className="w-3.5 h-3.5 text-muted-foreground" />
                                </Button>
                              )}
                              {isEditingDescription && (
                                <Button
                                  variant={isBBCodeMode ? "default" : "outline"}
                                  size="sm"
                                  onClick={() => setIsBBCodeMode(!isBBCodeMode)}
                                  title="Toggle BBCode Mode"
                                  className="h-6 text-xs"
                                >
                                  BBCode
                                </Button>
                              )}
                            </div>
                          </div>

                          {isEditingDescription ? (
                            <Textarea
                              value={editDescriptionValue}
                              onChange={(e) =>
                                setEditDescriptionValue(e.target.value)
                              }
                              // Use style to force height as requested ("traditional css")
                              style={{ height: "280px", minHeight: "280px" }}
                              className="font-sans resize-y"
                              placeholder={
                                isBBCodeMode
                                  ? "Enter description in BBCode format..."
                                  : "Enter mod description..."
                              }
                            />
                          ) : (
                            <div className="prose prose-sm max-w-none text-muted-foreground">
                              {details?.mod?.description &&
                              !details.mod.description.includes(
                                "Local mod (auto-generated)",
                              ) ? (
                                <div
                                  dangerouslySetInnerHTML={{
                                    __html: sanitizeHtml(
                                      details?.mod?.description || "",
                                    ),
                                  }}
                                />
                              ) : details?.mod?.summary ? (
                                <p>{details?.mod?.summary}</p>
                              ) : (
                                <p className="italic">
                                  No description available.
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </ScrollArea>
                </TabsContent>

                {/* Images Tab */}
                <TabsContent
                  value="images"
                  className="!h-full m-0 data-[state=active]:!flex data-[state=active]:!flex-col data-[state=active]:!h-full overflow-hidden"
                  style={{ height: "100%" }}
                >
                  <ScrollArea
                    className="flex-1 min-h-0 overflow-auto"
                    style={{ height: "100%" }}
                  >
                    <div className="px-6 py-4">
                      <div className="flex flex-wrap gap-4">
                        {/* Image thumbnails with 300px fixed height */}
                        {modImages.map((image, index) => (
                          <div
                            key={`img-${image.id}-${index}`}
                            style={{ height: "350px" }}
                            className="bg-muted rounded-lg overflow-hidden cursor-pointer hover:opacity-80 transition-opacity relative group"
                            onClick={() => openLightbox(index)}
                          >
                            <img
                              src={
                                image.source === "nexus"
                                  ? image.url
                                  : `data:${image.mimeType};base64,${image.data}`
                              }
                              alt={image.filename || `Image ${index + 1}`}
                              style={{ height: "100%", width: "auto" }}
                              className="object-contain"
                            />

                            {/* Delete button for custom images only */}
                            {image.source === "custom" && (
                              <button
                                className="absolute top-2 right-2 bg-destructive text-destructive-foreground rounded-full p-1 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-destructive/90"
                                onClick={(e) => handleDeleteImage(image.id, e)}
                                aria-label="Delete image"
                              >
                                <X className="w-4 h-4" />
                              </button>
                            )}
                          </div>
                        ))}

                        {/* Plus button for upload - enabled if we have ANY effective ID */}
                        {effectiveModId && (
                          <div
                            style={{ width: "350px", height: "350px" }}
                            className="bg-muted rounded-lg flex items-center justify-center cursor-pointer hover:bg-muted/70 transition-colors border-2 border-dashed border-border"
                          >
                            <label
                              htmlFor="image-upload"
                              className="cursor-pointer w-full h-full flex items-center justify-center"
                            >
                              <Plus className="w-12 h-12 text-muted-foreground" />
                              <input
                                id="image-upload"
                                type="file"
                                accept="image/*"
                                multiple
                                style={{ display: "none" }}
                                onChange={handleImageUpload}
                                disabled={isUploadingImages}
                              />
                            </label>
                          </div>
                        )}

                        {/* Empty state */}
                        {modImages.length === 0 && !effectiveModId && (
                          <p className="text-sm text-muted-foreground italic">
                            No images available for this mod.
                          </p>
                        )}
                      </div>
                    </div>
                  </ScrollArea>
                </TabsContent>

                {/* Lightbox Gallery */}
                {lightboxOpen && modImages.length > 0 && (
                  <div
                    className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center"
                    onClick={closeLightbox}
                  >
                    {/* Image counter - stays at top center of screen */}
                    <div className="absolute top-4 left-1/2 transform -translate-x-1/2 text-white text-lg font-medium z-10">
                      {lightboxIndex + 1} / {modImages.length}
                    </div>

                    {/* Image container - matches image dimensions */}
                    <div
                      className="relative inline-block"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {/* Close button - positioned on image */}
                      <button
                        className="absolute top-4 right-4 text-white hover:text-gray-300 transition-colors z-10"
                        onClick={closeLightbox}
                        aria-label="Close lightbox"
                      >
                        <X className="w-8 h-8" />
                      </button>

                      {/* Previous button - positioned on image */}
                      {modImages.length > 1 && (
                        <button
                          className="absolute left-4 top-1/2 -translate-y-1/2 text-white hover:text-gray-300 transition-colors z-10"
                          onClick={(e) => {
                            e.stopPropagation();
                            prevImage();
                          }}
                          aria-label="Previous image"
                        >
                          <ChevronLeft className="w-12 h-12" />
                        </button>
                      )}

                      {/* Image */}
                      <img
                        src={
                          modImages[lightboxIndex]?.source === "nexus"
                            ? modImages[lightboxIndex]?.url
                            : `data:${modImages[lightboxIndex]?.mimeType};base64,${modImages[lightboxIndex]?.data}`
                        }
                        alt={
                          modImages[lightboxIndex]?.filename ||
                          `Image ${lightboxIndex + 1}`
                        }
                        style={{ maxHeight: "80vh", width: "auto" }}
                        className="object-contain"
                      />

                      {/* Next button - positioned on image */}
                      {modImages.length > 1 && (
                        <button
                          className="absolute right-4 top-1/2 -translate-y-1/2 text-white hover:text-gray-300 transition-colors z-10"
                          onClick={(e) => {
                            e.stopPropagation();
                            nextImage();
                          }}
                          aria-label="Next image"
                        >
                          <ChevronRight className="w-12 h-12" />
                        </button>
                      )}
                    </div>
                  </div>
                )}

                <TabsContent
                  value="files"
                  className="!h-full m-0 data-[state=active]:!flex data-[state=active]:!flex-col data-[state=active]:!h-full overflow-hidden"
                  style={{ height: "100%" }}
                >
                  <ScrollArea
                    className="flex-1 min-h-0 overflow-auto"
                    style={{ height: "100%" }}
                  >
                    <div className="px-6 py-4">
                      <div className="space-y-4">
                        <div className="flex items-center gap-2 mb-4">
                          <h3 className="text-lg font-semibold flex items-center gap-2">
                            <File className="w-5 h-5" />
                            Pak Files
                          </h3>
                        </div>

                        <div className="space-y-4">
                          {downloadSections.length === 0 && (
                            <div className="text-sm text-muted-foreground">
                              No local downloads recorded for this mod yet.
                            </div>
                          )}
                          {downloadSections.map(({ entry, groups }) => {
                            const activeList =
                              activeByDownload[entry.id] ??
                              entry.active_paks ??
                              [];
                            const isActive = activeList.length > 0;
                            const lower = (entry.path || "").toLowerCase();
                            // Allow activation for archives, single .pak files, and folders (extracted mods)
                            const isArchive =
                              lower.endsWith(".zip") ||
                              lower.endsWith(".rar") ||
                              lower.endsWith(".7z");
                            const isSinglePak = lower.endsWith(".pak");
                            // Assume it's a folder if it doesn't have a recognized file extension
                            const isFolder = !isArchive && !isSinglePak;
                            const canApply =
                              isArchive || isSinglePak || isFolder;
                            const switchDisabled =
                              isApplying ||
                              deletingDownloadId === entry.id ||
                              !canApply;
                            const statusMap =
                              pakStatusByDownload[entry.id] ?? {};
                            const statusValues = Object.values(statusMap);
                            const displayVersion = normalizeVersion(
                              statusValues.find(
                                (status) =>
                                  status.display_version &&
                                  status.display_version.trim() !== "",
                              )?.display_version || entry.version,
                            );
                            const entryLabel = entry.name?.trim()
                              ? entry.name
                              : mod.name;
                            return (
                              <div
                                key={entry.id}
                                className={`border border-border rounded-xl p-4 space-y-3 transition-colors ${
                                  isActive
                                    ? "bg-green-50 dark:bg-green-950/40"
                                    : "bg-background"
                                }`}
                              >
                                <div className="flex items-center justify-between gap-3 flex-wrap">
                                  <div className="flex items-center gap-3 min-w-0 flex-wrap">
                                    <h4 className="font-semibold text-base truncate">
                                      {entryLabel}
                                    </h4>
                                    <Badge
                                      variant="outline"
                                      className="text-xs"
                                    >
                                      Version {displayVersion}
                                    </Badge>
                                    <Badge
                                      variant="outline"
                                      className={`text-xs ${
                                        isActive
                                          ? "border-green-600 text-green-600"
                                          : "text-muted-foreground"
                                      }`}
                                    >
                                      {isActive ? "Active" : "Inactive"}
                                    </Badge>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      onClick={() => {
                                        if (
                                          isApplying ||
                                          deletingDownloadId != null
                                        ) {
                                          return;
                                        }
                                        setDeleteDialogEntry(entry);
                                      }}
                                      disabled={
                                        isApplying ||
                                        deletingDownloadId === entry.id
                                      }
                                      className="text-destructive hover:text-destructive hover:bg-destructive/10"
                                      aria-label={`Delete ${entryLabel}`}
                                    >
                                      <Trash2 className="w-4 h-4" />
                                    </Button>
                                  </div>
                                </div>

                                <div className="space-y-2">
                                  {groups.length === 0 && (
                                    <div className="text-sm text-muted-foreground">
                                      No .pak files recorded for this download.
                                    </div>
                                  )}
                                  {(() => {
                                    const tree = buildFileTree(groups);
                                    // Check if any group has subfolder structure
                                    const hasSubfolders = groups.some((g) =>
                                      g.primary
                                        .replace(/\\/g, "/")
                                        .includes("/"),
                                    );

                                    if (!hasSubfolders) {
                                      // Simple flat list for mods without subfolders
                                      return groups.map(
                                        ({ primary, files }) => {
                                          const checked = files.some((file) =>
                                            activeList.includes(file),
                                          );
                                          return (
                                            <div
                                              key={`${entry.id}-${primary}`}
                                              className={`border border-border rounded-lg transition-colors ${
                                                checked
                                                  ? "bg-green-100 dark:bg-green-900/60"
                                                  : "bg-popover"
                                              }`}
                                              style={{ padding: "6px" }}
                                            >
                                              <div className="flex items-center justify-between gap-4">
                                                <div className="flex items-center gap-3 min-w-0">
                                                  <File className="w-4 h-4 text-muted-foreground shrink-0" />
                                                  <div className="font-medium truncate">
                                                    {primary}
                                                  </div>
                                                </div>
                                                <Switch
                                                  disabled={switchDisabled}
                                                  checked={checked}
                                                  onCheckedChange={(
                                                    willCheck: boolean,
                                                  ) =>
                                                    handleToggle(
                                                      entry.id,
                                                      files,
                                                      willCheck,
                                                    )
                                                  }
                                                />
                                              </div>
                                            </div>
                                          );
                                        },
                                      );
                                    }

                                    // Hierarchical tree rendering for mods with subfolders
                                    return (
                                      <FileTreeRenderer
                                        nodes={tree.children}
                                        depth={0}
                                        entryId={entry.id}
                                        activeList={activeList}
                                        switchDisabled={switchDisabled}
                                        handleToggle={handleToggle}
                                      />
                                    );
                                  })()}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  </ScrollArea>
                </TabsContent>

                <TabsContent
                  value="assets"
                  className="!h-full m-0 data-[state=active]:!flex data-[state=active]:!flex-col data-[state=active]:!h-full overflow-hidden"
                  style={{ height: "100%" }}
                >
                  <ScrollArea
                    className="flex-1 min-h-0 overflow-auto"
                    style={{ height: "100%" }}
                  >
                    <div className="px-6 py-4">
                      <div className="space-y-4">
                        <div className="flex items-center gap-2 mb-4">
                          <h3 className="text-lg font-semibold flex items-center gap-2">
                            <File className="w-5 h-5" />
                            Assets
                          </h3>
                        </div>

                        {pakAssets.length === 0 && (
                          <div className="text-sm text-muted-foreground">
                            No assets found for this mod.
                          </div>
                        )}

                        {pakAssets.map((pakAsset) => (
                          <div
                            key={pakAsset.pak_name}
                            className="border border-border rounded-xl p-4 space-y-3 bg-background"
                          >
                            <h4 className="font-semibold text-base">
                              {pakAsset.pak_name}
                            </h4>
                            <div className="space-y-1">
                              {pakAsset.assets.length === 0 && (
                                <div className="text-sm text-muted-foreground italic">
                                  No assets in this pak
                                </div>
                              )}
                              {pakAsset.assets.map((asset, index) => (
                                <div
                                  key={`${pakAsset.pak_name}-${index}`}
                                  className="text-sm font-mono text-muted-foreground"
                                >
                                  {asset}
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </ScrollArea>
                </TabsContent>

                <TabsContent
                  value="changelog"
                  className="!h-full m-0 data-[state=active]:!flex data-[state=active]:!flex-col data-[state=active]:!h-full overflow-hidden"
                  style={{ height: "100%" }}
                >
                  <ScrollArea
                    className="flex-1 min-h-0 overflow-auto"
                    style={{ height: "100%" }}
                  >
                    <div className="px-6 py-4">
                      <div className="space-y-4">
                        {resolvedChangelogs.map((version) => {
                          const changelogHtml = toChangelogHtml(
                            version.changelog,
                          );
                          return (
                            <div
                              key={`${version.version}-${version.uploaded_at}`}
                              className="border-l-2 border-muted pl-4"
                            >
                              <div className="flex items-center gap-2 mb-2">
                                <h4 className="font-medium">
                                  Version {version.version}
                                </h4>
                                <Badge variant="outline" className="text-xs">
                                  {version.uploaded_at
                                    ? formatDate(version.uploaded_at)
                                    : ""}
                                </Badge>
                              </div>
                              {changelogHtml ? (
                                <div
                                  className="text-sm text-muted-foreground"
                                  dangerouslySetInnerHTML={{
                                    __html: changelogHtml,
                                  }}
                                />
                              ) : (
                                <div className="text-sm text-muted-foreground italic">
                                  No changelog details provided.
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </ScrollArea>
                </TabsContent>

                {/* Comments tab removed per request */}
              </div>
            </Tabs>
          </div>
        </div>
        <div className="flex justify-end px-6 pb-6 pt-2">
          <DialogClose asChild>
            <Button
              variant="default"
              className="px-2 text-base font-semibold shadow-sm"
            >
              Close
            </Button>
          </DialogClose>
        </div>

        <AlertDialog
          open={deleteDialogEntry != null}
          onOpenChange={handleDeleteDialogChange}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                Delete {pendingDeleteLabel || "this download"}?
              </AlertDialogTitle>
              <AlertDialogDescription asChild>
                <div className="space-y-2 text-sm">
                  <p>
                    This removes the archive or folder from disk and deletes its
                    entry from the RivalNxt database. This action cannot be
                    undone.
                  </p>
                  {pendingDeletePath ? (
                    <p className="text-muted-foreground break-all text-xs">
                      {pendingDeletePath}
                    </p>
                  ) : null}
                </div>
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={isDeletingSelectedEntry}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                onClick={handleConfirmDelete}
                disabled={isDeletingSelectedEntry}
              >
                {isDeletingSelectedEntry ? "Deleting..." : "Delete"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  );
}
