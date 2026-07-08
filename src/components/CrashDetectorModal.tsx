import { useState, useMemo, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Button } from "./ui/button";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Loader2,
  ShieldOff,
  X,
  PackageX,
  CheckCircle2,
  Info,
} from "lucide-react";
import { toast } from "sonner";
import { type ApiDownload, disableAllMods, deactivateByName, scanActive } from "../lib/api";
import {
  type CrashInfo,
  correlateCrashWithMods,
  formatCrashTime,
} from "../lib/crashParser";

export interface CrashDetectorModalProps {
  open: boolean;
  crashInfo: CrashInfo | null;
  /** All downloads from the API — we filter to active ones inside */
  allDownloads: ApiDownload[];
  /** Pak assets for active downloads */
  pakAssets: import("../lib/api").ApiPakAsset[];
  onDismiss: () => void;
  /** Called after mods are deactivated so caller can refresh */
  onDeactivated: () => void;
}

export function CrashDetectorModal({
  open,
  crashInfo,
  allDownloads,
  pakAssets,
  onDismiss,
  onDeactivated,
}: CrashDetectorModalProps) {
  const [rawLogExpanded, setRawLogExpanded] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [isDeactivating, setIsDeactivating] = useState(false);
  const [done, setDone] = useState(false);

  // Reset state when modal opens with new crash
  const resetState = useCallback(() => {
    setRawLogExpanded(false);
    setIsDeactivating(false);
    setDone(false);
  }, []);

  // Correlate mods with crash info
  const { suspicious, rest } = useMemo(() => {
    if (!crashInfo) return { suspicious: [], rest: [] };
    return correlateCrashWithMods(crashInfo, allDownloads, pakAssets);
  }, [crashInfo, allDownloads, pakAssets]);

  const allActiveMods = useMemo(
    () => [...suspicious.map((s) => s.mod), ...rest],
    [suspicious, rest],
  );

  // Pre-select suspicious mods when modal opens.
  // Priority: if any exact/skin matches exist → select only those.
  // Fallback: if only character/fallback matches exist → select those too.
  useMemo(() => {
    if (open && crashInfo) {
      resetState();
      const skinOrExact = suspicious.filter(
        (s) => s.matchType === "exact" || s.matchType === "skin",
      );
      const toSelect =
        skinOrExact.length > 0 ? skinOrExact : suspicious;
      setSelectedIds(new Set(toSelect.map((s) => s.mod.id)));
    }
  }, [open, crashInfo]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const selectAll = () =>
    setSelectedIds(new Set(allActiveMods.map((m) => m.id)));
  const selectNone = () => setSelectedIds(new Set());

  const handleDeactivateSelected = async () => {
    if (selectedIds.size === 0) {
      toast.info("No mods selected to deactivate");
      return;
    }
    setIsDeactivating(true);
    const toastId = "crash-deactivate";
    toast.loading(
      `Deactivating ${selectedIds.size} mod${selectedIds.size !== 1 ? "s" : ""}...`,
      { id: toastId },
    );
    try {
      const toDeactivate = allActiveMods.filter((m) => selectedIds.has(m.id));
      for (const mod of toDeactivate) {
        const name = mod.name || mod.mod_name || String(mod.id);
        await deactivateByName(name);
      }
      await scanActive();
      toast.success(
        `Deactivated ${toDeactivate.length} mod${toDeactivate.length !== 1 ? "s" : ""}`,
        { id: toastId },
      );
      setDone(true);
      onDeactivated();
    } catch (err: any) {
      toast.error(err?.message || "Failed to deactivate mods", {
        id: toastId,
      });
    } finally {
      setIsDeactivating(false);
    }
  };

  const handleDisableAll = async () => {
    setIsDeactivating(true);
    const toastId = "crash-disable-all";
    toast.loading("Disabling all active mods...", { id: toastId });
    try {
      await disableAllMods();
      await scanActive();
      toast.success("All active mods disabled", { id: toastId });
      setDone(true);
      onDeactivated();
    } catch (err: any) {
      toast.error(err?.message || "Failed to disable all mods", {
        id: toastId,
      });
    } finally {
      setIsDeactivating(false);
    }
  };

  if (!crashInfo) return null;

  const hasActiveMods = allActiveMods.length > 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen && !isDeactivating) onDismiss();
      }}
    >
      <DialogContent
        className="max-w-[580px] p-0 overflow-hidden border-0 shadow-2xl"
        style={{
          background: "var(--color-surface-1, #1a1a2e)",
          borderRadius: "16px",
        }}
        showCloseButton={false}
      >
        {/* ── Header gradient strip ─────────────────────────────── */}
        <div
          style={{
            background:
              "linear-gradient(135deg, #dc2626 0%, #ea580c 50%, #f97316 100%)",
            padding: "20px 24px 16px",
            position: "relative",
          }}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3 text-white text-xl font-bold">
              <div
                style={{
                  background: "rgba(255,255,255,0.15)",
                  borderRadius: "10px",
                  padding: "6px",
                  display: "flex",
                  alignItems: "center",
                }}
              >
                <AlertTriangle className="w-5 h-5 text-white" />
              </div>
              Crash Detected
            </DialogTitle>
            <p className="text-orange-100 text-sm mt-1 opacity-90">
              Marvel Rivals crashed. A mod may be responsible.
            </p>
          </DialogHeader>

          {/* Close button */}
          <button
            onClick={onDismiss}
            disabled={isDeactivating}
            className="absolute top-4 right-4 text-white/70 hover:text-white transition-colors"
            style={{ background: "none", border: "none", cursor: "pointer" }}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* ── Body ──────────────────────────────────────────────── */}
        <div style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: "16px" }}>

          {/* Crash summary card */}
          <div
            style={{
              background: "rgba(220,38,38,0.08)",
              border: "1px solid rgba(220,38,38,0.2)",
              borderRadius: "10px",
              padding: "14px 16px",
              display: "flex",
              flexDirection: "column",
              gap: "6px",
            }}
          >
            <p
              className="text-sm font-semibold"
              style={{ color: "var(--color-text-1, #f1f5f9)" }}
            >
              {crashInfo.errorMessage}
            </p>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {crashInfo.timeOfCrash && (
                <span className="text-xs" style={{ color: "var(--color-text-3, #94a3b8)" }}>
                  🕐 {formatCrashTime(crashInfo.timeOfCrash)}
                </span>
              )}
              {crashInfo.engineVersion && (
                <span className="text-xs" style={{ color: "var(--color-text-3, #94a3b8)" }}>
                  ⚙️ {crashInfo.engineVersion}
                </span>
              )}
              {crashInfo.crashGuid && (
                <span
                  className="text-xs font-mono"
                  style={{ color: "var(--color-text-3, #94a3b8)", wordBreak: "break-all" }}
                >
                  ID: {crashInfo.crashGuid}
                </span>
              )}
            </div>
          </div>

          {/* Raw log expander */}
          {crashInfo.rawXml && (
            <div>
              <button
                onClick={() => setRawLogExpanded((v) => !v)}
                className="flex items-center gap-1.5 text-xs transition-colors"
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--color-text-3, #94a3b8)",
                  padding: 0,
                }}
              >
                {rawLogExpanded ? (
                  <ChevronUp className="w-3.5 h-3.5" />
                ) : (
                  <ChevronDown className="w-3.5 h-3.5" />
                )}
                {rawLogExpanded ? "Hide" : "Show"} raw crash log
              </button>
              {rawLogExpanded && (
                <pre
                  style={{
                    background: "rgba(0,0,0,0.3)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: "8px",
                    padding: "10px 12px",
                    fontSize: "10px",
                    lineHeight: "1.5",
                    color: "#94a3b8",
                    maxHeight: "160px",
                    overflowY: "auto",
                    marginTop: "8px",
                    wordBreak: "break-all",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {crashInfo.rawXml}
                </pre>
              )}
            </div>
          )}

          {/* Mod list */}
          {done ? (
            <div
              style={{
                background: "rgba(34,197,94,0.1)",
                border: "1px solid rgba(34,197,94,0.2)",
                borderRadius: "10px",
                padding: "14px 16px",
                display: "flex",
                alignItems: "center",
                gap: "10px",
              }}
            >
              <CheckCircle2 className="w-5 h-5 text-green-400 shrink-0" />
              <div>
                <p className="text-sm font-semibold text-green-300">
                  Mods deactivated
                </p>
                <p className="text-xs text-green-400/70 mt-0.5">
                  Restart Marvel Rivals to apply the changes.
                </p>
              </div>
            </div>
          ) : !hasActiveMods ? (
            <div
              style={{
                background: "rgba(148,163,184,0.08)",
                border: "1px solid rgba(148,163,184,0.15)",
                borderRadius: "10px",
                padding: "14px 16px",
                display: "flex",
                alignItems: "center",
                gap: "10px",
              }}
            >
              <Info className="w-5 h-5 text-slate-400 shrink-0" />
              <p className="text-sm text-slate-400">
                No active mods to deactivate.
              </p>
            </div>
          ) : (
            <>
              {/* Heuristic note */}
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "8px",
                }}
              >
                <Info className="w-3.5 h-3.5 mt-0.5 text-slate-500 shrink-0" />
                <p className="text-xs" style={{ color: "var(--color-text-3, #94a3b8)" }}>
                  {suspicious.length > 0
                    ? "Pre-selected mods may have caused the crash based on the call stack. Select which to deactivate."
                    : "One of these active mods may have caused the crash. Select which to deactivate."}
                </p>
              </div>

              {/* Select all / none */}
              <div className="flex items-center gap-3">
                <span
                  className="text-xs font-medium"
                  style={{ color: "var(--color-text-2, #cbd5e1)" }}
                >
                  Active mods ({allActiveMods.length})
                </span>
                <div className="flex gap-2 ml-auto">
                  <button
                    onClick={selectAll}
                    className="text-xs px-2 py-0.5 rounded transition-colors"
                    style={{
                      background: "rgba(255,255,255,0.06)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      color: "var(--color-text-3, #94a3b8)",
                      cursor: "pointer",
                    }}
                  >
                    All
                  </button>
                  <button
                    onClick={selectNone}
                    className="text-xs px-2 py-0.5 rounded transition-colors"
                    style={{
                      background: "rgba(255,255,255,0.06)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      color: "var(--color-text-3, #94a3b8)",
                      cursor: "pointer",
                    }}
                  >
                    None
                  </button>
                </div>
              </div>

              {/* Mod checklist */}
              <div
                style={{
                  maxHeight: "200px",
                  overflowY: "auto",
                  display: "flex",
                  flexDirection: "column",
                  gap: "4px",
                  paddingRight: "2px",
                }}
              >
                {allActiveMods.map((mod) => {
                  const suspiciousEntry = suspicious.find((s) => s.mod.id === mod.id);
                  const matchType = suspiciousEntry?.matchType;
                  const reason = suspiciousEntry?.reason;
                  const isChecked = selectedIds.has(mod.id);
                  const displayName =
                    mod.name || mod.mod_name || `Download #${mod.id}`;

                  // Badge config per tier
                  const badge: { label: string; bg: string; color: string; border: string } | null =
                    matchType === "exact"
                      ? { label: "Exact Skin Match", bg: "rgba(220,38,38,0.25)", color: "#f87171", border: "rgba(220,38,38,0.5)" }
                      : matchType === "skin"
                      ? { label: "Skin Match", bg: "rgba(234,88,12,0.22)", color: "#fb923c", border: "rgba(234,88,12,0.45)" }
                      : matchType === "character"
                      ? { label: "Character Match", bg: "rgba(234,179,8,0.18)", color: "#facc15", border: "rgba(234,179,8,0.35)" }
                      : matchType === "fallback"
                      ? { label: "Suspected", bg: "rgba(234,88,12,0.12)", color: "#fdba74", border: "rgba(234,88,12,0.25)" }
                      : null;

                  // Row background — gradient red for exact/skin, neutral for rest
                  const rowBg =
                    matchType === "exact"
                      ? "rgba(220,38,38,0.16)"
                      : matchType === "skin"
                      ? "rgba(220,38,38,0.10)"
                      : isChecked
                      ? "rgba(220,38,38,0.08)"
                      : "rgba(255,255,255,0.04)";

                  const rowBorder =
                    matchType === "exact"
                      ? "rgba(220,38,38,0.40)"
                      : matchType === "skin"
                      ? "rgba(234,88,12,0.35)"
                      : isChecked
                      ? "rgba(220,38,38,0.20)"
                      : "rgba(255,255,255,0.06)";

                  return (
                    <label
                      key={mod.id}
                      htmlFor={`crash-mod-${mod.id}`}
                      title={reason ?? undefined}
                      style={{
                        display: "flex",
                        alignItems: "flex-start",
                        gap: "10px",
                        padding: "8px 12px",
                        borderRadius: "8px",
                        cursor: "pointer",
                        background: rowBg,
                        border: `1px solid ${rowBorder}`,
                        transition: "all 0.15s ease",
                      }}
                    >
                      <input
                        id={`crash-mod-${mod.id}`}
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleSelect(mod.id)}
                        style={{ accentColor: "#dc2626", cursor: "pointer", marginTop: "2px" }}
                      />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <p
                          className="text-sm font-medium truncate"
                          style={{ color: "var(--color-text-1, #f1f5f9)" }}
                        >
                          {displayName}
                        </p>
                        {mod.active_paks && mod.active_paks.length > 0 && (
                          <p
                            className="text-xs truncate"
                            style={{ color: "var(--color-text-3, #94a3b8)" }}
                          >
                            {mod.active_paks.length} active pak
                            {mod.active_paks.length !== 1 ? "s" : ""}
                          </p>
                        )}
                        {reason && (
                          <p
                            className="text-xs"
                            style={{
                              marginTop: "2px",
                              color:
                                matchType === "exact"
                                  ? "#f87171"
                                  : matchType === "skin"
                                  ? "#fb923c"
                                  : "#facc15",
                              whiteSpace: "normal",
                            }}
                          >
                            {reason}
                          </p>
                        )}
                      </div>
                      {badge && (
                        <span
                          style={{
                            fontSize: "10px",
                            padding: "2px 6px",
                            borderRadius: "99px",
                            background: badge.bg,
                            color: badge.color,
                            border: `1px solid ${badge.border}`,
                            fontWeight: 600,
                            flexShrink: 0,
                            marginTop: "2px",
                          }}
                        >
                          {badge.label}
                        </span>
                      )}
                    </label>
                  );
                })}
              </div>
            </>
          )}

          {/* Action buttons */}
          <div
            style={{
              display: "flex",
              gap: "10px",
              justifyContent: "flex-end",
              paddingTop: "4px",
              flexWrap: "wrap",
            }}
          >
            <Button
              variant="ghost"
              size="sm"
              onClick={onDismiss}
              disabled={isDeactivating}
              style={{ color: "var(--color-text-3, #94a3b8)" }}
            >
              Dismiss
            </Button>

            {!done && hasActiveMods && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleDisableAll}
                  disabled={isDeactivating}
                  style={{
                    borderColor: "rgba(220,38,38,0.4)",
                    color: "#fca5a5",
                  }}
                >
                  {isDeactivating ? (
                    <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                  ) : (
                    <ShieldOff className="w-3.5 h-3.5 mr-1.5" />
                  )}
                  Disable All
                </Button>

                <Button
                  size="sm"
                  onClick={handleDeactivateSelected}
                  disabled={isDeactivating || selectedIds.size === 0}
                  style={{
                    background:
                      selectedIds.size === 0
                        ? "rgba(220,38,38,0.3)"
                        : "linear-gradient(135deg, #dc2626, #ea580c)",
                    color: "white",
                    border: "none",
                    opacity: selectedIds.size === 0 ? 0.5 : 1,
                  }}
                >
                  {isDeactivating ? (
                    <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                  ) : (
                    <PackageX className="w-3.5 h-3.5 mr-1.5" />
                  )}
                  Deactivate Selected
                  {selectedIds.size > 0 && ` (${selectedIds.size})`}
                </Button>
              </>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
