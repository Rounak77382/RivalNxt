import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import {
  Archive,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  FolderOpen,
  Download,
  Power,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  buildBackupFromMods,
  generateBackupName,
  addBackupMeta,
  computeRestoreDiff,
  type ModBackup,
  type BackupMeta,
} from "../lib/backupUtils";
import {
  invokeSaveFileDialog,
  invokeSaveTextFile,
  invokeOpenFileDialog,
  invokeReadTextFile,
} from "../lib/tauri-utils";
import { setActivePaks, scanActive, refreshConflicts, getLocalDownload, listDownloads } from "../lib/api";

interface BackupModalProps {
  open: boolean;
  onClose: () => void;
  mods: any[];
  onToggleMod: (modId: string) => void;
  onBackupCreated?: () => void;
}

type ModalView = "home" | "creating" | "created" | "restoring" | "restored";

interface RestorePreview {
  backup: ModBackup;
  filePath: string;
  toEnable: any[];
  toDisable: any[];
  missing: string[];
}

export function BackupModal({
  open,
  onClose,
  mods,
  onToggleMod,
  onBackupCreated,
}: BackupModalProps) {
  const [view, setView] = useState<ModalView>("home");
  const [isWorking, setIsWorking] = useState(false);
  const [restorePreview, setRestorePreview] = useState<RestorePreview | null>(
    null
  );
  const [lastSavedMeta, setLastSavedMeta] = useState<BackupMeta | null>(null);

  // Reset to home when modal opens
  useEffect(() => {
    if (open) {
      setView("home");
      setRestorePreview(null);
      setLastSavedMeta(null);
    }
  }, [open]);

  const installedMods = mods.filter((m) => m.isInstalled);
  const activeMods = installedMods.filter((m) => m.isActive);

  // ── Create backup ────────────────────────────────────────────────────────
  const handleCreateBackup = async () => {
    setIsWorking(true);
    setView("creating");
    try {
      const name = generateBackupName();
      const backup = buildBackupFromMods(mods, name);
      const defaultFileName = `rivalnxt-backup-${name
        .replace(/[: ]/g, "-")
        .replace(/--+/g, "-")}.json`;

      const path = await invokeSaveFileDialog(defaultFileName, ["json"]);
      if (!path) {
        // User cancelled
        setView("home");
        return;
      }

      await invokeSaveTextFile(path, JSON.stringify(backup, null, 2));

      const meta: BackupMeta = {
        id: backup.id,
        name: backup.name,
        createdAt: backup.createdAt,
        filePath: path,
        totalMods: backup.totalMods,
        activeMods: backup.activeMods,
      };
      addBackupMeta(meta);
      setLastSavedMeta(meta);
      setView("created");
      toast.success(`Backup saved: ${name}`, {
        description: `${backup.totalMods} mods snapshotted (${backup.activeMods} active)`,
      });
      if (onBackupCreated) {
        onBackupCreated();
      }
    } catch (err: any) {
      toast.error(`Failed to save backup: ${err?.message ?? String(err)}`);
      setView("home");
    } finally {
      setIsWorking(false);
    }
  };

  // ── Load backup for preview ───────────────────────────────────────────────
  const handleLoadBackup = async () => {
    setIsWorking(true);
    try {
      const path = await invokeOpenFileDialog(["json"]);
      if (!path) return;

      const content = await invokeReadTextFile(path);
      const backup = JSON.parse(content) as ModBackup;

      if (!backup.mods || !Array.isArray(backup.mods)) {
        toast.error("Invalid backup file — no mod list found");
        return;
      }

      const { toEnable, toDisable, missing } = computeRestoreDiff(
        backup,
        mods
      );
      setRestorePreview({ backup, filePath: path, toEnable, toDisable, missing });
      setView("restoring");
    } catch (err: any) {
      toast.error(`Failed to load backup: ${err?.message ?? String(err)}`);
    } finally {
      setIsWorking(false);
    }
  };

  // ── Apply restore ─────────────────────────────────────────────────────────
  const handleApplyRestore = async () => {
    if (!restorePreview) return;
    const { backup, toEnable, toDisable, missing } = restorePreview;

    setIsWorking(true);
    setView("restoring");

    try {
      // Step 1 – Disable every active installed mod first to ensure a clean sweep of the ~mods folder
      try {
        await scanActive();
        const allDownloads = await listDownloads(1000);
        const activeDownloads = allDownloads.filter(
          (dl) => dl.active_paks && dl.active_paks.length > 0
        );
        for (const dl of activeDownloads) {
          await setActivePaks(Number(dl.id), []);
        }
      } catch (err) {
        console.warn("Failed to perform complete cleanup sweep, falling back to installedMods filter:", err);
        const activeInstalled = installedMods.filter(
          (m) => m.isActive !== false && m.isInstalled
        );
        for (const mod of activeInstalled) {
          for (const dlId of mod.sourceDownloadIds || []) {
            await setActivePaks(Number(dlId), []);
          }
        }
      }

      let changed = 0;

      // Step 2 – Enable each mod in toEnable with its saved active variant paks
      for (const mod of toEnable) {
        // Find matching backup entry to get exact activePaks
        const backupEntry = backup.mods.find(e => {
          if (e.backendModId != null && mod.backendModId != null) {
            return e.backendModId === mod.backendModId;
          }
          if (e.sourceDownloadIds.length > 0 && Array.isArray(mod.sourceDownloadIds)) {
            return e.sourceDownloadIds.some(id => mod.sourceDownloadIds.includes(id));
          }
          return String(e.modId) === String(mod.id);
        });

        const backupDlIds = new Set<number>((backupEntry?.sourceDownloadIds || []).map(Number));
        const backupActivePaks = backupEntry?.activePaks || [];
        const backupActiveBases = new Set(backupActivePaks.map(p => {
          const parts = p.split(/[\/\\]/);
          return parts[parts.length - 1].toLowerCase();
        }));

        const currentDownloadIds = mod.sourceDownloadIds || [];
        for (const dlId of currentDownloadIds) {
          const numId = Number(dlId);
          if (!backupDlIds.has(numId)) {
            await setActivePaks(numId, []);
            continue;
          }

          if (backupActiveBases.size > 0) {
            const dl = await getLocalDownload(numId);
            const paks = (dl.contents || []).filter((f: string) => f.toLowerCase().endsWith(".pak"));
            const targetPaks = paks.filter((p: string) => {
              const parts = p.split(/[\/\\]/);
              return backupActiveBases.has(parts[parts.length - 1].toLowerCase());
            });
            await setActivePaks(numId, targetPaks);
          } else {
            const dl = await getLocalDownload(numId);
            const paks = (dl.contents || []).filter((f: string) => f.toLowerCase().endsWith(".pak"));
            await setActivePaks(numId, paks);
          }
        }
        changed++;
      }

      // Step 3 – Single filesystem sync
      await scanActive();
      await refreshConflicts();

      setView("restored");

      if (missing.length > 0) {
        toast.warning(
          `${missing.length} mod${missing.length > 1 ? "s" : ""} from this backup not installed`,
          {
            description: missing.slice(0, 5).join(", ") +
              (missing.length > 5 ? ` and ${missing.length - 5} more…` : ""),
            duration: 8000,
          }
        );
      }

      const totalChanges = toEnable.length + toDisable.length;
      if (totalChanges > 0) {
        toast.success(
          `Backup restored — ${totalChanges} mod${totalChanges > 1 ? "s" : ""} updated`
        );
      } else {
        toast.info("Mods already match this backup — no changes needed");
      }
    } catch (err: any) {
      console.error("Restore failed:", err);
      toast.error(`Restore failed: ${err?.message ?? String(err)}`);
      setView("home");
    } finally {
      setIsWorking(false);
    }
  };

  // ── Accent color ──────────────────────────────────────────────────────────
  const accentGradient =
    view === "created" || view === "restored"
      ? "linear-gradient(90deg, #22c55e, #10b981)"
      : view === "restoring"
        ? "linear-gradient(90deg, #f59e0b, #f97316)"
        : "linear-gradient(90deg, #8b5cf6, #6366f1)";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        className="max-w-lg"
        style={{
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
                background:
                  view === "created" || view === "restored"
                    ? "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(16,185,129,0.15))"
                    : view === "restoring"
                      ? "linear-gradient(135deg, rgba(245,158,11,0.15), rgba(249,115,22,0.15))"
                      : "linear-gradient(135deg, rgba(139,92,246,0.15), rgba(99,102,241,0.15))",
                flexShrink: 0,
                transition: "background 0.4s ease",
              }}
            >
              {view === "creating" ? (
                <Loader2 className="h-6 w-6 text-violet-400 animate-spin" />
              ) : view === "created" ? (
                <ShieldCheck className="h-6 w-6 text-emerald-400" />
              ) : view === "restored" ? (
                <CheckCircle2 className="h-6 w-6 text-emerald-400" />
              ) : view === "restoring" ? (
                <RotateCcw className="h-6 w-6 text-amber-400" />
              ) : (
                <Archive className="h-6 w-6 text-violet-400" />
              )}
            </div>

            {/* Title */}
            <div>
              <DialogTitle className="text-lg font-semibold">
                {view === "home"
                  ? "Mod Backup"
                  : view === "creating"
                    ? "Saving Backup…"
                    : view === "created"
                      ? "Backup Saved!"
                      : view === "restoring"
                        ? "Restore Preview"
                        : "Restore Complete"}
              </DialogTitle>
              <p className="text-sm text-muted-foreground" style={{ marginTop: "2px" }}>
                {view === "home"
                  ? `${activeMods.length} active mods · ${installedMods.length} installed`
                  : view === "creating"
                    ? "Choosing save location…"
                    : view === "created"
                      ? `${lastSavedMeta?.totalMods ?? 0} mods saved (${lastSavedMeta?.activeMods ?? 0} active)`
                      : view === "restoring"
                        ? restorePreview?.backup.name ?? ""
                        : "Mod states have been updated"}
              </p>
            </div>
          </div>
        </DialogHeader>

        {/* ── HOME VIEW ── */}
        {view === "home" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px", paddingTop: "4px" }}>
            {/* Stats strip */}
            <div
              style={{
                display: "flex",
                gap: "12px",
                padding: "14px 16px",
                borderRadius: "12px",
                background: "hsl(var(--accent) / 0.3)",
                border: "1px solid hsl(var(--border) / 0.5)",
              }}
            >
              <div style={{ flex: 1, textAlign: "center" }}>
                <p className="text-2xl font-bold text-foreground">{activeMods.length}</p>
                <p className="text-xs text-muted-foreground mt-0.5">Active Mods</p>
              </div>
              <div style={{ width: "1px", background: "hsl(var(--border))" }} />
              <div style={{ flex: 1, textAlign: "center" }}>
                <p className="text-2xl font-bold text-foreground">{installedMods.length}</p>
                <p className="text-xs text-muted-foreground mt-0.5">Installed</p>
              </div>
            </div>

            {/* Create backup */}
            <div
              style={{
                padding: "16px",
                borderRadius: "12px",
                background: "linear-gradient(135deg, rgba(139,92,246,0.08), rgba(99,102,241,0.08))",
                border: "1px solid rgba(139,92,246,0.2)",
              }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
                <div
                  style={{
                    width: "36px",
                    height: "36px",
                    borderRadius: "8px",
                    background: "rgba(139,92,246,0.15)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <Archive className="h-4 w-4 text-violet-400" />
                </div>
                <div style={{ flex: 1 }}>
                  <p className="text-sm font-semibold text-foreground">Create Snapshot</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Save your current mod loadout to a file you can restore later.
                  </p>
                  <Button
                    size="sm"
                    onClick={handleCreateBackup}
                    disabled={isWorking || installedMods.length === 0}
                    style={{
                      marginTop: "10px",
                      background: "linear-gradient(135deg, #8b5cf6, #6366f1)",
                      border: "none",
                      fontWeight: 600,
                    }}
                    className="gap-2"
                  >
                    <Archive className="h-3.5 w-3.5" />
                    Save Backup…
                  </Button>
                </div>
              </div>
            </div>

            {/* Restore */}
            <div
              style={{
                padding: "16px",
                borderRadius: "12px",
                background: "linear-gradient(135deg, rgba(245,158,11,0.08), rgba(249,115,22,0.08))",
                border: "1px solid rgba(245,158,11,0.2)",
              }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
                <div
                  style={{
                    width: "36px",
                    height: "36px",
                    borderRadius: "8px",
                    background: "rgba(245,158,11,0.15)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <RotateCcw className="h-4 w-4 text-amber-400" />
                </div>
                <div style={{ flex: 1 }}>
                  <p className="text-sm font-semibold text-foreground">Restore Backup</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    Browse for a backup file and preview what will change before applying.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleLoadBackup}
                    disabled={isWorking}
                    className="gap-2"
                    style={{ borderColor: "rgba(245,158,11,0.4)", color: "#f59e0b", marginTop: "10px" }}
                  >
                    <FolderOpen className="h-3.5 w-3.5" />
                    Browse for Backup…
                  </Button>
                </div>
              </div>
            </div>

            <p className="text-xs text-center text-muted-foreground/60 pb-1">
              Backups also appear in the <strong>Collections</strong> tab for quick switching.
            </p>
          </div>
        )}

        {/* ── CREATING VIEW (spinner) ── */}
        {view === "creating" && (
          <div className="flex flex-col items-center justify-center py-10 gap-4">
            <Loader2 className="h-10 w-10 text-violet-400 animate-spin" />
            <p className="text-sm text-muted-foreground">Opening save dialog…</p>
          </div>
        )}

        {/* ── CREATED VIEW ── */}
        {view === "created" && lastSavedMeta && (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px", paddingTop: "4px" }}>
            <div
              style={{
                padding: "16px",
                borderRadius: "12px",
                background: "rgba(34,197,94,0.08)",
                border: "1px solid rgba(34,197,94,0.2)",
                display: "flex",
                flexDirection: "column",
                gap: "10px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                <span className="text-sm font-semibold text-emerald-300">{lastSavedMeta.name}</span>
              </div>
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                <Badge variant="secondary" className="text-xs gap-1">
                  <Power className="h-3 w-3" /> {lastSavedMeta.activeMods} active
                </Badge>
                <Badge variant="secondary" className="text-xs gap-1">
                  <Download className="h-3 w-3" /> {lastSavedMeta.totalMods} total
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground break-all font-mono">
                {lastSavedMeta.filePath}
              </p>
            </div>
            <p className="text-xs text-muted-foreground text-center pb-1">
              This backup is now listed in your <strong>Collections</strong> tab.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <Button variant="outline" size="sm" onClick={() => setView("home")} className="gap-2">
                <Archive className="h-3.5 w-3.5" /> Another Backup
              </Button>
              <Button
                size="sm"
                onClick={onClose}
                style={{ background: "linear-gradient(135deg, #22c55e, #10b981)", border: "none", fontWeight: 600 }}
              >
                Done
              </Button>
            </div>
          </div>
        )}

        {/* ── RESTORE PREVIEW VIEW ── */}
        {view === "restoring" && restorePreview && (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px", paddingTop: "4px" }}>
            {/* Changes summary */}
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
              {restorePreview.toEnable.length > 0 && (
                <div
                  style={{
                    flex: 1,
                    padding: "12px 14px",
                    borderRadius: "10px",
                    background: "rgba(34,197,94,0.08)",
                    border: "1px solid rgba(34,197,94,0.2)",
                    minWidth: "120px",
                  }}
                >
                  <p className="text-xl font-bold text-emerald-400">{restorePreview.toEnable.length}</p>
                  <p className="text-xs text-muted-foreground">will enable</p>
                </div>
              )}
              {restorePreview.toDisable.length > 0 && (
                <div
                  style={{
                    flex: 1,
                    padding: "12px 14px",
                    borderRadius: "10px",
                    background: "rgba(239,68,68,0.08)",
                    border: "1px solid rgba(239,68,68,0.2)",
                    minWidth: "120px",
                  }}
                >
                  <p className="text-xl font-bold text-red-400">{restorePreview.toDisable.length}</p>
                  <p className="text-xs text-muted-foreground">will disable</p>
                </div>
              )}
              {restorePreview.missing.length > 0 && (
                <div
                  style={{
                    flex: 1,
                    padding: "12px 14px",
                    borderRadius: "10px",
                    background: "rgba(245,158,11,0.08)",
                    border: "1px solid rgba(245,158,11,0.2)",
                    minWidth: "120px",
                  }}
                >
                  <p className="text-xl font-bold text-amber-400">{restorePreview.missing.length}</p>
                  <p className="text-xs text-muted-foreground">not installed</p>
                </div>
              )}
              {restorePreview.toEnable.length === 0 && restorePreview.toDisable.length === 0 && (
                <div
                  style={{
                    flex: 1,
                    padding: "12px 14px",
                    borderRadius: "10px",
                    background: "hsl(var(--accent) / 0.3)",
                    border: "1px solid hsl(var(--border) / 0.5)",
                  }}
                >
                  <p className="text-sm font-semibold text-foreground">Already up to date</p>
                  <p className="text-xs text-muted-foreground">No mod state changes needed.</p>
                </div>
              )}
            </div>

            {/* Missing mods warning */}
            {restorePreview.missing.length > 0 && (
              <div
                style={{
                  padding: "12px 14px",
                  borderRadius: "10px",
                  background: "rgba(245,158,11,0.06)",
                  border: "1px solid rgba(245,158,11,0.2)",
                  display: "flex",
                  gap: "8px",
                  alignItems: "flex-start",
                }}
              >
                <AlertTriangle className="h-4 w-4 text-amber-400 mt-0.5 flex-shrink-0" />
                <div>
                  <p className="text-xs font-semibold text-amber-300">Not installed:</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {restorePreview.missing.slice(0, 4).join(", ")}
                    {restorePreview.missing.length > 4 ? ` +${restorePreview.missing.length - 4} more` : ""}
                  </p>
                  <p className="text-xs text-muted-foreground/70 mt-1">
                    Download these mods from Nexus to fully restore this backup.
                  </p>
                </div>
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <Button variant="ghost" size="sm" onClick={() => setView("home")}>
                Back
              </Button>
              <Button
                size="sm"
                onClick={handleApplyRestore}
                style={{
                  background: "linear-gradient(135deg, #f59e0b, #f97316)",
                  border: "none",
                  fontWeight: 600,
                }}
                className="gap-2"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Apply Restore
              </Button>
            </div>
          </div>
        )}

        {/* ── RESTORED VIEW ── */}
        {view === "restored" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px", paddingTop: "4px" }}>
            <div
              style={{
                padding: "16px",
                borderRadius: "12px",
                background: "rgba(34,197,94,0.08)",
                border: "1px solid rgba(34,197,94,0.2)",
                display: "flex",
                alignItems: "center",
                gap: "12px",
              }}
            >
              <CheckCircle2 className="h-6 w-6 text-emerald-400 flex-shrink-0" />
              <div>
                <p className="text-sm font-semibold text-emerald-300">Restore applied!</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Your mod active states have been updated to match the backup.
                </p>
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <Button
                size="sm"
                onClick={onClose}
                style={{
                  background: "linear-gradient(135deg, #22c55e, #10b981)",
                  border: "none",
                  fontWeight: 600,
                }}
              >
                Done
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
