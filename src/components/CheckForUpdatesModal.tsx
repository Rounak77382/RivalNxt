import { useCallback, useMemo, useRef } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import {
  RefreshCw,
  CheckCircle,
  AlertTriangle,
  ArrowUpCircle,
  Loader2,
} from "lucide-react";
import type { Mod } from "./ModCard";
import {
  checkModUpdate,
} from "../lib/api";
import { toast } from "sonner";

interface CheckForUpdatesModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mods: Mod[];
  onUpdateMod?: (modId: string, targetFileId?: number) => void;
  onRefreshMods?: () => void;
  // Controlled state lifted to parent so results survive modal close/reopen
  statuses: Record<string, ModStatus>;
  onStatusesChange: (s: Record<string, ModStatus>) => void;
  checked: boolean;
  onCheckedChange: (c: boolean) => void;
  isCheckingAll: boolean;
  onIsCheckingAllChange: (v: boolean) => void;
}

export type ModUpdateStatus =
  | "idle"
  | "checking"
  | "up-to-date"
  | "has-update"
  | "error";

export interface ModStatus {
  status: ModUpdateStatus;
  error?: string;
}

const FALLBACK_IMG =
  "https://i.pinimg.com/1200x/44/da/5e/44da5e6d9dd75cb753ab5925aff4ce4c.jpg";

const formatVersionDisplay = (ver: string | undefined | null): string => {
  if (!ver) return "";
  const cleaned = ver.replace(/\.\d{9,11}$/, "");
  return cleaned.toLowerCase().startsWith("v") ? cleaned : `v${cleaned}`;
};

const normalizeVersionForCheck = (ver: string | undefined | null): string => {
  if (!ver) return "";
  let cleaned = ver.replace(/\.\d{9,11}$/, "").toLowerCase();
  if (!cleaned.startsWith("v")) cleaned = "v" + cleaned;
  cleaned = cleaned.replace(/^vs/, "v");
  cleaned = cleaned.replace(/-w\d*$/, "");
  return cleaned;
};

