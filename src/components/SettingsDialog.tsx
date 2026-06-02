import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";

import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { open } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import { Switch } from "./ui/switch";
import { ScrollArea } from "./ui/scroll-area";
import { Loader2, RefreshCw, Play, Folder, ExternalLink } from "lucide-react";
import { toast } from "sonner";

import {
  type ApiSettings,
  type ApiSettingsTaskResponse,
  type SettingsTask,
} from "../lib/api";
import { NxmProtocolSettings } from "./NxmProtocolSettings";
import { TaskOutputSummary } from "./TaskOutputSummary";
export type SettingsFormValues = {
  data_dir: string;
  marvel_rivals_root: string;
  marvel_rivals_local_downloads_root: string;
  nexus_api_key: string;
  aes_key_hex: string;
  allow_direct_api_downloads: boolean;
  seven_zip_bin: string;
};

interface SettingsDialogProps {
  open: boolean;
  loading: boolean;
  saving: boolean;
  settings: ApiSettings | null;
  taskBusy: SettingsTask | null;
  taskJobs: Partial<Record<SettingsTask, ApiSettingsTaskResponse>>;
  onOpenChange: (open: boolean) => void;
  onRefresh: () => void;
  onSubmit: (values: SettingsFormValues) => Promise<void>;
  onRunTask: (task: SettingsTask) => void;
}

const EMPTY_SETTINGS: SettingsFormValues = {
  data_dir: "",
  marvel_rivals_root: "",
  marvel_rivals_local_downloads_root: "",
  nexus_api_key: "",
  aes_key_hex: "",
  allow_direct_api_downloads: false,
  seven_zip_bin: "",
};

const TASKS: Array<{
  key: SettingsTask;
  label: string;
  description: string;
}> = [
  {
    key: "bootstrap_rebuild" as SettingsTask,
    label: "Initial Database Build",
    description:
      "Run the full bootstrap flow: rebuild downloads, ingest pak assets, sync Nexus metadata, rebuild tags, and refresh conflicts.",
  },
  {
    key: "ingest_download_assets" as SettingsTask,
    label: "Rebuild Local Downloads",
    description:
      "Extract local download archives and refresh per-pak metadata for each mod.",
  },
  {
    key: "scan_active_mods" as SettingsTask,
    label: "Rescan Active Mods",
    description:
      "Inspect the ~mods directory to rebuild the list of active pak files.",
  },
  {
    key: "sync_nexus" as SettingsTask,
    label: "Sync Nexus API",
    description:
      "Pull the latest metadata, files, and changelogs from the Nexus Mods API for linked mods.",
  },
  {
    key: "rebuild_tags" as SettingsTask,
    label: "Rebuild Tags",
    description:
      "Regenerate asset and pak tags so browsing and filters reflect new content.",
  },
  {
    key: "rebuild_conflicts" as SettingsTask,
    label: "Rebuild Conflicts",
    description:
      "Refresh conflict tables to reflect the current intersection of mods and assets.",
  },
  {
    key: "rebuild_character_data" as SettingsTask,
    label: "Rebuild Character & Skin Data",
    description:
      "Re-extract character and skin names from Marvel Rivals PAK files and update the database.",
  },
  {
    key: "delete_outdated_versions" as SettingsTask,
    label: "Delete Outdated Versions",
    description:
      "Clean up older versions of mods when a newer version is already installed locally.",
  },
];

function formatTimestamp(value?: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString();
}

