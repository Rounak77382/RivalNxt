import React, { useMemo } from "react";
import type { SyntheticEvent } from "react";
import { Card, CardContent } from "./ui/card";
import { Button } from "./ui/button";
// Badge is used by TagList; not needed directly here
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";
import { Download, Star, Eye, Heart, FolderOpen, Power, RefreshCw, Ban } from "lucide-react";
import TagList from "./TagList";
import { useNsfwFilter } from "./NSFWFilterProvider";
import { LazyLoad } from "./LazyLoad";
import { Skeleton } from "./ui/skeleton";

export interface Mod {
  id: string;
  backendModId?: number | null; // server-side mods.mod_id if available
  // Aggregated local download ids that belong to this mod card (used for activation toggles)
  sourceDownloadIds?: number[];
  // Aggregated Nexus file ids that belong to this mod card (used to map collection variants)
  sourceFileIds?: number[];
  // Aggregated raw download paths that belong to this mod card
  sourcePaths?: string[];
  // Aggregated active paks across the grouped downloads (used to seed UI)
  defaultActivePaks?: string[];
  name: string;
  description: string;
  author: string;
  authorAvatar?: string;
  authorMemberId?: number;
  authorProfileUrl?: string;
  category: string;
  categoryTags?: string[];
  character?: string; // New field for character filtering
  tags: string[];
  downloads: number;
  rating: number;
  images: string[];
  version: string;
  lastUpdated: string;
  lastUpdatedRaw?: string | null;
  releaseDate?: string | null;
  hasInstallDate?: boolean;
  hasUpdateTimestamp?: boolean;
  isInstalled?: boolean;
  isFavorited?: boolean;
  hasUpdate?: boolean;
  installedVersion?: string;
  latestVersion?: string;
  latestVersionKey?: string | null;
  localVersionKey?: string | null;
  latestUploadedAt?: string | null;
  latestFileId?: number | null;
  latestFileName?: string | null;
  installDate?: string | null;
  isActive?: boolean; // New field for active/inactive status
  contents?: string[];
  performanceImpact?: number; // 1-5 scale for performance impact
  needsUpdate?: boolean;
  isUpdating?: boolean;
  updateError?: string | null;
  containsAdultContent?: boolean;
  size?: string; // Optional display size
  hideMetrics?: boolean; // Hide downloads and rating (e.g., for collections)
  collectionVariantsCount?: number;
  collectionDownloadedCount?: number;
  collectionVariants?: any[];
  isDownloading?: boolean;
  downloadProgress?: number;
  isFailed?: boolean;
  failureReason?: string;
  isIncompatible?: boolean; // true when the mod has no .pak files (incompatible with this app)
}

interface ModCardProps {
  mod: Mod;
  viewMode: "grid" | "list";
  onInstall: (modId: string) => void;
  onFavorite: (modId: string) => void;
  onView?: (mod: Mod) => void;
  onOpenFilesTab: (modId: string) => void;
  onToggleActive?: (modId: string) => void;
}

