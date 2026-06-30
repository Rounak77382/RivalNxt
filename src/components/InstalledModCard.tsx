import React, { useState, type SyntheticEvent } from "react";
import { Card, CardContent } from "./ui/card";
import { Button } from "./ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";
import { Skeleton } from "./ui/skeleton";
import { LazyLoad } from "./LazyLoad";
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
import {
  Trash2,
  RefreshCw,
  Eye,
  Heart,
  AlertTriangle,
  CheckCircle,
  Link,
  CheckCircle2,
  AlertCircle,
  Pencil
} from "lucide-react";
import type { Mod } from "./ModCard";
import { computeTagDisplay } from "../lib/tagDisplay";
import TagList from "./TagList";
import { useNsfwFilter } from "./NSFWFilterProvider";
import { AuthorPopover } from "./AuthorPopover";

interface InstalledModCardProps {
  mod: Mod;
  viewMode: "grid" | "list";
  onUninstall: (modId: string) => void | Promise<void>;
  onUpdate: (modId: string) => void | Promise<void>;
  onCheckUpdate: (modId: string) => void | Promise<void>;
  onView: (mod: Mod) => void;
  onFavorite: (modId: string) => void;
  onOpenFilesTab: (modId: string) => void;
  onAssignModId?: (modId: string) => void;
  onRefresh?: () => void;
}

