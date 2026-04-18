import React, { useMemo } from "react";
import type { SyntheticEvent } from "react";
import { Card, CardContent } from "./ui/card";
import { Button } from "./ui/button";
// Badge is used by TagList; not needed directly here
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";
import { Download, Star, Eye, Heart } from "lucide-react";
import TagList from "./TagList";
import { useNsfwFilter } from "./NSFWFilterProvider";

export interface Mod {
  id: string;
  backendModId?: number | null; // server-side mods.mod_id if available
  // Aggregated local download ids that belong to this mod card (used for activation toggles)
  sourceDownloadIds?: number[];
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
  performanceImpact?: number; // 1-5 scale for performance impact
  needsUpdate?: boolean;
  isUpdating?: boolean;
  updateError?: string | null;
  containsAdultContent?: boolean;
}

interface ModCardProps {
  mod: Mod;
  viewMode: "grid" | "list";
  onInstall: (modId: string) => void;
  onFavorite: (modId: string) => void;
  onView: (mod: Mod) => void;
}

function ModCardInner({
  mod,
  viewMode,
  onInstall,
  onFavorite,
  onView,
}: ModCardProps) {
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
      <div className="card-list-item border-b border-border/20 last:border-b-0 py-1">
        <div className="p-2">
          <div className="flex gap-3">
            <div className="p-1">
              <div className="w-8 h-8 bg-muted rounded-lg overflow-hidden flex-shrink-0 relative">
                <img
                  src={mod.images[0]}
                  alt={mod.name}
                  className="w-full h-full object-cover"
                  style={shouldBlur ? { filter: "blur(4px)" } : undefined}
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
                    className="font-normal truncate cursor-pointer hover:text-primary"
                    onClick={() => onView(mod)}
                  >
                    {mod.name}
                  </h3>
                </div>

                <div className="flex items-center gap-2 ml-4">
                  <TagList tags={mod.tags} />

                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onFavorite(mod.id)}
                    className={mod.isFavorited ? "text-red-500" : ""}
                  >
                    <Heart
                      className={`w-4 h-4 ${
                        mod.isFavorited ? "fill-current" : ""
                      }`}
                    />
                  </Button>

                  <Button
                    variant={mod.isInstalled ? "secondary" : "default"}
                    size="sm"
                    onClick={() => onInstall(mod.id)}
                    className="gap-1"
                  >
                    <Download className="w-3 h-3" />
                    {mod.isInstalled ? "Installed" : "Install"}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <Card className="group">
      <CardContent className="p-0">
        <div className="aspect-video bg-muted relative overflow-hidden rounded-t-lg">
          <img
            src={mod.images[0]}
            alt={mod.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
            style={shouldBlur ? { filter: "blur(20px)" } : undefined}
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
          <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => onView(mod)}
              className="gap-2"
            >
              <Eye className="w-4 h-4" />
              View Details
            </Button>
          </div>
          {/* No main category badge; rely on tag list */}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onFavorite(mod.id)}
            className={`absolute top-2 right-2 ${
              mod.isFavorited ? "text-red-500" : "text-white"
            }`}
          >
            <Heart
              className={`w-4 h-4 ${mod.isFavorited ? "fill-current" : ""}`}
            />
          </Button>
        </div>

        <div className="p-4">
          <h3
            className="font-medium mb-1 cursor-pointer hover:text-primary"
            onClick={() => onView(mod)}
          >
            {mod.name}
          </h3>
          <p className="text-sm text-muted-foreground mb-3 line-clamp-2">
            {mod.description}
          </p>

          {mod.backendModId != null && mod.backendModId > 0 && (
            <div className="flex items-center gap-2 mb-3">
              <Avatar className="w-6 h-6">
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
                      if (typeof window !== "undefined") {
                        console.warn("[avatar] fallback to next candidate", {
                          modId: mod.id,
                          name: mod.name,
                          attempted: img.src,
                          nextSrc,
                          nextIndex,
                        });
                      }
                      img.src = nextSrc;
                      return;
                    }
                    if (typeof window !== "undefined") {
                      console.error("[avatar] all avatar candidates failed", {
                        modId: mod.id,
                        name: mod.name,
                        candidates,
                      });
                    }
                    img.dataset.avatarIndex = String(candidates.length);
                    img.src = "";
                  }}
                />
                <AvatarFallback className="text-xs">
                  {(mod.author?.trim()?.[0] ?? "?").toUpperCase()}
                </AvatarFallback>
              </Avatar>
              <span className="text-sm text-muted-foreground">
                {mod.author || "Unknown author"}
              </span>
            </div>
          )}

          <TagList tags={mod.tags} className="mb-3" />

          <div className="flex items-center justify-between text-xs text-muted-foreground mb-3">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1">
                <Download className="w-3 h-3" />
                {formatNumber(mod.downloads)}
              </div>
              <div className="flex items-center gap-1">
                <Star className="w-3 h-3 fill-yellow-400 text-yellow-400" />
                {mod.rating.toFixed(1)}
              </div>
            </div>
            <span>{formatDate(mod.lastUpdated)}</span>
          </div>

          <Button
            variant={mod.isInstalled ? "secondary" : "default"}
            size="sm"
            onClick={() => onInstall(mod.id)}
            className="w-full gap-2"
          >
            <Download className="w-4 h-4" />
            {mod.isInstalled ? "Installed" : "Install"}
          </Button>
        </div>
      </CardContent>
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
  ];
  for (const k of keys) {
    // @ts-ignore - index by dynamic key
    if (a[k] !== b[k]) return false;
  }
  // viewMode affects layout
  if (prev.viewMode !== next.viewMode) return false;
  return true;
}

export const ModCard = React.memo(ModCardInner, modPropsAreEqual);