export function SettingsDialog({
  open,
  loading,
  saving,
  settings,
  taskBusy,
  taskJobs,
  onOpenChange,
  onRefresh,
  onSubmit,
  onRunTask,
}: SettingsDialogProps) {
  const [formValues, setFormValues] =
    useState<SettingsFormValues>(EMPTY_SETTINGS);

  useEffect(() => {
    if (!open) {
      setFormValues(EMPTY_SETTINGS);
      return;
    }
    if (settings) {
      setFormValues({
        data_dir: settings.data_dir ?? "",
        marvel_rivals_root: settings.marvel_rivals_root ?? "",
        marvel_rivals_local_downloads_root:
          settings.marvel_rivals_local_downloads_root ?? "",
        nexus_api_key: settings.nexus_api_key ?? "",
        aes_key_hex: settings.aes_key_hex ?? "",
        allow_direct_api_downloads: settings.allow_direct_api_downloads,
        seven_zip_bin: settings.seven_zip_bin ?? "",
      });

      // Auto-detect sidecars if not set
      const autoDetectSidecars = async () => {
        // Add a small delay to ensure backend is ready
        setTimeout(async () => {
          // Auto-detect archive tool if seven_zip_bin is not set
          if (!settings.seven_zip_bin || settings.seven_zip_bin.trim() === "") {
            try {
              const result = await invoke<{
                success: boolean;
                name?: string;
                executable?: string;
                message: string;
                already_in_path?: boolean;
              }>("detect_archive_tool");

              if (result.success && result.executable) {
                setFormValues((prev) => ({
                  ...prev,
                  seven_zip_bin: result.executable!,
                }));

                if (result.already_in_path) {
                  toast.info(`${result.name} detected`, {
                    description: `Already in PATH: ${result.executable}`,
                    duration: 4000,
                  });
                } else {
                  toast.success(`${result.name} detected and added to PATH`, {
                    description: `Executable: ${result.executable}`,
                    duration: 4000,
                  });
                }
              }
            } catch (error) {
              console.error("Archive tool auto-detect failed:", error);
              // Silently fail - user can manually detect later
            }
          }
        }, 100); // Small delay to ensure backend is ready
      };

      // Auto-detect Marvel Rivals path if not set
      const detectMarvelRivalsPath = async () => {
        if (
          !settings.marvel_rivals_root ||
          settings.marvel_rivals_root.trim() === ""
        ) {
          try {
            const result = await invoke<{
              success: boolean;
              path?: string;
              message: string;
            }>("detect_marvel_rivals_path");

            if (result.success && result.path) {
              setFormValues((prev) => ({
                ...prev,
                marvel_rivals_root: result.path!,
              }));
              toast.success("Marvel Rivals detected", {
                description: `Found at: ${result.path}`,
                duration: 4000,
              });
            }
          } catch (error) {
            console.log("Marvel Rivals detection failed:", error);
            // Silently fail - user can manually detect later
          }
        }
      };

      // Run detection
      detectMarvelRivalsPath();

      autoDetectSidecars();
    }
  }, [open, settings]);

  const hasChanges = useMemo(() => {
    if (!settings) return false;

    const stringKeys: Array<
      Exclude<keyof SettingsFormValues, "allow_direct_api_downloads">
    > = [
      "data_dir",
      "marvel_rivals_root",
      "marvel_rivals_local_downloads_root",
      "nexus_api_key",
      "aes_key_hex",
      "seven_zip_bin",
    ];

    const baseline: SettingsFormValues = {
      data_dir: settings.data_dir ?? "",
      marvel_rivals_root: settings.marvel_rivals_root ?? "",
      marvel_rivals_local_downloads_root:
        settings.marvel_rivals_local_downloads_root ?? "",
      nexus_api_key: settings.nexus_api_key ?? "",
      aes_key_hex: settings.aes_key_hex ?? "",
      allow_direct_api_downloads: settings.allow_direct_api_downloads,
      seven_zip_bin: settings.seven_zip_bin ?? "",
    };

    const stringChanged = stringKeys.some((key) => {
      const current = (formValues[key] ?? "").trim();
      const original = (baseline[key] ?? "").trim();
      return current !== original;
    });

    const boolChanged =
      baseline.allow_direct_api_downloads !==
      formValues.allow_direct_api_downloads;

    return stringChanged || boolChanged;
  }, [formValues, settings]);

  const handleInputChange =
    (field: Exclude<keyof SettingsFormValues, "allow_direct_api_downloads">) =>
    (event: ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setFormValues((prev) => ({ ...prev, [field]: value }));
    };

  const handleToggleChange = (value: boolean) => {
    setFormValues((prev) => ({
      ...prev,
      allow_direct_api_downloads: value,
    }));
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!settings) return;
    await onSubmit(formValues);
  };

  const handleFolderSelect = async (field: string) => {
    try {
      const result = await invoke<string>("select_folder_dialog", {
        defaultPath: null,
      });

      if (result) {
        setFormValues((prev) => ({ ...prev, [field]: result }));
      }
    } catch (error) {
      console.error(`Failed to select folder for ${field}:`, error);
      if (error !== "Selection cancelled") {
        alert(`Failed to select folder: ${error}`);
      }
    }
  };

  const handleFileSelect = async (field: string) => {
    try {
      const result = await invoke<string>("select_file_dialog", {
        defaultPath: null,
        filterExtensions:
          field === "seven_zip_bin" ? ["exe", "bat", "cmd", "msi"] : ["*"],
      });

      if (result) {
        setFormValues((prev) => ({ ...prev, [field]: result }));
      }
    } catch (error) {
      console.error(`Failed to select file for ${field}:`, error);
      if (error !== "Selection cancelled") {
        alert(`Failed to select file: ${error}`);
      }
    }
  };

  const disableSave = saving || loading || !settings || !hasChanges;

  const renderValidationStatus = (key: keyof ApiSettings["validation"]) => {
    if (!settings?.validation) return null;
    const info = settings.validation[key];
    if (!info) return null;
    const trimmedMessage = info.message?.trim() ?? "";
    const tone = !info.ok
      ? "text-destructive"
      : info.reason === "not_configured"
        ? "text-muted-foreground"
        : "text-emerald-500";
    return <div className={`mt-1 text-xs ${tone}`}>{trimmedMessage || ""}</div>;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-5xl h-[90vh] max-h-[90vh] flex flex-col overflow-hidden settings-dialog-scroll"
        onOpenAutoFocus={(e) => e.preventDefault()}
        style={{
          width: "80%",
          overflowY: "auto",
          // Traditional CSS to hide scrollbars while keeping functionality
          msOverflowStyle: "none", // IE and Edge
          scrollbarWidth: "none", // Firefox
        }}
      >
        <style>{`
          .settings-dialog-scroll::-webkit-scrollbar {
            display: none;
          }
        `}</style>
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b px-6 py-5">
          <DialogHeader className="space-y-2 mb-4">
            <DialogTitle className="text-2xl font-bold">
              Application Settings
            </DialogTitle>
          </DialogHeader>

          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            disabled={loading}
            className="shrink-0"
            title="Reload settings"
          >
            {loading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Refreshing
              </>
            ) : (
              <>
                <RefreshCw className="mr-2 h-4 w-4" />
                Reload
              </>
            )}
          </Button>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit} className="flex flex-col">
          <div className="px-6 py-5">
            {settings ? (
              <div
                className="settings-grid"
                style={{
                  display: "grid",
                  gridTemplateColumns: "3fr 2fr",
                  gap: "40px",
                  alignItems: "start",
                  marginBottom: "8px",
                }}
              >
                {/* Left column (wider - 2/3) */}
                <div
                  className="settings-left"
                  style={{
                    minWidth: 0,
                    display: "flex",
                    flexDirection: "column",
                    gap: "32px",
                  }}
                >
                  {/* Directories & Tools */}
                  <section
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "24px",
                    }}
                  >
                    <div style={{ marginBottom: "8px" }}>
                      <h3 className="text-lg font-semibold">
                        Directories & Tools
                      </h3>
                      <p className="text-sm text-muted-foreground">
                        Provide absolute paths so helper scripts can locate the
                        game, packed downloads, and optional tooling.
                      </p>
                    </div>

                    <div
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        gap: "20px",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: "10px",
                        }}
                      >
                        <Label htmlFor="marvel_rivals_local_downloads_root">
                          Local downloads folder
                        </Label>
                        <div
                          style={{
                            display: "flex",
                            gap: "0.5rem",
                            alignItems: "center",
                          }}
                        >
                          <Input
                            id="marvel_rivals_local_downloads_root"
                            placeholder="D:\Mods\MarvelRivalsDownloads"
                            value={
                              formValues.marvel_rivals_local_downloads_root
                            }
                            onChange={handleInputChange(
                              "marvel_rivals_local_downloads_root",
                            )}
                            className="flex-1"
                          />
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              handleFolderSelect(
                                "marvel_rivals_local_downloads_root",
                              )
                            }
                            style={{ padding: "0.5rem", minWidth: "auto" }}
                            title="Select folder"
                          >
                            <Folder className="h-4 w-4" />
                          </Button>
                        </div>
                        {renderValidationStatus(
                          "marvel_rivals_local_downloads_root",
                        )}
                      </div>

                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: "10px",
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                          }}
                        >
                          <Label htmlFor="nexus_api_key">Nexus API key</Label>
                          <Button
                            type="button"
                            variant="link"
                            size="sm"
                            onClick={async () => {
                              const apiKeysUrl =
                                "https://next.nexusmods.com/settings/api-keys#:~:text=Rivalnxt";
                              try {
                                const { openInBrowser } =
                                  await import("../lib/tauri-utils");
                                await openInBrowser(apiKeysUrl);
                              } catch (error) {
                                console.error(
                                  "Failed to open API keys page:",
                                  error,
                                );
                              }
                            }}
                            style={{
                              padding: "0",
                              height: "auto",
                              fontSize: "0.875rem",
                            }}
                          >
                            <ExternalLink className="h-3 w-3 mr-1" />
                            Get API Key
                          </Button>
                        </div>
                        <Input
                          id="nexus_api_key"
                          type="password"
                          placeholder="••••••••••••••••"
                          value={formValues.nexus_api_key}
                          onChange={handleInputChange("nexus_api_key")}
                        />
                      </div>

                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: "10px",
                        }}
                      >
                        <Label htmlFor="marvel_rivals_root">
                          Marvel Rivals root
                        </Label>
                        <div
                          style={{
                            display: "flex",
                            gap: "0.5rem",
                            alignItems: "center",
                          }}
                        >
                          <Input
                            id="marvel_rivals_root"
                            placeholder="D:\Games\MarvelRivals"
                            value={formValues.marvel_rivals_root}
                            onChange={handleInputChange("marvel_rivals_root")}
                            className="flex-1"
                          />
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={async () => {
                              try {
                                const result = await invoke<{
                                  success: boolean;
                                  path?: string;
                                  message: string;
                                }>("detect_marvel_rivals_path");

                                if (result.success && result.path) {
                                  setFormValues((prev) => ({
                                    ...prev,
                                    marvel_rivals_root: result.path!,
                                  }));
                                  toast.success("Marvel Rivals detected", {
                                    description: `Found at: ${result.path}`,
                                    duration: 4000,
                                  });
                                } else {
                                  toast.error("Marvel Rivals not found", {
                                    description:
                                      result.message ||
                                      "Installation not detected. Please install via Steam or Epic Games Store.",
                                    duration: 4000,
                                  });
                                }
                              } catch (error) {
                                console.error(
                                  "Failed to detect Marvel Rivals:",
                                  error,
                                );
                                toast.error("Detection failed", {
                                  description: String(error),
                                  duration: 4000,
                                });
                              }
                            }}
                            style={{ padding: "0.5rem", minWidth: "auto" }}
                            title="Auto-detect Marvel Rivals installation"
                          >
                            <RefreshCw className="h-4 w-4" />
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              handleFolderSelect("marvel_rivals_root")
                            }
                            style={{ padding: "0.5rem", minWidth: "auto" }}
                            title="Select folder"
                          >
                            <Folder className="h-4 w-4" />
                          </Button>
                        </div>
                        {renderValidationStatus("marvel_rivals_root")}
                      </div>

                      <div
                        style={{
                          display: "grid",
                          gap: "16px",
                        }}
                      >
                        <div
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: "10px",
                          }}
                        >
                          <Label htmlFor="seven_zip_bin">
                            WinRAR executable
                          </Label>
                          <div
                            style={{
                              display: "flex",
                              gap: "0.5rem",
                              alignItems: "center",
                            }}
                          >
                            <Input
                              id="seven_zip_bin"
                              placeholder="C:\Program Files\WinRAR\WinRAR.exe"
                              value={formValues.seven_zip_bin}
                              onChange={handleInputChange("seven_zip_bin")}
                              className="flex-1"
                            />
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={async () => {
                                try {
                                  const result = await invoke<{
                                    success: boolean;
                                    name?: string;
                                    executable?: string;
                                    message: string;
                                    already_in_path?: boolean;
                                  }>("detect_archive_tool");

                                  if (result.success && result.executable) {
                                    setFormValues((prev) => ({
                                      ...prev,
                                      seven_zip_bin: result.executable!,
                                    }));

                                    // Show toast notification
                                    if (result.already_in_path) {
                                      toast.info(`${result.name} detected`, {
                                        description: `Already in PATH: ${result.executable}`,
                                        duration: 4000,
                                      });
                                    } else {
                                      toast.success(
                                        `${result.name} detected and added to PATH`,
                                        {
                                          description: `Executable: ${result.executable}`,
                                          duration: 4000,
                                        },
                                      );
                                    }
                                  } else {
                                    toast.error("Archive tool not found", {
                                      description:
                                        result.message ||
                                        "WinRAR installation not found",
                                      duration: 4000,
                                    });
                                  }
                                } catch (error) {
                                  console.error(
                                    "Failed to detect archive tool:",
                                    error,
                                  );
                                  toast.error("Detection failed", {
                                    description: String(error),
                                    duration: 4000,
                                  });
                                }
                              }}
                              style={{ padding: "0.5rem", minWidth: "auto" }}
                              title="Auto-detect WinRAR"
                            >
                              <RefreshCw className="h-4 w-4" />
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => handleFileSelect("seven_zip_bin")}
                              style={{ padding: "0.5rem", minWidth: "auto" }}
                              title="Select file"
                            >
                              <Folder className="h-4 w-4" />
                            </Button>
                          </div>
                          {renderValidationStatus("seven_zip_bin")}
                        </div>
                      </div>

                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: "10px",
                        }}
                      >
                        <Label htmlFor="aes_key_hex">AES key</Label>
                        <Input
                          id="aes_key_hex"
                          type="password"
                          placeholder="hex-encoded key"
                          value={formValues.aes_key_hex}
                          onChange={handleInputChange("aes_key_hex")}
                        />
                      </div>

                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: "10px",
                        }}
                      >
                        <Label htmlFor="data_dir">
                          Data directory (Locked)
                        </Label>
                        <div
                          style={{
                            display: "flex",
                            gap: "0.5rem",
                            alignItems: "center",
                          }}
                        >
                          <Input
                            id="data_dir"
                            placeholder="C:\Users\You\AppData\Local\RivalsModManager\data"
                            value={formValues.data_dir}
                            readOnly
                            className="flex-1 bg-muted/50"
                          />
                        </div>
                        {renderValidationStatus("data_dir")}
                      </div>
                    </div>
                  </section>

                  {/* NXM Protocol Registration */}
                  <section>
                    <NxmProtocolSettings />
                  </section>
                </div>

                {/* Right column (narrower - 1/3) */}
                <div
                  className="settings-right"
                  style={{
                    minWidth: 0,
                    display: "flex",
                    flexDirection: "column",
                    gap: "24px",
                  }}
                >
                  <div style={{ marginBottom: "8px" }}>
                    <h3 className="text-lg font-semibold">Maintenance Tasks</h3>
                    <p className="text-sm text-muted-foreground">
                      These scripts run on the backend and may take a moment;
                      outputs are captured for review.
                    </p>
                  </div>

                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "20px",
                    }}
                  >
                    {TASKS.map((task) => {
                      const result = taskJobs?.[task.key] ?? null;
                      const isRunning = taskBusy === task.key;
                      const rawStatus =
                        result?.status ?? (isRunning ? "running" : null);
                      const status =
                        rawStatus === "pending" && isRunning
                          ? "running"
                          : rawStatus;
                      const statusLabel =
                        status === "succeeded"
                          ? "Success"
                          : status === "failed"
                            ? "Failed"
                            : status === "running"
                              ? "Running"
                              : status === "pending"
                                ? "Pending"
                                : result?.ok
                                  ? "Success"
                                  : "Idle";
                      const statusTone =
                        status === "failed"
                          ? "font-medium text-red-600"
                          : status === "succeeded"
                            ? "font-medium text-green-600"
                            : status === "running"
                              ? "font-medium text-blue-600"
                              : "font-medium text-muted-foreground";
                      const startedAt = formatTimestamp(
                        result?.started_at ?? null,
                      );
                      const updatedAt = formatTimestamp(
                        result?.updated_at ?? null,
                      );
                      const finishedAt = formatTimestamp(
                        result?.finished_at ?? null,
                      );
                      const timestampSource =
                        finishedAt || updatedAt || startedAt;
                      const timestampPrefix = finishedAt
                        ? "Finished"
                        : updatedAt && status === "running"
                          ? "Updated"
                          : "Started";
                      let durationSeconds: string | null = null;
                      if (result?.status === "running" && result.started_at) {
                        const started = new Date(result.started_at).getTime();
                        if (!Number.isNaN(started)) {
                          durationSeconds = (
                            (Date.now() - started) /
                            1000
                          ).toFixed(2);
                        }
                      } else if (typeof result?.duration_ms === "number") {
                        durationSeconds = Math.max(
                          result.duration_ms / 1000,
                          0,
                        ).toFixed(2);
                      }
                      const outputText = result?.output ?? "";
                      const exitCodeText =
                        typeof result?.exit_code === "number"
                          ? ` (exit ${result.exit_code})`
                          : "";

                      return (
                        <div
                          key={task.key}
                          style={{
                            borderRadius: "10px",
                            border: "1px solid #333",
                            padding: "16px",
                            marginBottom: "4px",
                          }}
                        >
                          <div
                            style={{
                              display: "flex",
                              alignItems: "flex-start",
                              justifyContent: "space-between",
                              gap: "12px",
                            }}
                          >
                            <div style={{ minWidth: 0, flex: 1 }}>
                              <h4 className="text-sm font-medium leading-tight">
                                {task.label}
                              </h4>
                              <p className="text-xs text-muted-foreground leading-snug">
                                {task.description}
                              </p>
                            </div>
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              onClick={() => onRunTask(task.key)}
                              disabled={isRunning || !settings}
                              style={{ marginLeft: "8px", minWidth: "72px" }}
                            >
                              {isRunning ? (
                                <>
                                  <Loader2
                                    style={{
                                      marginRight: "6px",
                                      height: "18px",
                                      width: "18px",
                                    }}
                                    className="animate-spin"
                                  />
                                  Running
                                </>
                              ) : (
                                <>
                                  <Play
                                    style={{
                                      marginRight: "6px",
                                      height: "18px",
                                      width: "18px",
                                    }}
                                  />
                                  Run
                                </>
                              )}
                            </Button>
                          </div>

                          {result ? (
                            <div
                              style={{
                                marginTop: "16px",
                                display: "flex",
                                flexDirection: "column",
                                gap: "12px",
                              }}
                            >
                              <div
                                style={{
                                  display: "flex",
                                  flexWrap: "wrap",
                                  alignItems: "center",
                                  gap: "10px",
                                  fontSize: "13px",
                                }}
                              >
                                <span>
                                  Status:{" "}
                                  <span className={statusTone}>
                                    {status === "running" ? (
                                      <>
                                        <Loader2 className="mr-1 inline-block h-3 w-3 animate-spin" />
                                        {statusLabel}
                                      </>
                                    ) : (
                                      `${statusLabel}`
                                    )}
                                  </span>
                                  {exitCodeText}
                                </span>
                                {timestampSource ? (
                                  <span className="text-muted-foreground">
                                    {timestampPrefix} {timestampSource}
                                  </span>
                                ) : null}
                                {durationSeconds ? (
                                  <span className="text-muted-foreground">
                                    {result?.status === "running"
                                      ? `Elapsed ${durationSeconds}s`
                                      : `Duration ${durationSeconds}s`}
                                  </span>
                                ) : null}
                              </div>

                              {result.error ? (
                                <div
                                  style={{
                                    borderRadius: "6px",
                                    border: "1px solid #f99",
                                    background: "#fff0f0",
                                    padding: "10px",
                                    fontSize: "12px",
                                    color: "#b00",
                                  }}
                                >
                                  {result.error}
                                </div>
                              ) : null}

                              <div
                                style={{
                                  display: "flex",
                                  flexDirection: "column",
                                  gap: "8px",
                                }}
                              >
                                <Label className="text-xs">Output</Label>
                                <TaskOutputSummary
                                  task={task.key}
                                  output={outputText}
                                  isRunning={isRunning}
                                  fallbackMinHeight="h-24"
                                />
                              </div>
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex h-48 items-center justify-center">
                {loading ? (
                  <div className="inline-flex items-center text-sm text-muted-foreground">
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Loading settings…
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground">
                    Settings are unavailable. Try reloading.
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Footer - always visible at bottom */}
          <DialogFooter
            style={{
              position: "sticky",
              bottom: 0,
              left: 0,
              width: "100%",
              background: "rgba(24, 24, 27, 0.85)",
              borderTop: "1px solid #222",
              padding: "18px 32px",
              display: "flex",
              justifyContent: "flex-end",
              gap: "16px",
              zIndex: 10,
              borderRadius: "18px",
              backdropFilter: "blur(12px)",
              WebkitBackdropFilter: "blur(12px)",
              boxShadow: "0 -2px 16px 0 rgba(0,0,0,0.18)",
              transition: "background 0.2s",
            }}
          >
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              style={{ minWidth: "110px" }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={disableSave}
              style={{ minWidth: "140px" }}
            >
              {saving ? (
                <>
                  <Loader2
                    style={{
                      marginRight: "8px",
                      height: "20px",
                      width: "20px",
                    }}
                    className="animate-spin"
                  />
                  Saving
                </>
              ) : (
                "Save changes"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