export function CheckForUpdatesModal({
  open,
  onOpenChange,
  mods,
  onUpdateMod,
  onRefreshMods,
  statuses,
  onStatusesChange,
  checked,
  onCheckedChange,
  isCheckingAll,
  onIsCheckingAllChange,
}: CheckForUpdatesModalProps) {
  const installedMods = useMemo(
    () =>
      mods.filter(
        (m) =>
          m.isInstalled &&
          typeof m.backendModId === "number" &&
          m.backendModId > 0,
      ),
    [mods],
  );

  // Keep a ref of statuses to avoid stale closure during async check-all loop
  const statusesRef = useRef(statuses);
  statusesRef.current = statuses;

  const setModStatus = useCallback(
    (modId: string, status: ModUpdateStatus, error?: string) => {
      const next = { ...statusesRef.current, [modId]: { status, error } };
      statusesRef.current = next;
      onStatusesChange(next);
    },
    [onStatusesChange],
  );

  const checkSingleMod = useCallback(
    async (mod: Mod) => {
      if (!mod.backendModId) return;
      setModStatus(mod.id, "checking");
      try {
        // checkModUpdate updates the backend's cached update status
        const result = await checkModUpdate(mod.backendModId);
        // Use the backend's authoritative needs_update value directly
        // (same field as mod.hasUpdate) to stay in sync with the sidebar badge
        if (result?.needs_update) {
          setModStatus(mod.id, "has-update");
        } else {
          setModStatus(mod.id, "up-to-date");
        }
      } catch (e) {
        setModStatus(
          mod.id,
          "error",
          e instanceof Error ? e.message : "Check failed",
        );
      }
    },
    [setModStatus],
  );

  const handleCheckAll = useCallback(async () => {
    if (isCheckingAll || installedMods.length === 0) return;
    onIsCheckingAllChange(true);
    onCheckedChange(false);
    // Reset all to idle first
    onStatusesChange({});

    const toastId = "check-updates-modal";
    toast.loading(`Checking 0/${installedMods.length} mods…`, { id: toastId });

    let done = 0;
    for (const mod of installedMods) {
      await checkSingleMod(mod);
      done++;
      toast.loading(`Checking ${done}/${installedMods.length} mods…`, {
        id: toastId,
      });
    }

    toast.dismiss(toastId);
    onIsCheckingAllChange(false);
    onCheckedChange(true);

    // Refresh mods so sidebar badge (updatesCount) re-syncs with modal results
    if (onRefreshMods) {
      onRefreshMods();
    }
  }, [isCheckingAll, installedMods, checkSingleMod, onRefreshMods, onIsCheckingAllChange, onCheckedChange, onStatusesChange]);

  const getDerivedStatus = useCallback((mod: Mod): ModUpdateStatus => {
    if (statuses[mod.id]) {
      return statuses[mod.id].status;
    }
    return mod.hasUpdate ? "has-update" : "idle";
  }, [statuses]);

  const modsWithUpdates = useMemo(
    () =>
      installedMods.filter((m) => {
        const s = getDerivedStatus(m) === "has-update";
        if (s) {
          if (
            m.installedVersion &&
            m.latestVersion &&
            normalizeVersionForCheck(m.installedVersion) === normalizeVersionForCheck(m.latestVersion)
          ) {
            return false;
          }
          return true;
        }
        return false;
      }),
    [installedMods, getDerivedStatus],
  );

  const visibleMods = useMemo(() => {
    return installedMods.filter((mod) => {
      const s = getDerivedStatus(mod);
      if (s === "has-update") {
        if (
          mod.installedVersion &&
          mod.latestVersion &&
          normalizeVersionForCheck(mod.installedVersion) === normalizeVersionForCheck(mod.latestVersion)
        ) {
          return false;
        }
        return true;
      }
      return s === "checking" || s === "error";
    });
  }, [installedMods, getDerivedStatus]);

  const handleUpdateAll = useCallback(() => {
    if (!onUpdateMod) return;
    for (const mod of modsWithUpdates) {
      onUpdateMod(mod.id, mod.latestFileId ?? undefined);
    }
    toast.success(
      `Started update for ${modsWithUpdates.length} mod${modsWithUpdates.length !== 1 ? "s" : ""}.`,
    );
  }, [modsWithUpdates, onUpdateMod]);

  // Stats for header
  const totalChecked = installedMods.filter((m) =>
    ["up-to-date", "has-update", "error"].includes(getDerivedStatus(m)),
  ).length;

  const accentGradient = isCheckingAll
    ? "linear-gradient(90deg, #8b5cf6, #6366f1)"
    : checked && modsWithUpdates.length > 0
    ? "linear-gradient(90deg, #ef4444, #f97316)"
    : checked && modsWithUpdates.length === 0
    ? "linear-gradient(90deg, #22c55e, #10b981)"
    : "linear-gradient(90deg, #3b82f6, #06b6d4)";

  const iconBg = isCheckingAll
    ? "linear-gradient(135deg, rgba(139,92,246,0.15), rgba(99,102,241,0.15))"
    : checked && modsWithUpdates.length > 0
    ? "linear-gradient(135deg, rgba(239,68,68,0.15), rgba(249,115,22,0.15))"
    : checked && modsWithUpdates.length === 0
    ? "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(16,185,129,0.15))"
    : "linear-gradient(135deg, rgba(59,130,246,0.15), rgba(6,182,212,0.15))";

  const getStatusBadge = (mod: Mod) => {
    const s = getDerivedStatus(mod);
    if (s === "checking")
      return (
        <span className="flex items-center gap-1.5 text-xs text-violet-400 font-semibold">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Checking…
        </span>
      );
    if (s === "has-update")
      return (
        <div className="text-xs flex items-center gap-1.5 font-medium pr-2">
          {mod.installedVersion ? (
            <>
              {mod.latestVersion && (
                <>
                  <span className="text-muted-foreground/60">→</span>
                  <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-semibold animate-pulse">
                    {formatVersionDisplay(mod.latestVersion)}
                  </span>
                </>
              )}
            </>
          ) : (
            mod.latestVersion && (
              <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-semibold animate-pulse">
                {formatVersionDisplay(mod.latestVersion)}
              </span>
            )
          )}
        </div>
      );
    if (s === "up-to-date")
      return (
        <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-semibold">
          <CheckCircle className="w-3.5 h-3.5" />
          Up to date
        </span>
      );
    if (s === "error")
      return (
        <span
          className="flex items-center gap-1.5 text-xs text-red-400 font-semibold cursor-help"
          title={statuses[mod.id]?.error}
        >
          <AlertTriangle className="w-3.5 h-3.5" />
          Failed
        </span>
      );
    // idle
    return (
      <span className="text-xs text-muted-foreground/60">
        Ready
      </span>
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="w-full bg-card p-0 flex flex-col shadow-2xl"
        style={{
          maxWidth: "min(900px, 95vw)",
          minWidth: "600px",
          width: "min(900px, 95vw)",
          height: "85vh",
          maxHeight: "85vh",
          border: "1px solid hsl(var(--border))",
          borderRadius: "16px",
          overflow: "hidden",
        }}
      >
        {/* Gradient accent bar */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "4px",
            background: accentGradient,
            transition: "background 0.4s ease",
            zIndex: 50,
          }}
        />

        {/* Header */}
        <DialogHeader className="flex-shrink-0 pt-6">
          <div className="flex items-center justify-between w-full px-6 pt-2 pb-4 border-b border-border/60">
            <div className="flex items-center gap-3">
              {/* Icon Container */}
              <div
                style={{
                  width: "44px",
                  height: "44px",
                  borderRadius: "12px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: iconBg,
                  flexShrink: 0,
                  transition: "background 0.4s ease",
                }}
              >
                {isCheckingAll ? (
                  <Loader2 className="w-5 h-5 text-violet-400 animate-spin" />
                ) : checked && modsWithUpdates.length > 0 ? (
                  <ArrowUpCircle className="w-5 h-5 text-red-400 animate-pulse" />
                ) : checked && modsWithUpdates.length === 0 ? (
                  <CheckCircle className="w-5 h-5 text-emerald-400" />
                ) : (
                  <RefreshCw className="w-5 h-5 text-blue-400" />
                )}
              </div>

              <div>
                <DialogTitle className="text-lg font-semibold tracking-tight">
                  Check for Updates
                </DialogTitle>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {installedMods.length} mod
                  {installedMods.length !== 1 ? "s" : ""} with Nexus IDs
                  {(checked || modsWithUpdates.length > 0)
                    ? ` · ${modsWithUpdates.length} update${modsWithUpdates.length !== 1 ? "s" : ""} available`
                    : ""}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {/* Update All — only disabled when checking is running or no updates are present */}
              <Button
                variant="default"
                size="sm"
                disabled={isCheckingAll || modsWithUpdates.length === 0}
                onClick={handleUpdateAll}
                className="gap-2 transition-all duration-300 hover:shadow-lg hover:shadow-red-500/20 active:scale-[0.98]"
                style={{
                  background: isCheckingAll || modsWithUpdates.length === 0
                    ? undefined
                    : "linear-gradient(135deg, #ef4444, #f97316)",
                  border: "none",
                  fontWeight: 600,
                }}
              >
                <ArrowUpCircle className="w-4 h-4" />
                Update All
                {modsWithUpdates.length > 0 && (
                  <Badge
                    variant="secondary"
                    className="ml-1 text-xs px-1.5 py-0 bg-white/20 text-white border-none"
                  >
                    {modsWithUpdates.length}
                  </Badge>
                )}
              </Button>

              <Button
                variant="outline"
                size="sm"
                disabled={isCheckingAll || installedMods.length === 0}
                onClick={handleCheckAll}
                className="gap-2 transition-all duration-300 hover:bg-accent/50 active:scale-[0.98]"
                style={{
                  borderColor: isCheckingAll ? "rgba(139,92,246,0.2)" : "rgba(59,130,246,0.3)",
                  color: isCheckingAll ? "#a78bfa" : "#3b82f6",
                  fontWeight: 600,
                }}
              >
                <RefreshCw
                  className={`w-4 h-4 ${isCheckingAll ? "animate-spin" : ""}`}
                />
                {isCheckingAll
                  ? `Checking… (${totalChecked}/${installedMods.length})`
                  : checked
                    ? "Re-check All"
                    : "Check All"}
              </Button>
            </div>
          </div>
        </DialogHeader>

        {/* Scrollable mod list */}
        <style>{`
          .updates-modal-scroll::-webkit-scrollbar {
            width: 8px;
          }
          .updates-modal-scroll::-webkit-scrollbar-track {
            background: transparent;
          }
          .updates-modal-scroll::-webkit-scrollbar-thumb {
            background: rgba(100, 100, 100, 0.45);
            border-radius: 4px;
          }
          .updates-modal-scroll::-webkit-scrollbar-thumb:hover {
            background: rgba(100, 100, 100, 0.7);
          }
          .updates-modal-scroll {
            scrollbar-color: rgba(100, 100, 100, 0.45) transparent;
            scrollbar-width: thin;
          }
        `}</style>

        <div className="flex-1 min-h-0 overflow-y-auto updates-modal-scroll px-6 py-4">
          {installedMods.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
              <RefreshCw className="w-10 h-10 opacity-30" />
              <p className="text-sm font-medium">
                No installed mods are linked to Nexus IDs.
              </p>
            </div>
          ) : !checked && !isCheckingAll && visibleMods.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground text-center">
              <RefreshCw className="w-10 h-10 opacity-30 animate-pulse" />
              <div>
                <p className="text-sm font-medium text-foreground">
                  Ready to check for updates
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Click the <strong>Check All</strong> button above to scan your {installedMods.length} mods.
                </p>
              </div>
            </div>
          ) : checked && visibleMods.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-green-500 text-center">
              <CheckCircle className="w-12 h-12 opacity-80 mx-auto animate-pulse" />
              <div>
                <p className="text-sm font-medium text-foreground">
                  All mods are up to date!
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  No updates were found for your installed mods.
                </p>
              </div>
            </div>
          ) : (
            <div className="grid gap-2">
              {visibleMods.map((mod) => {
                const s = statuses[mod.id]?.status ?? "idle";
                const hasUpdate = s === "has-update";

                return (
                  <div
                    key={mod.id}
                    className={`group flex items-center gap-4 p-3.5 rounded-xl border transition-all duration-300 hover:-translate-y-0.5 hover:shadow-lg ${
                      hasUpdate
                        ? "border-red-500/25 bg-red-500/5 hover:border-red-500/40 hover:shadow-red-500/5"
                        : "border-border/60 bg-card hover:border-border/80 hover:bg-accent/30 hover:shadow-black/5"
                    }`}
                  >
                    {/* Thumbnail */}
                    <div className="w-14 h-10 rounded-lg overflow-hidden bg-muted flex-shrink-0 border border-border/40 relative">
                      <img
                        src={mod.images?.[0] || FALLBACK_IMG}
                        alt={mod.name}
                        className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-110"
                        onError={(e) => {
                          if (e.currentTarget.src !== FALLBACK_IMG)
                            e.currentTarget.src = FALLBACK_IMG;
                        }}
                        loading="lazy"
                      />
                    </div>

                    {/* Name + author */}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold truncate leading-tight">
                        {mod.name}
                      </p>
                      {mod.author && (
                        <p className="text-xs text-muted-foreground truncate mt-0.5">
                          by {mod.author}
                        </p>
                      )}
                    </div>

                    {/* Version info */}
                    {mod.installedVersion && !hasUpdate && (
                      <div className="text-xs hidden sm:flex items-center gap-1.5 flex-shrink-0 font-medium">
                        <span className="px-2 py-0.5 rounded bg-muted/60 text-muted-foreground border border-border/40">
                          {formatVersionDisplay(mod.installedVersion)}
                        </span>
                      </div>
                    )}

                    {/* Status badge */}
                    <div className={`flex items-center justify-end flex-shrink-0 ${hasUpdate ? "w-32" : "w-28"}`}>
                      {getStatusBadge(mod)}
                    </div>

                    {/* Update button (only when update available) */}
                    {hasUpdate && onUpdateMod && (
                      <Button
                        size="sm"
                        variant="default"
                        className="gap-1.5 flex-shrink-0 h-8 text-xs font-semibold transition-all duration-300 hover:shadow-md hover:shadow-red-500/10 active:scale-95 border-none"
                        style={{
                          background: "linear-gradient(135deg, #ef4444, #f97316)",
                          border: "none",
                        }}
                        onClick={() => onUpdateMod(mod.id, mod.latestFileId ?? undefined)}
                      >
                        <ArrowUpCircle className="w-3.5 h-3.5" />
                        Update
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer hint */}
        {!checked && !isCheckingAll && installedMods.length > 0 ? (
          <div className="flex-shrink-0 py-1 border-t border-border/40">
            <p className="text-xs text-muted-foreground text-center">
              Click <strong>Check All</strong> to contact the Nexus API and
              refresh update status for all mods.
            </p>
          </div>
        ) : (
          <div className="h-6 flex-shrink-0" />
        )}
      </DialogContent>
    </Dialog>
  );
}

