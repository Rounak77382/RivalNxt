import { useEffect, useState, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { toast } from "sonner";
import { invokeReadTextFile } from "../lib/tauri-utils";
import { computeRestoreDiff, type BackupMeta, type ModBackup } from "../lib/backupUtils";
import { setActivePaks, scanActive, refreshConflicts, getLocalDownload, addModCustomTag } from "../lib/api";
import { Loader2, CheckCircle2, XCircle, RotateCcw } from "lucide-react";

interface BackupRestoreModalProps {
  meta: BackupMeta | null;
  installedMods: any[];
  onComplete: () => void;
  onClose: () => void;
}

type RestoreStatus = "idle" | "analyzing" | "restoring" | "finalizing" | "completed" | "error";

export function BackupRestoreModal({ meta, installedMods, onComplete, onClose }: BackupRestoreModalProps) {
  const [status, setStatus] = useState<RestoreStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [currentModName, setCurrentModName] = useState("");
  const [stats, setStats] = useState({ total: 0, completed: 0, missing: 0, toEnable: 0, toDisable: 0 });
  const [errorMsg, setErrorMsg] = useState("");
  
  // Ref to prevent double-execution in StrictMode
  const hasStarted = useRef(false);
  // Track whether onComplete has already been auto-called (to avoid double-call on Done click)
  const completedCalledRef = useRef(false);

  useEffect(() => {
    if (!meta) {
      hasStarted.current = false;
      setStatus("idle");
      setProgress(0);
      return;
    }

    if (hasStarted.current) return;
    hasStarted.current = true;
    
    startRestore(meta);
  }, [meta]);

  const startRestore = async (backupMeta: BackupMeta) => {
    setStatus("analyzing");
    setProgress(5);
    setCurrentModName("Reading backup file...");

    try {
      const content = await invokeReadTextFile(backupMeta.filePath);
      const backup = JSON.parse(content) as ModBackup;
      
      setCurrentModName("Computing differences...");
      setProgress(10);
      const { toEnable, toDisable, missing } = computeRestoreDiff(backup, installedMods);
      
      const totalOperations = toEnable.length + toDisable.length;
      setStats({
        total: totalOperations,
        completed: 0,
        missing: missing.length,
        toEnable: toEnable.length,
        toDisable: toDisable.length,
      });

      if (totalOperations === 0) {
        setStatus("completed");
        setProgress(100);
        setCurrentModName("Mods already match this backup.");
        return;
      }


      setStatus("restoring");
      let completed = 0;
      
      // Process Disables (delta only — no nuclear sweep that would wipe all active mods)
      for (const mod of toDisable) {
        setCurrentModName(`Disabling ${mod.name}...`);
        const downloadIds = mod.sourceDownloadIds || [];
        for (const dlId of downloadIds) {
          await setActivePaks(Number(dlId), []);
        }
        completed++;
        setProgress(10 + Math.floor((completed / totalOperations) * 80));
        setStats(s => ({ ...s, completed }));
      }
      

      // Process Enables
      for (const mod of toEnable) {
        setCurrentModName(`Enabling ${mod.name}...`);

        // Find matching backup entry to get exact activePaks + sourceDownloadIds that were saved
        const backupEntry = backup.mods.find(e => {
          if (e.backendModId != null && mod.backendModId != null) {
            return e.backendModId === mod.backendModId;
          }
          if (e.sourceDownloadIds.length > 0 && Array.isArray(mod.sourceDownloadIds)) {
            return e.sourceDownloadIds.some(id => mod.sourceDownloadIds.includes(id));
          }
          return String(e.modId) === String(mod.id);
        });

        // The download IDs that were part of this mod card AT THE TIME the backup was created.
        // These are the only ones we should enable — any new dlIds merged into the card since
        // the backup was made should remain inactive.
        const backupDlIds = new Set<number>((backupEntry?.sourceDownloadIds || []).map(Number));

        // Build a set of the exact pak basenames that were active when backup was saved
        const backupActivePaks = backupEntry?.activePaks || [];
        const backupActiveBases = new Set(backupActivePaks.map(p => {
          const parts = p.split(/[\/\\]/);
          return parts[parts.length - 1].toLowerCase();
        }));

        const currentDownloadIds = mod.sourceDownloadIds || [];
        for (const dlId of currentDownloadIds) {
          const numId = Number(dlId);

          if (!backupDlIds.has(numId)) {
            // This download was added to the card AFTER the backup — deactivate it
            await setActivePaks(numId, []);
            continue;
          }

          // This download existed at backup time — activate only the right paks
          if (backupActiveBases.size > 0) {
            // New backup format: filter this download's contents to only the recorded active variant paks
            const dl = await getLocalDownload(numId);
            const paks = (dl.contents || []).filter((f: string) => f.toLowerCase().endsWith(".pak"));
            const targetPaks = paks.filter((p: string) => {
              const parts = p.split(/[\/\\]/);
              return backupActiveBases.has(parts[parts.length - 1].toLowerCase());
            });
            await setActivePaks(numId, targetPaks);
          } else {
            // Old backup format (no activePaks field): only enable downloads from the backup's
            // own sourceDownloadIds. Use this download's full pak list as best-effort.
            const dl = await getLocalDownload(numId);
            const paks = (dl.contents || []).filter((f: string) => f.toLowerCase().endsWith(".pak"));
            await setActivePaks(numId, paks);
          }
        }
        completed++;
        setProgress(10 + Math.floor((completed / totalOperations) * 80));
        setStats(s => ({ ...s, completed }));
      }

      // Restore custom tags for enabled mods (best-effort, failures are silent)
      setCurrentModName("Restoring custom tags...");
      for (const mod of toEnable) {
        const backupEntry = backup.mods.find(e => {
          if (e.backendModId != null && mod.backendModId != null) return e.backendModId === mod.backendModId;
          if (e.sourceDownloadIds.length > 0 && Array.isArray(mod.sourceDownloadIds)) return e.sourceDownloadIds.some(id => mod.sourceDownloadIds.includes(id));
          return String(e.modId) === String(mod.id);
        });
        const savedTags = backupEntry?.customTags || [];
        if (savedTags.length === 0) continue;
        const effectiveModId =
          mod.backendModId != null
            ? mod.backendModId
            : Array.isArray(mod.sourceDownloadIds) && mod.sourceDownloadIds.length > 0
              ? -mod.sourceDownloadIds[0]
              : null;
        if (effectiveModId == null) continue;
        for (const tagName of savedTags) {
          try { await addModCustomTag(effectiveModId, tagName); } catch { /* tag may already exist */ }
        }
      }

      setStatus("finalizing");
      setCurrentModName("Synchronizing filesystem...");
      setProgress(95);
      
      await scanActive();
      await refreshConflicts();
      
      setProgress(100);
      setStatus("completed");
      setCurrentModName("Restore completed successfully!");
      
      if (missing.length > 0) {
        toast.warning(`${missing.length} mod${missing.length > 1 ? "s" : ""} not installed`, {
          description: missing.slice(0, 5).join(", ") + (missing.length > 5 ? ` +${missing.length - 5} more` : ""),
        });
      }
      toast.success(`Backup "${backupMeta.name}" applied — ${completed} mod${completed !== 1 ? "s" : ""} updated`);

      // Auto-notify parent immediately so mod list refreshes without waiting for user to click Done
      if (!completedCalledRef.current) {
        completedCalledRef.current = true;
        onComplete();
      }
      
    } catch (err: any) {
      console.error("Restore failed:", err);
      setStatus("error");
      setErrorMsg(err?.message || "An unknown error occurred during restore.");
    }
  };

  const handleClose = () => {
    if (status === "restoring" || status === "analyzing" || status === "finalizing") {
      // Prevent closing while active
      return;
    }
    // Only call onComplete here if it wasn't already auto-called on success
    if (status === "completed" && !completedCalledRef.current) {
      completedCalledRef.current = true;
      onComplete();
    }
    onClose();
  };

  const accentGradient =
    status === "completed"
      ? "linear-gradient(90deg, #22c55e, #10b981)"
      : status === "error"
      ? "linear-gradient(90deg, #ef4444, #dc2626)"
      : status === "analyzing"
      ? "linear-gradient(90deg, #8b5cf6, #6366f1)"
      : "linear-gradient(90deg, #f59e0b, #f97316)";

  const iconBg =
    status === "completed"
      ? "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(16,185,129,0.15))"
      : status === "error"
      ? "linear-gradient(135deg, rgba(239,68,68,0.15), rgba(220,38,38,0.15))"
      : status === "analyzing"
      ? "linear-gradient(135deg, rgba(139,92,246,0.15), rgba(99,102,241,0.15))"
      : "linear-gradient(135deg, rgba(245,158,11,0.15), rgba(249,115,22,0.15))";

  const cardBg =
    status === "completed"
      ? "linear-gradient(135deg, rgba(34,197,94,0.08), rgba(16,185,129,0.08))"
      : status === "error"
      ? "linear-gradient(135deg, rgba(239,68,68,0.08), rgba(220,38,38,0.08))"
      : status === "analyzing"
      ? "linear-gradient(135deg, rgba(139,92,246,0.08), rgba(99,102,241,0.08))"
      : "linear-gradient(135deg, rgba(245,158,11,0.08), rgba(249,115,22,0.08))";

  const cardBorder =
    status === "completed"
      ? "1px solid rgba(34,197,94,0.2)"
      : status === "error"
      ? "1px solid rgba(239,68,68,0.2)"
      : status === "analyzing"
      ? "1px solid rgba(139,92,246,0.2)"
      : "1px solid rgba(245,158,11,0.2)";

  return (
    <Dialog open={!!meta} onOpenChange={(open) => !open && handleClose()}>
      <DialogContent
        className="w-[50vw] max-w-[50vw]"
        style={{
          width: "50vw",
          maxWidth: "50vw",
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
          }}
        />

        <DialogHeader className="pt-2">
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            {/* Icon */}
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
              {status === "completed" ? (
                <CheckCircle2 className="h-6 w-6 text-emerald-400" />
              ) : status === "error" ? (
                <XCircle className="h-6 w-6 text-red-400" />
              ) : status === "analyzing" ? (
                <Loader2 className="h-6 w-6 text-violet-400 animate-spin" />
              ) : (
                <RotateCcw className="h-6 w-6 text-amber-400 animate-spin" />
              )}
            </div>

            {/* Title */}
            <div>
              <DialogTitle className="text-lg font-semibold">
                {status === "analyzing"
                  ? "Analyzing Backup…"
                  : status === "restoring"
                  ? "Restoring Mod States…"
                  : status === "finalizing"
                  ? "Completing Restore…"
                  : status === "completed"
                  ? "Restore Complete!"
                  : status === "error"
                  ? "Restore Failed"
                  : "Restoring Backup"}
              </DialogTitle>
              <p className="text-sm text-muted-foreground" style={{ marginTop: "2px" }}>
                {meta ? `Applying snapshot: ${meta.name}` : ""}
              </p>
            </div>
          </div>
        </DialogHeader>

        <div className="py-2 flex flex-col gap-4">
          {/* ── ERROR VIEW ── */}
          {status === "error" && (
            <div
              style={{
                padding: "16px",
                borderRadius: "12px",
                background: cardBg,
                border: cardBorder,
                display: "flex",
                alignItems: "flex-start",
                gap: "10px",
                marginTop: "4px",
              }}
            >
              <XCircle className="h-5 w-5 text-red-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-semibold text-red-300">Error Occurred</p>
                <p className="text-xs text-muted-foreground mt-1 font-mono break-words leading-relaxed">
                  {errorMsg}
                </p>
              </div>
            </div>
          )}

          {/* ── COMPLETED VIEW ── */}
          {status === "completed" && (
            <div
              style={{
                padding: "16px",
                borderRadius: "12px",
                background: cardBg,
                border: cardBorder,
                display: "flex",
                flexDirection: "column",
                gap: "10px",
                marginTop: "4px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                <span className="text-sm font-semibold text-emerald-300">
                  {meta?.name || "Backup Restored"}
                </span>
              </div>
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                <Badge variant="secondary" className="text-xs gap-1">
                  <CheckCircle2 className="h-3 w-3" /> {stats.completed} updated
                </Badge>
                {stats.missing > 0 && (
                  <Badge variant="outline" className="bg-amber-500/10 text-amber-500 border-amber-500/20 text-xs">
                    {stats.missing} missing
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Your mod states have been successfully updated to match this backup.
              </p>
            </div>
          )}

          {/* ── IN PROGRESS VIEW ── */}
          {(status === "analyzing" || status === "restoring" || status === "finalizing" || status === "idle") && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "16px",
                marginTop: "4px",
                padding: "20px",
                borderRadius: "12px",
                background: cardBg,
                border: cardBorder,
                boxShadow: "0 8px 32px rgba(0, 0, 0, 0.2)",
                backdropFilter: "blur(8px)",
                WebkitBackdropFilter: "blur(8px)",
                transition: "all 0.4s ease",
              }}
            >
              {/* Progress Bar & Percentage */}
              <div className="space-y-3">
                <div className="flex justify-between items-center text-xs font-semibold">
                  <span
                    style={{
                      color:
                        status === "analyzing"
                          ? "#a78bfa"
                          : "#f59e0b",
                      fontSize: "14px",
                      fontWeight: 700,
                    }}
                  >
                    {Math.round(progress)}%
                  </span>
                  {stats.total > 0 && (
                    <span className="text-muted-foreground font-semibold">
                      {stats.completed} / {stats.total} modifications
                    </span>
                  )}
                </div>

                {/* Custom Gradient Progress Bar */}
                <div
                  style={{
                    width: "100%",
                    height: "8px",
                    borderRadius: "9999px",
                    background: "hsl(var(--secondary) / 0.5)",
                    overflow: "hidden",
                    position: "relative",
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      left: 0,
                      top: 0,
                      bottom: 0,
                      width: `${progress}%`,
                      background: accentGradient,
                      borderRadius: "9999px",
                      transition: "width 0.35s cubic-bezier(0.4, 0, 0.2, 1)",
                    }}
                  />
                </div>
              </div>

              {/* Status info box */}
              <div
                style={{
                  padding: "12px 14px",
                  borderRadius: "10px",
                  background: "hsl(var(--background))",
                  border: "1px solid hsl(var(--border) / 0.6)",
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  boxShadow: "0 4px 12px rgba(0, 0, 0, 0.08)",
                }}
              >
                <Loader2
                  className="h-4 w-4 animate-spin shrink-0"
                  style={{
                    color:
                      status === "analyzing"
                        ? "#8b5cf6"
                        : "#f59e0b",
                  }}
                />
                <span className="text-sm font-medium text-foreground truncate max-w-full">
                  {currentModName || "Preparing restore..."}
                </span>
              </div>

              {/* Modifications Stats Grid - identical to BackupModal's style */}
              {(stats.toEnable > 0 || stats.toDisable > 0 || stats.missing > 0) && (
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "4px" }}>
                  {stats.toEnable > 0 && (
                    <div
                      style={{
                        flex: 1,
                        padding: "10px 12px",
                        borderRadius: "8px",
                        background: "rgba(34,197,94,0.08)",
                        border: "1px solid rgba(34,197,94,0.15)",
                        minWidth: "100px",
                        textAlign: "center",
                      }}
                    >
                      <p className="text-lg font-bold text-emerald-400 leading-none">{stats.toEnable}</p>
                      <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mt-1">to enable</p>
                    </div>
                  )}
                  {stats.toDisable > 0 && (
                    <div
                      style={{
                        flex: 1,
                        padding: "10px 12px",
                        borderRadius: "8px",
                        background: "rgba(239,68,68,0.08)",
                        border: "1px solid rgba(239,68,68,0.15)",
                        minWidth: "100px",
                        textAlign: "center",
                      }}
                    >
                      <p className="text-lg font-bold text-red-400 leading-none">{stats.toDisable}</p>
                      <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mt-1">to disable</p>
                    </div>
                  )}
                  {stats.missing > 0 && (
                    <div
                      style={{
                        flex: 1,
                        padding: "10px 12px",
                        borderRadius: "8px",
                        background: "rgba(245,158,11,0.08)",
                        border: "1px solid rgba(245,158,11,0.15)",
                        minWidth: "100px",
                        textAlign: "center",
                      }}
                    >
                      <p className="text-lg font-bold text-amber-400 leading-none">{stats.missing}</p>
                      <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mt-1">missing</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "12px" }}>
          <Button
            onClick={handleClose}
            disabled={status === "analyzing" || status === "restoring" || status === "finalizing"}
            style={{
              background:
                status === "error"
                  ? "linear-gradient(135deg, #ef4444, #dc2626)"
                  : status === "completed"
                  ? "linear-gradient(135deg, #22c55e, #10b981)"
                  : undefined,
              border: "none",
              fontWeight: 600,
            }}
            variant={status === "error" || status === "completed" ? "default" : "secondary"}
            className="gap-2"
          >
            {status === "analyzing" || status === "restoring" || status === "finalizing" ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Please wait…
              </>
            ) : status === "error" ? (
              "Close"
            ) : (
              "Done"
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