function ModCardInner({
  mod,
  viewMode,
  onInstall,
  onFavorite,
  onView,
  onToggleActive,
}: ModCardProps) {
  const formatVersion = (v: string) => {
    if (!v) return "";
    const parts = v.split(".");
    if (parts.length > 1) {
      const last = parts[parts.length - 1];
      // If last segment is 9+ digits, it's almost certainly a timestamp
      if (last.length >= 9 && /^\d+$/.test(last)) {
        return parts.slice(0, -1).join(".");
      }
    }
    if (v.length > 12) return v.substring(0, 10);
    return v;
  };

  // Setup multi-stage button resolution logic
  const hasMultiStage = !!onToggleActive;
  let actionLabel = mod.isInstalled ? "Installed" : "Download";
  let actionVariant: "default" | "secondary" | "outline" | "destructive" = mod.isInstalled ? "secondary" : "default";
  let actionClassName = "";
  let actionStyle: React.CSSProperties = {};
  let ActionIcon = Download;
  let handleActionClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onInstall(mod.id);
  };

  // If actively downloading
  if (mod.isDownloading) {
    const percent = mod.downloadProgress != null ? Math.round(mod.downloadProgress) : null;
    actionLabel = percent != null ? `Downloading (${percent}%)` : "Downloading...";
    actionVariant = "secondary";
    ActionIcon = RefreshCw;
    actionClassName = "cursor-wait";
    handleActionClick = (e: React.MouseEvent) => {
      e.stopPropagation();
    };
  } else if (mod.isIncompatible) {
    actionLabel = "Incompatible";
    actionVariant = "outline";
    ActionIcon = Ban;
    actionClassName = "cursor-not-allowed opacity-80";
    actionStyle = {
      color: "#f59e0b",
      backgroundColor: "rgba(245, 158, 11, 0.08)",
      borderColor: "rgba(245, 158, 11, 0.35)",
    };
    handleActionClick = (e: React.MouseEvent) => {
      e.stopPropagation();
      // no-op: incompatible mods can't be installed
    };
  } else if (mod.isFailed) {
    actionLabel = "Failed (Retry)";
    actionVariant = "destructive";
    ActionIcon = RefreshCw;
    actionClassName = "";
    handleActionClick = (e: React.MouseEvent) => {
      e.stopPropagation();
      onInstall(mod.id);
    };
  } else if (mod.collectionVariantsCount != null && mod.collectionVariantsCount >= 1) {
    // Handle fractional collection downloads
    if (mod.collectionDownloadedCount !== mod.collectionVariantsCount) {
      actionLabel = `Download (${mod.collectionDownloadedCount || 0}/${mod.collectionVariantsCount})`;
      actionVariant = "default";
      ActionIcon = Download;
      actionClassName = "";
      handleActionClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        if (onView) {
          onView(mod);
        } else {
          onInstall(mod.id);
        }
      };
    }
  }

  if (hasMultiStage && mod.isInstalled && !mod.isIncompatible && mod.collectionDownloadedCount === mod.collectionVariantsCount) {
    if (mod.isActive) {
      actionLabel = "Enabled";
      actionVariant = "outline";
      ActionIcon = Power;
      actionClassName = "hover:brightness-110 transition-all";
      actionStyle = {
        color: "#34d399",
        backgroundColor: "rgba(16, 185, 129, 0.1)",
        borderColor: "rgba(16, 185, 129, 0.3)",
      };
      handleActionClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        onToggleActive(mod.id);
      };
    } else {
      actionLabel = "Disabled";
      actionVariant = "outline";
      ActionIcon = Power;
      actionClassName = "hover:brightness-110 transition-all";
      actionStyle = {
        color: "#f87171",
        backgroundColor: "rgba(239, 68, 68, 0.1)",
        borderColor: "rgba(239, 68, 68, 0.3)",
      };
      handleActionClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        onToggleActive(mod.id);
      };
    }
  }
  // NSFW blur filter
  const { nsfwBlurEnabled } = useNsfwFilter();
  const shouldBlur = mod.containsAdultContent && nsfwBlurEnabled;

  // Debug logging for NSFW blur
  if (mod.containsAdultContent) {
    console.log("[ModCard] NSFW mod detected:", mod.name, {
      containsAdultContent: mod.containsAdultContent,
      nsfwBlurEnabled,
      shouldBlur,
    });
  }

  // Memoize computed tag display to avoid recalculating on every parent render
  // Tag rendering is delegated to `TagList` which will compute and re-render
  // itself when necessary (including on resize). This keeps heavy tag math
  // localized and avoids re-rendering the whole `ModCard`.

  const { avatarCandidates, authorAvatarSrc } = useMemo(() => {
    const fallbackAvatarSrc =
      mod.authorMemberId != null
        ? `https://avatars.nexusmods.com/${mod.authorMemberId}/100`
        : undefined;
    const pngAvatarSrc =
      mod.authorMemberId != null
        ? `https://avatars.nexusmods.com/${mod.authorMemberId}/100.png`
        : undefined;
    const candidates = Array.from(
      new Set(
        [mod.authorAvatar, fallbackAvatarSrc, pngAvatarSrc].filter(
          (v): v is string => Boolean(v),
        ),
      ),
    );
    return { avatarCandidates: candidates, authorAvatarSrc: candidates[0] };
  }, [mod.authorAvatar, mod.authorMemberId]);

  if (typeof window !== "undefined") {
    // Keep a lightweight debug log; don't stringify large objects
    console.debug("[avatar] ModCard candidates", {
      modId: mod.id,
      name: mod.name,
      candidates: avatarCandidates?.slice(0, 3),
    });
  }

  const formatNumber = useMemo(() => {
    return (num: number) => {
      if (num >= 1000000) return (num / 1000000).toFixed(1) + "M";
      if (num >= 1000) return (num / 1000).toFixed(1) + "K";
      return num.toString();
    };
  }, []);

  const formatDate = useMemo(() => {
    return (dateString?: string | null) => {
      if (!dateString) return "Unknown";
      const date = new Date(dateString);
      if (Number.isNaN(date.getTime())) return "Unknown";
      return date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      });
    };
  }, []);

  if (viewMode === "list") {
    return (
      <LazyLoad
        placeholder={
          <div className="card-list-item border-b border-border/20 last:border-b-0 py-1">
            <div className="p-2">
              <div className="flex gap-3 items-center">
                <Skeleton className="w-8 h-8 rounded-lg shrink-0" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-1/3" />
                  <Skeleton className="h-3 w-1/4" />
                </div>
                <div className="flex gap-2">
                  <Skeleton className="h-8 w-16 rounded-md" />
                  <Skeleton className="h-8 w-20 rounded-md" />
                </div>
              </div>
            </div>
          </div>
        }
        className="card-list-item border-b border-border/20 last:border-b-0 py-1"
      >
        <div className="p-2">
          <div 
            className="flex gap-3 items-center"
            style={!mod.isInstalled && mod.collectionVariantsCount == null ? { opacity: 0.6, pointerEvents: "none" } : mod.isIncompatible ? { opacity: 0.55, filter: "grayscale(0.4)", pointerEvents: "none" } : {}}
          >
            <div className="p-1">
              <div 
                className={`w-8 h-8 bg-muted rounded-lg overflow-hidden flex-shrink-0 relative ${(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView ? "cursor-pointer" : ""}`}
                onClick={(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView ? () => onView(mod) : undefined}
              >
                <img
                  src={mod.images[0]}
                  alt={mod.name}
                  className="w-full h-full object-cover"
                  style={shouldBlur ? { filter: "blur(4px)" } : undefined}
                  loading="lazy"
                />
                {shouldBlur && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                    <span className="text-[6px] font-bold text-white/80">
                      18+
                    </span>
                  </div>
                )}
              </div>
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-start justify-between">
                <div className="min-w-0 flex-1">
                  <h3
                    className={`font-normal truncate ${(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView ? "cursor-pointer hover:text-primary" : ""}`}
                    onClick={(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView ? () => onView(mod) : undefined}
                  >
                    {mod.name}
                  </h3>
                </div>

                <div className="flex items-center gap-2 ml-4">
                  <TagList tags={mod.tags} />

                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      onFavorite(mod.id);
                    }}
                    className={mod.isFavorited ? "text-red-500" : ""}
                  >
                    <Heart
                      className={`w-4 h-4 ${
                        mod.isFavorited ? "fill-current" : ""
                      }`}
                    />
                  </Button>

                  <Button
                    variant={actionVariant}
                    size="sm"
                    onClick={handleActionClick}
                    className={`gap-1 ${actionClassName}`}
                    style={{ pointerEvents: "auto", ...actionStyle }}
                  >
                    <ActionIcon className={`w-3 h-3 ${mod.isDownloading ? "animate-spin" : ""}`} />
                    {actionLabel}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </LazyLoad>
    );
  }

  return (
    <Card 
      className={`h-full flex flex-col group overflow-hidden transition-all duration-200 ${
        mod.isIncompatible
          ? "border-border/20 cursor-default"
          : mod.isInstalled || mod.collectionVariantsCount != null
          ? "hover:shadow-md hover:border-primary/30"
          : "border-border/10"
      }`}
      style={
        !mod.isInstalled && mod.collectionVariantsCount == null 
          ? { boxShadow: "none", transform: "none" } 
          : mod.isIncompatible 
          ? { pointerEvents: "none" } 
          : {}
      }
    >
      <LazyLoad
        className="h-full flex flex-col flex-1"
        placeholder={
          <div className="flex flex-col h-full min-h-[400px]">
            <Skeleton className="aspect-video w-full rounded-b-none" />
            <div className="space-y-2" style={{ padding: "12px 16px" }}>
              <Skeleton className="h-5 w-3/4" />
              <div className="space-y-1">
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-5/6" />
              </div>
              <div className="flex gap-2">
                <Skeleton className="h-4 w-12" />
                <Skeleton className="h-4 w-12" />
              </div>
              <Skeleton className="h-9 w-full mt-auto" />
            </div>
          </div>
        }
      >
        <CardContent className="p-0 flex flex-col h-full flex-1">
          <div 
            className="flex-1 flex flex-col"
            style={!mod.isInstalled && mod.collectionVariantsCount == null ? { 
              opacity: 0.6, 
              pointerEvents: "none",
              filter: "grayscale(0.1)"
            } : mod.isIncompatible ? {
              opacity: 0.55,
              filter: "grayscale(0.35)",
              pointerEvents: "none"
            } : {}}
            onClick={(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView ? () => onView(mod) : undefined}
          >
            <div 
              className={`aspect-video bg-muted relative overflow-hidden rounded-t-lg ${(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView ? "cursor-pointer" : ""}`}
            >
              <img
                src={mod.images[0]}
                alt={mod.name}
                className={`w-full h-full object-cover transition-transform duration-300 ${!mod.isIncompatible && (mod.isInstalled || mod.collectionVariantsCount != null) ? "group-hover:scale-105" : ""}`}
                style={shouldBlur ? { filter: "blur(20px)" } : undefined}
                loading="lazy"
              />
              {shouldBlur && (
                <div className="absolute top-2 right-2 pointer-events-none z-10">
                  <span
                    className="text-xs font-bold text-white px-2 py-0.5 rounded"
                    style={{ backgroundColor: "#e84545" }}
                  >
                    NSFW
                  </span>
                </div>
              )}
              {(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView && (
                <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center z-30">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      onView(mod);
                    }}
                    className="gap-2 pointer-events-auto"
                  >
                    <Eye className="w-4 h-4" />
                    View
                  </Button>
                </div>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  onFavorite(mod.id);
                }}
                className={`absolute top-2 right-2 z-40 pointer-events-auto ${
                  mod.isFavorited ? "text-red-500" : "text-white/80 hover:text-white"
                }`}
              >
                <Heart
                  className={`w-4 h-4 ${mod.isFavorited ? "fill-current" : ""}`}
                />
              </Button>
            </div>

            <div className="flex-1 flex flex-col" style={{ minWidth: 0, padding: "12px 16px" }}>
              <div 
                className="mb-2 overflow-hidden shrink-0" 
                style={{ 
                  height: "4rem", 
                  minHeight: "4rem", 
                  maxHeight: "4rem",
                  minWidth: 0 
                }}
              >
                <h3
                  className={`font-medium mb-0.5 truncate ${(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView ? "cursor-pointer hover:text-primary" : ""}`}
                  onClick={(mod.isInstalled || mod.collectionVariantsCount != null) && !mod.isIncompatible && onView ? () => onView(mod) : undefined}
                >
                  {mod.name}
                </h3>
                <p 
                  className="text-sm text-muted-foreground leading-snug w-full"
                  style={{
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                    wordBreak: "break-all",
                    overflowWrap: "anywhere",
                  }}
                >
                  {mod.description}
                </p>
              </div>

              {mod.backendModId != null && mod.backendModId > 0 && (
                <div className="flex items-center gap-2 mb-2">
                  <Avatar className="w-6 h-6 border border-border/50">
                    <AvatarImage
                      src={authorAvatarSrc}
                      alt={mod.author || "Unknown author"}
                      referrerPolicy="no-referrer"
                      data-avatar-index="0"
                      data-avatar-candidates={avatarCandidates.join("|")}
                      onError={(event: SyntheticEvent<HTMLImageElement>) => {
                        const img = event.currentTarget;
                        const candidates = (img.dataset.avatarCandidates || "")
                          .split("|")
                          .filter(Boolean);
                        const currentIndex = Number(img.dataset.avatarIndex || "0");
                        const nextIndex = currentIndex + 1;
                        if (nextIndex < candidates.length) {
                          const nextSrc = candidates[nextIndex];
                          img.dataset.avatarIndex = String(nextIndex);
                          img.src = nextSrc;
                          return;
                        }
                        img.dataset.avatarIndex = String(candidates.length);
                        img.src = "";
                      }}
                    />
                    <AvatarFallback className="text-[10px]">
                      {(mod.author?.trim()?.[0] ?? "?").toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <span className="text-sm text-muted-foreground truncate">
                    {mod.author || "Unknown author"}
                  </span>
                </div>
              )}

              <TagList tags={mod.tags} className="mb-2" />

              <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground mb-2">
                <div className="flex items-center gap-3">
                  {mod.hideMetrics ? (
                    <>
                      {mod.size && (
                        <div className="flex items-center gap-1">
                          <FolderOpen className="w-3 h-3 text-muted-foreground/70" />
                          <span>{mod.size}</span>
                        </div>
                      )}
                      <div className="flex items-center gap-1 min-w-0">
                        <span className="opacity-70">v</span>
                        <span className="truncate">
                          {formatVersion(mod.version)}
                        </span>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="flex items-center gap-1">
                        <Download className="w-3 h-3" />
                        {formatNumber(mod.downloads)}
                      </div>
                      <div className="flex items-center gap-1">
                        <Star className="w-3 h-3 fill-yellow-400 text-yellow-400" />
                        {mod.rating.toFixed(1)}
                      </div>
                    </>
                  )}
                </div>
                <span>{formatDate(mod.lastUpdated)}</span>
              </div>
            </div>
          </div>

          <div style={{ padding: "0 16px 12px 16px" }}>
            <Button
              variant={actionVariant}
              size="sm"
              onClick={handleActionClick}
              className={`w-full gap-2 font-medium ${actionClassName}`}
              style={{ pointerEvents: "auto", ...actionStyle }}
            >
              <ActionIcon className={`w-4 h-4 ${mod.isDownloading ? "animate-spin" : ""}`} />
              {actionLabel}
            </Button>
          </div>
        </CardContent>
      </LazyLoad>
    </Card>
  );
}

// Use React.memo with a focused comparator so ModCards only re-render when
// meaningful fields change. This avoids large re-render storms (e.g. during
// window resizes) when parent re-renders but mod data hasn't changed.
function modPropsAreEqual(prev: ModCardProps, next: ModCardProps) {
  const a = prev.mod;
  const b = next.mod;
  if (a.id !== b.id) return false;
  // Compare a small set of frequently-changing fields that affect render
  const keys: (keyof Mod)[] = [
    "isInstalled",
    "isFavorited",
    "hasUpdate",
    "isUpdating",
    "isActive",
    "downloads",
    "rating",
    "name",
    "description",
    "latestVersion",
    "size",
    "hideMetrics",
    "collectionVariantsCount",
    "collectionDownloadedCount",
    "isDownloading",
    "downloadProgress",
    "isFailed",
    "failureReason",
    "isIncompatible",
  ];
  for (const k of keys) {
    // @ts-ignore - index by dynamic key
    if (a[k] !== b[k]) return false;
  }
  if (a.images?.[0] !== b.images?.[0]) return false;
  // viewMode affects layout
  if (prev.viewMode !== next.viewMode) return false;
  return true;
}

export const ModCard = React.memo(ModCardInner, modPropsAreEqual);