function InstalledModCardInner({
  mod,
  viewMode,
  onUninstall,
  onCheckUpdate,
  onView,
  onFavorite,
  onOpenFilesTab,
  onAssignModId,
  onRefresh,
}: InstalledModCardProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [isUninstalling, setIsUninstalling] = useState(false);
  const [isCheckingUpdate, setIsCheckingUpdate] = useState(false);

  // NSFW blur filter
  const { nsfwBlurEnabled } = useNsfwFilter();
  const shouldBlur = mod.containsAdultContent && nsfwBlurEnabled;

  const debugCards =
    typeof window !== "undefined" &&
    window.localStorage.getItem("mm-debug-cards") === "1";
  const { visible: displayTags } = computeTagDisplay(
    mod.tags,
    mod.categoryTags?.[0] ?? mod.category,
  );

  // Use lightweight TagList to avoid expensive per-resize measurements.
  // TagList shows up to `maxVisible` tags (default 3) and a simple +N badge.
  const formatDate = (dateString?: string | null) => {
    if (!dateString) return "Unknown";
    const date = new Date(dateString);
    if (Number.isNaN(date.getTime())) return "Unknown";
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  const fallbackAvatarSrc =
    mod.authorMemberId != null
      ? `https://avatars.nexusmods.com/${mod.authorMemberId}/100`
      : undefined;
  const pngAvatarSrc =
    mod.authorMemberId != null
      ? `https://avatars.nexusmods.com/${mod.authorMemberId}/100.png`
      : undefined;

  const avatarCandidates = Array.from(
    new Set(
      [
        mod.customAuthorAvatar,
        mod.authorAvatar,
        fallbackAvatarSrc,
        pngAvatarSrc,
      ].filter((value): value is string => Boolean(value)),
    ),
  );

  const authorAvatarSrc = avatarCandidates[0];

  const handleConfirmUninstall = async () => {
    setIsUninstalling(true);
    try {
      await Promise.resolve(onUninstall(mod.id));
      setConfirmOpen(false);
    } catch (error) {
      console.warn("[InstalledModCard] uninstall failed", error);
    } finally {
      setIsUninstalling(false);
    }
  };

  const confirmDialog = (
    <AlertDialog
      open={confirmOpen}
      onOpenChange={(open) => {
        if (!open && !isUninstalling) {
          setConfirmOpen(false);
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove {mod.name || "this mod"}?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes the mod's local downloads and disconnects it from the
            manager. You can re-install it later from Nexus.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isUninstalling}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirmUninstall}
            disabled={isUninstalling}
          >
            {isUninstalling ? "Removing..." : "Remove"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );

  if (viewMode === "list") {
    return (
      <>
        <LazyLoad
          placeholder={
            <div className="card-list-item border-b border-border/20 last:border-b-0 py-1">
              <div className="p-2">
                <div className="flex gap-3 items-center">
                  <Skeleton className="w-8 h-8 rounded-lg shrink-0" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-1/3" />
                  </div>
                  <div className="flex gap-2">
                    <Skeleton className="h-8 w-12 rounded-md" />
                    <Skeleton className="h-8 w-12 rounded-md" />
                    <Skeleton className="h-8 w-20 rounded-md" />
                  </div>
                </div>
              </div>
            </div>
          }
          className="card-list-item border-b border-border/20 last:border-b-0 py-1"
        >
          <div className="p-2">
            <div className="flex gap-3 flex-wrap sm:flex-nowrap">
              <div className="p-1">
                <div
                  className="w-8 h-8 bg-muted rounded-lg overflow-hidden flex-shrink-0 relative cursor-pointer"
                  role="button"
                  tabIndex={0}
                  aria-label={`Open ${mod.name} details`}
                  onClick={() => onView(mod)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onView(mod);
                    }
                  }}
                >
                  <img
                    src={mod.images[0]}
                    alt={mod.name}
                    className="w-full h-full object-cover"
                    style={shouldBlur ? { filter: "blur(4px)" } : undefined}
                    loading="lazy"
                  />
                  {(mod.hasUpdate || mod.isUpdating) && (
                    <div className="absolute -top-1 -right-1 w-4 h-4 bg-destructive rounded-full flex items-center justify-center">
                      {mod.isUpdating ? (
                        <RefreshCw className="w-2 h-2 text-destructive-foreground animate-spin" />
                      ) : (
                        <AlertTriangle className="w-2 h-2 text-destructive-foreground" />
                      )}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex-1 min-w-0 flex items-center justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <h3
                      className="font-normal truncate cursor-pointer hover:text-primary"
                      onClick={() => onView(mod)}
                    >
                      {mod.name}
                    </h3>
                    {(mod.hasUpdate || mod.isUpdating) && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onOpenFilesTab(mod.id)}
                        className="h-8 px-3 gap-1.5 text-sm font-medium bg-transparent border border-white/10 hover:bg-white hover:text-black transition-all"
                        disabled={mod.isUpdating}
                      >
                        <RefreshCw
                          className={`w-3 h-3${
                            mod.isUpdating ? " animate-spin" : ""
                          }`}
                        />
                        {mod.isUpdating ? "Updating…" : "Update Available"}
                      </Button>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0 flex-wrap sm:flex-nowrap">
                  <TagList
                    tags={displayTags}
                    className="flex items-center gap-1 overflow-hidden flex-nowrap"
                    maxVisible={3}
                  />

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
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      setIsCheckingUpdate(true);
                      try {
                        await Promise.resolve(onCheckUpdate(mod.id));
                      } finally {
                        setIsCheckingUpdate(false);
                      }
                    }}
                    disabled={isCheckingUpdate || mod.isUpdating}
                    className="shrink-0"
                    title="Check for update"
                  >
                    <RefreshCw
                      className={`w-3 h-3${
                        isCheckingUpdate ? " animate-spin" : ""
                      }`}
                    />
                  </Button>

                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmOpen(true)}
                    disabled={isUninstalling}
                    className="text-destructive hover:text-destructive hover:bg-destructive/10 shrink-0"
                  >
                    <Trash2 className="w-3 h-3" />
                  </Button>
                </div>
              </div>
              {(mod.needsManualModId || mod.backendModId == null) && (
                <div className="mt-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full sm:w-auto gap-2 text-amber-400 border-amber-400/40 hover:bg-amber-400/10"
                    onClick={(e) => { e.stopPropagation(); onAssignModId?.(mod.id); }}
                  >
                    <Link className="w-3 h-3" /> Assign Mod ID
                  </Button>
                </div>
              )}
              {mod.renameStatus === "failed" && (
                <div className="mt-1 text-xs text-red-400 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" /> {mod.renameError}
                </div>
              )}
            </div>
          </div>
        </LazyLoad>
        {confirmDialog}
      </>
    );
  }

  return (
    <>
      <Card className="h-full flex flex-col group relative overflow-hidden">
        <LazyLoad
          className="h-full flex flex-col flex-1"
          placeholder={
            <div className="flex flex-col min-h-[370px]">
              <Skeleton className="aspect-video w-full rounded-b-none" />
              <div className="p-4 space-y-4">
                <div className="space-y-2">
                  <Skeleton className="h-5 w-3/4" />
                  <Skeleton className="h-3 w-1/3" />
                </div>
                <div className="flex gap-2">
                  <Skeleton className="h-6 w-6 rounded-full" />
                  <Skeleton className="h-4 w-24" />
                </div>
                <div className="flex gap-2 mt-auto">
                  <Skeleton className="h-9 w-10" />
                  <Skeleton className="h-9 flex-1" />
                  <Skeleton className="h-9 w-10" />
                </div>
              </div>
            </div>
          }
        >
          <CardContent className="p-0 h-full min-h-[370px] flex flex-col flex-1">
            <div
              className="aspect-video bg-muted relative overflow-hidden rounded-t-lg cursor-pointer"
              role="button"
              tabIndex={0}
              aria-label={`Open ${mod.name} details`}
              onClick={() => onView(mod)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onView(mod);
                }
              }}
            >
              <img
                src={mod.images[0]}
                alt={mod.name}
                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                style={shouldBlur ? { filter: "blur(20px)" } : undefined}
                loading="lazy"
              />
            {shouldBlur && (
              <div className="absolute top-2 right-2 flex items-center justify-center pointer-events-none z-10">
                <img src="/icons/18-plus.svg" alt="18+" className="w-8 h-8" />
              </div>
            )}

            {(mod.hasUpdate || mod.isUpdating) && (
              <div className="absolute top-2 left-2 bg-destructive text-destructive-foreground px-2 py-1 rounded-md text-xs font-medium flex items-center gap-1">
                {mod.isUpdating ? (
                  <RefreshCw className="w-3 h-3 animate-spin" />
                ) : (
                  <AlertTriangle className="w-3 h-3" />
                )}
                {mod.isUpdating ? "Updating…" : "Update Available"}
              </div>
            )}

            <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={(ev) => {
                    // prevent parent handler from double-firing
                    ev.stopPropagation();
                    onView(mod);
                  }}
                  className="gap-2"
                >
                  <Eye className="w-4 h-4" />
                  View
                </Button>
              </div>
            </div>
          </div>

          <div
            className="flex flex-col flex-1 h-full "
            style={{ padding: "10px 6px 16px 6px" }}
          >
            <div className="flex-1 flex flex-col justify-between h-full">
              <div style={{ paddingLeft: "10px" }}>
                <div className="flex items-start justify-between mb-2">
                  <div className="flex-1 min-w-0">
                    <h3
                      className="font-medium mb-1 cursor-pointer hover:text-primary line-clamp-1"
                      onClick={() => onView(mod)}
                    >
                      {mod.name}
                    </h3>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {mod.isActive && (
                        <>
                          <CheckCircle className="w-3 h-3 text-green-500" />
                          <span>Active</span>
                          <span>•</span>
                        </>
                      )}
                      <span>
                        {formatDate(mod.installDate || mod.lastUpdated)}
                      </span>
                    </div>
                  </div>

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
                </div>

                {(!mod.backendModId || mod.backendModId <= 0 || mod.needsManualModId) ? (
                  <AuthorPopover 
                    modKey={mod.modKey!} 
                    currentAuthorName={mod.customAuthorName} 
                    onSave={() => {
                      // Call direct refresh prop first (fastest), then event as fallback
                      if (onRefresh) {
                        onRefresh();
                      } else {
                        window.dispatchEvent(new CustomEvent("refresh-downloads"));
                      }
                    }}
                  >
                    <div className="flex items-center gap-2 mb-2 cursor-pointer group rounded-sm p-1 -ml-1 hover:bg-accent hover:text-accent-foreground transition-colors max-w-max">
                      <Avatar className="w-6 h-6">
                        <AvatarImage
                          src={authorAvatarSrc}
                          alt={mod.customAuthorName || mod.author || "Unknown author"}
                          referrerPolicy="no-referrer"
                          data-avatar-index="0"
                          data-avatar-candidates={avatarCandidates.join("|")}
                          onError={(event: SyntheticEvent<HTMLImageElement>) => {
                            const img = event.currentTarget;
                            const candidates = (img.dataset.avatarCandidates || "")
                              .split("|")
                              .filter(Boolean);
                            const currentIndex = Number(
                              img.dataset.avatarIndex || "0",
                            );
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
                        <AvatarFallback className="text-xs">
                          {(mod.customAuthorName || mod.author || "?")
                            .substring(0, 2)
                            .toUpperCase()}
                        </AvatarFallback>
                      </Avatar>
                      <span className="text-xs font-medium truncate flex-1 flex items-center gap-1 group-hover:text-primary">
                        {mod.customAuthorName || mod.author || "Assign Author"}
                        <Pencil className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity ml-1" />
                      </span>
                    </div>
                  </AuthorPopover>
                ) : (
                  <a
                    className="flex items-center gap-2 mb-3 cursor-pointer hover:text-primary transition-colors"
                    onClick={async () => {
                      const modUrl = `https://next.nexusmods.com/profile/${mod.author || "unknown"}`;
                      try {
                        const { openInBrowser } = await import("../lib/tauri-utils");
                        await openInBrowser(modUrl);
                      } catch (error) {
                        console.error("Failed to open mod page:", error);
                      }
                    }}
                  >
                    <Avatar className="w-6 h-6">
                      <AvatarImage
                        src={authorAvatarSrc}
                        alt={mod.author || "Unknown author"}
                        referrerPolicy="no-referrer"
                        data-avatar-index="0"
                        data-avatar-candidates={avatarCandidates.join("|")}
                        onError={(event: SyntheticEvent<HTMLImageElement>) => {
                          const img = event.currentTarget;
                          const candidates = (img.dataset.avatarCandidates || "").split("|").filter(Boolean);
                          const currentIndex = Number(img.dataset.avatarIndex || "0");
                          const nextIndex = currentIndex + 1;
                          if (nextIndex < candidates.length) {
                            const nextSrc = candidates[nextIndex];
                            img.dataset.avatarIndex = String(nextIndex);
                            img.src = nextSrc;
                          }
                        }}
                      />
                      <AvatarFallback className="text-xs">
                        {(mod.author?.trim()?.[0] ?? "?").toUpperCase()}
                      </AvatarFallback>
                    </Avatar>
                    <span className="text-xs text-muted-foreground font-medium truncate flex-1">
                      {mod.author || "Unknown author"}
                    </span>
                  </a>
                )}
                <TagList
                  tags={displayTags}
                  className="flex items-center gap-1 mb-2 overflow-hidden flex-nowrap"
                  maxVisible={3}
                />
                {debugCards && (
                  <div className="text-[10px] text-muted-foreground border rounded p-1">
                    <div>
                      <strong>category tags:</strong>{" "}
                      {(mod.categoryTags && mod.categoryTags.length > 0
                        ? mod.categoryTags.join(", ")
                        : mod.category) || "(none)"}
                    </div>
                    <div>
                      <strong>tags:</strong> {mod.tags.join(", ")}
                    </div>
                  </div>
                )}
              </div>
              <div className="flex gap-2 mt-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async (ev) => {
                    ev.stopPropagation();
                    setIsCheckingUpdate(true);
                    try {
                      await Promise.resolve(onCheckUpdate(mod.id));
                    } finally {
                      setIsCheckingUpdate(false);
                    }
                  }}
                  disabled={isCheckingUpdate || mod.isUpdating}
                  className="shrink-0"
                  title="Check for update"
                >
                  <RefreshCw
                    className={`w-4 h-4${
                      isCheckingUpdate ? " animate-spin" : ""
                    }`}
                  />
                </Button>
                {(mod.needsManualModId || mod.backendModId == null) ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1 gap-2 px-[5px] text-amber-400 border-amber-400/40 hover:bg-amber-400/10"
                    onClick={(e) => { e.stopPropagation(); onAssignModId?.(mod.id); }}
                  >
                    <Link className="w-3 h-3" /> Assign Mod ID
                  </Button>
                ) : mod.hasUpdate || mod.isUpdating ? (
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => onOpenFilesTab(mod.id)}
                    className="flex-1 gap-2"
                    disabled={mod.isUpdating}
                    aria-disabled={mod.isUpdating}
                  >
                    <RefreshCw
                      className={`w-3 h-3${
                        mod.isUpdating ? " animate-spin" : ""
                      }`}
                    />
                    {mod.isUpdating ? "Updating…" : "Update"}
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1 gap-2 pointer-events-none"
                    asChild
                  >
                    <div>
                      <CheckCircle className="w-3 h-3" />
                      Up to date
                    </div>
                  </Button>
                )}

                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(ev) => {
                    ev.stopPropagation();
                    setConfirmOpen(true);
                  }}
                  disabled={isUninstalling}
                  className="text-destructive hover:text-destructive hover:bg-destructive/10"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
              {mod.updateError && (
                <div className="mt-2 text-xs text-destructive">
                  {mod.updateError}
                </div>
              )}
              {mod.renameStatus === "failed" && (
                <div className="mt-2 text-xs text-red-400 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" /> {mod.renameError}
                </div>
              )}
            </div>
          </div>
        </CardContent>
        </LazyLoad>
      </Card>
      {confirmDialog}
    </>
  );
}

// Custom memo comparison for performance
function installedModPropsAreEqual(
  prev: InstalledModCardProps,
  next: InstalledModCardProps,
) {
  const a = prev.mod;
  const b = next.mod;
  if (a.id !== b.id) return false;
  const keys: (keyof Mod)[] = [
    "isInstalled",
    "isFavorited",
    "hasUpdate",
    "isUpdating",
    "isActive",
    "name",
    "latestVersion",
    "updateError",
    "manualModIdOverride",
    "renameStatus",
    "renameError",
    "customAuthorName",
    "customAuthorAvatar",
    "customAuthorType",
    "customAuthorId",
  ];
  for (const k of keys) {
    // @ts-ignore
    if (a[k] !== b[k]) return false;
  }
  if (a.images?.[0] !== b.images?.[0]) return false;
  if ((a.tags || []).join(",") !== (b.tags || []).join(",")) return false;
  if (prev.viewMode !== next.viewMode) return false;
  return true;
}

export const InstalledModCard = React.memo(
  InstalledModCardInner,
  installedModPropsAreEqual,
);
