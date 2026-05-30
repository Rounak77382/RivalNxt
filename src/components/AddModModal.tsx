import { useEffect, useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogClose,
} from "./ui/dialog";
import { Button } from "./ui/button";
import {
  ApiError,
  addMod,
  uploadModFile,
  copyToDownloads,
  ingestNxmHandoff,
  submitNxmHandoff,
  type ApiUploadModResponse,
} from "../lib/api";
import { toast } from "sonner";
import { openInBrowser } from "../lib/tauri-utils";
import {
  waitForMatchingHandoff,
  createNxmProgressController,
  formatBytes,
  getModLabel,
  isNexusOfficialNaming,
  parseNexusFilename,
} from "../lib/nxmHelpers";
import { getCurrentWebview } from "@tauri-apps/api/webview";

interface AddModModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => Promise<void> | void;
}

export function AddModModal({
  open,
  onOpenChange,
  onSuccess,
}: AddModModalProps) {
  const [modLink, setModLink] = useState("");
  const [localPath, setLocalPath] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadInfo, setUploadInfo] = useState<ApiUploadModResponse | null>(
    null,
  );
  const [isDragging, setIsDragging] = useState(false);
  // Guard to prevent Tauri native drop + React onDrop from both firing
  const isDragProcessingRef = useRef(false);

  const parseNexusModLink = (raw: string) => {
    try {
      // Extract mod ID using pattern /mods/XXXX
      const modIdMatch = raw.match(/\/mods\/(\d+)/);
      if (!modIdMatch) return null;

      const modIdValue = Number.parseInt(modIdMatch[1], 10);
      if (!Number.isFinite(modIdValue)) return null;

      // Extract file ID using pattern &file_id=XXXX or ?file_id=XXXX
      let fileId: number | null = null;
      const fileIdMatch = raw.match(/[?&]file_id=(\d+)/i);
      if (fileIdMatch) {
        const parsed = Number.parseInt(fileIdMatch[1], 10);
        if (Number.isFinite(parsed)) {
          fileId = parsed;
        }
      }

      // Try to extract game from full URL if available, default to "marvelrivals"
      let game = "marvelrivals";
      try {
        const url = new URL(raw);
        if (/nexusmods\.com$/i.test(url.hostname)) {
          const segments = url.pathname
            .split("/")
            .map((segment) => segment.trim())
            .filter(Boolean);
          const modsIndex = segments.findIndex(
            (segment) => segment.toLowerCase() === "mods",
          );
          if (modsIndex > 0) {
            game = segments[modsIndex - 1];
          }
        }
      } catch {
        // Not a valid URL, use default game
      }

      // Construct the proper Nexus URL
      const constructedUrl = new URL(
        `https://www.nexusmods.com/${game}/mods/${modIdValue}`,
      );
      if (fileId != null) {
        constructedUrl.searchParams.set("tab", "files");
        constructedUrl.searchParams.set("file_id", String(fileId));
      }

      return { url: constructedUrl, game, modId: modIdValue, fileId };
    } catch (err) {
      return null;
    }
  };

  const resetInputs = () => {
    setModLink("");
    setLocalPath("");
    setUploadInfo(null);
  };

  const handleNxmError = (
    err: unknown,
    context?: string,
    toastId?: string | number,
  ): string => {
    let message: string;
    if (err instanceof ApiError) {
      const detail = err.detail;
      if (detail && typeof detail === "object" && detail !== null) {
        const detailMessage = (detail as Record<string, unknown>)["message"];
        if (typeof detailMessage === "string" && detailMessage.trim()) {
          message = detailMessage.trim();
        } else if (typeof err.message === "string" && err.message.trim()) {
          message = err.message.trim();
        } else {
          message = "Nexus handoff failed";
        }
      } else if (err.message) {
        message = err.message;
      } else {
        message = "Nexus handoff failed";
      }
    } else if (err instanceof Error && err.message) {
      message = err.message;
    } else {
      message = String(err ?? "Unknown error");
    }
    const fullMessage = context ? `${context}: ${message}` : message;
    if (toastId != null) {
      toast.error(fullMessage, { id: toastId });
    } else {
      toast.error(fullMessage);
    }
    return fullMessage;
  };

  const finalizeSuccess = async () => {
    resetInputs();
    onOpenChange(false);
    if (onSuccess) {
      await onSuccess();
    }
  };

  const handleDirectNxm = async (nxmUri: string) => {
    setBusy(true);
    try {
      const response = await submitNxmHandoff(nxmUri);
      const handoff = response?.handoff;
      if (!handoff || !handoff.id) {
        toast.error("Backend did not accept the RivalNxt link.");
        return;
      }
      const modLabel = getModLabel(handoff);
      const controller = createNxmProgressController(handoff.id, {
        label: `Downloading ${modLabel}`,
      });
      try {
        const ingest = await ingestNxmHandoff(handoff.id, {
          activate: false,
          deactivateExisting: false,
        });
        controller.stop();
        const modName =
          typeof ingest.mod_name === "string" &&
          ingest.mod_name.trim().length > 0
            ? ingest.mod_name
            : `Mod #${ingest.mod_id}`;
        const fileName =
          ingest.selected_file &&
          typeof ingest.selected_file["name"] === "string"
            ? (ingest.selected_file["name"] as string)
            : undefined;
        toast.success(`Added ${modName}`, {
          id: controller.toastId,
          description: fileName ?? controller.getLastDescription(),
        });
        if (ingest.activation_warning) {
          toast.warning(ingest.activation_warning);
        }
        await finalizeSuccess();
      } catch (err) {
        controller.stop();
        handleNxmError(
          err,
          "Failed to process RivalNxt link",
          controller.toastId,
        );
      }
    } catch (err) {
      handleNxmError(err, "Failed to process RivalNxt link");
    } finally {
      setBusy(false);
    }
  };

  const handleNexusLink = async (
    details: ReturnType<typeof parseNexusModLink>,
  ) => {
    if (!details) return;
    setBusy(true);
    const { url, game, modId, fileId } = details;

    try {
      const nexusUrl = new URL(url.toString());

      // If no file ID, open the files tab and let user choose which file(s) to download
      if (fileId == null) {
        nexusUrl.searchParams.set("tab", "files");

        toast.info("Opening Nexus Mods files tab", {
          description: `Select file(s) to download from Mod #${modId}`,
        });

        let openedNewTab = false;
        try {
          await openInBrowser(nexusUrl.toString());
          openedNewTab = true;
          console.log("Successfully opened Nexus URL");
        } catch (err) {
          console.error("Failed to open Nexus download page:", err);
          const errorMessage = err instanceof Error ? err.message : String(err);
          toast.error("Could not open browser", {
            description: errorMessage,
          });
        }

        if (!openedNewTab) {
          setBusy(false);
          return;
        }

        // Wait for any handoff from this mod (not specific to a file ID)
        const handoff = await waitForMatchingHandoff(modId, null);
        if (!handoff) {
          toast.error(
            "Did not receive a RivalNxt handoff. Click 'Mod Manager Download' on a file in the Nexus tab, then try again.",
          );
          setBusy(false);
          return;
        }

        const modLabel = await getModLabel(handoff);
        const controller = createNxmProgressController(handoff.id, {
          label: `Downloading ${modLabel}`,
        });

        try {
          const ingest = await ingestNxmHandoff(handoff.id, {
            activate: false,
            deactivateExisting: false,
          });
          controller.stop();
          const modName =
            typeof ingest.mod_name === "string" && ingest.mod_name?.trim()
              ? ingest.mod_name
              : `Mod #${ingest.mod_id}`;
          const fileName =
            ingest.selected_file &&
            typeof ingest.selected_file["name"] === "string"
              ? (ingest.selected_file["name"] as string)
              : undefined;
          toast.success(`Added ${modName}`, {
            id: controller.toastId,
            description: fileName ?? controller.getLastDescription(),
          });
          if (ingest.activation_warning) {
            toast.warning(ingest.activation_warning);
          }
          await finalizeSuccess();
        } catch (err) {
          controller.stop();
          handleNxmError(
            err,
            "Failed to ingest Nexus download",
            controller.toastId,
          );
        } finally {
          setBusy(false);
        }
        return;
      }

      // Original flow with specific file ID
      if (!nexusUrl.searchParams.get("tab")) {
        nexusUrl.searchParams.set("tab", "files");
      }
      nexusUrl.searchParams.set("file_id", String(fileId));
      nexusUrl.searchParams.set("nmm", "1");

      const nxmPreview = `nxm://${game}/mods/${modId}/files/${fileId}`;

      toast.info("Opening Nexus Mods to start the RivalNxt handoff", {
        description: `${nxmPreview}`,
      });

      let openedNewTab = false;
      try {
        await openInBrowser(nexusUrl.toString());
        openedNewTab = true;
        console.log("Successfully opened Nexus URL");
      } catch (err) {
        console.error("Failed to open Nexus download page:", err);
        const errorMessage = err instanceof Error ? err.message : String(err);
        toast.error("Could not open browser", {
          description: errorMessage,
        });
      }

      if (!openedNewTab) {
        setBusy(false);
        return;
      }

      const handoff = await waitForMatchingHandoff(modId, fileId);
      if (!handoff) {
        toast.error(
          "Did not receive a RivalNxt handoff. Approve the download in the Nexus tab, then try again.",
        );
        setBusy(false);
        return;
      }

      const modLabel = await getModLabel(handoff);
      const controller = createNxmProgressController(handoff.id, {
        label: `Downloading ${modLabel}`,
      });

      try {
        const ingest = await ingestNxmHandoff(handoff.id, {
          activate: false,
          deactivateExisting: false,
        });
        controller.stop();
        const modName =
          typeof ingest.mod_name === "string" && ingest.mod_name?.trim()
            ? ingest.mod_name
            : `Mod #${ingest.mod_id}`;
        const fileName =
          ingest.selected_file &&
          typeof ingest.selected_file["name"] === "string"
            ? (ingest.selected_file["name"] as string)
            : undefined;
        toast.success(`Added ${modName}`, {
          id: controller.toastId,
          description: fileName ?? controller.getLastDescription(),
        });
        if (ingest.activation_warning) {
          toast.warning(ingest.activation_warning);
        }
        await finalizeSuccess();
      } catch (err) {
        controller.stop();
        handleNxmError(
          err,
          "Failed to ingest Nexus download",
          controller.toastId,
        );
      }
    } catch (err) {
      handleNxmError(err, "Failed to ingest Nexus download");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!open) {
      setModLink("");
      setLocalPath("");
      setUploadInfo(null);
      setUploading(false);
      setBusy(false);
    }
  }, [open]);

  // Tauri file drop event listener
  useEffect(() => {
    if (!open) return;

    let unlisten: (() => void) | undefined;

    const setupListener = async () => {
      try {
        const webview = getCurrentWebview();
        unlisten = await webview.onDragDropEvent(async (event) => {
          if (event.payload.type === "enter") {
            setIsDragging(true);
          } else if (event.payload.type === "leave") {
            setIsDragging(false);
          } else if (event.payload.type === "drop") {
            setIsDragging(false);
            const paths = event.payload.paths;
            if (paths && paths.length > 0) {
              // Guard: prevent React onDrop from also processing this drop
              if (isDragProcessingRef.current) return;
              isDragProcessingRef.current = true;
              
              setUploading(true);
              let addedCount = 0;
              toast.loading(`Processing ${paths.length} file(s)...`, {
                id: "drag-drop-copy",
              });
              
              try {
                for (const filePath of paths) {
                  try {
                    const copyResult = await copyToDownloads(filePath);
                    const res = await addMod({ localPath: copyResult.path });
                    if (res.ok) {
                      addedCount++;
                      toast.success(
                        `Added: ${res.name} ${
                          res.ingested_paks
                            ? `(${res.ingested_paks} pak${
                                res.ingested_paks !== 1 ? "s" : ""
                              })`
                            : ""
                        }`
                      );
                      if (res.ingest_warning) toast.warning(res.ingest_warning);
                      if (res.metadata_warning) toast.warning(res.metadata_warning);
                    } else {
                      toast.error(`Failed to add mod from ${filePath}`);
                    }
                  } catch (err: any) {
                    if (err?.status === 409) {
                      toast.info(err.message || "Mod already exists", {
                        description: "This version is already in your library.",
                      });
                    } else {
                      console.error(`Failed to process dropped file ${filePath}:`, err);
                      toast.error(
                        err instanceof Error ? err.message : `Failed to add ${filePath}`
                      );
                    }
                  }
                }
                
                toast.dismiss("drag-drop-copy");
                if (addedCount > 0) {
                  setModLink("");
                  setLocalPath("");
                  setUploadInfo(null);
                  onOpenChange(false);
                  if (onSuccess) {
                    await onSuccess();
                  }
                }
              } finally {
                toast.dismiss("drag-drop-copy");
                setUploading(false);
                isDragProcessingRef.current = false;
              }
            }
          }
        });
      } catch (err) {
        console.error("Failed to setup file drop listener:", err);
      }
    };

    setupListener();

    return () => {
      if (unlisten) unlisten();
      setIsDragging(false);
    };
  }, [open, onOpenChange, onSuccess]);

  const handleBrowse = async (files?: FileList | File[] | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    let addedCount = 0;
    toast.loading(`Uploading ${files.length} file(s)...`, { id: "upload-batch" });
    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        try {
          const uploaded = await uploadModFile(file);
          const res = await addMod({ localPath: uploaded.path });
          if (res.ok) {
            addedCount++;
            toast.success(`Added: ${res.name}`);
            if (res.ingest_warning) toast.warning(res.ingest_warning);
          } else {
            toast.error(`Failed to add: ${file.name}`);
          }
        } catch (err: any) {
          if (err?.status === 409) {
            toast.info(err.message || "Mod already exists", {
              description: `${file.name} is already in your library.`,
            });
          } else {
            toast.error(err instanceof Error ? err.message : `Failed to upload ${file.name}`);
          }
        }
      }
      toast.dismiss("upload-batch");
      if (addedCount > 0) {
        setModLink("");
        setLocalPath("");
        setUploadInfo(null);
        onOpenChange(false);
        if (onSuccess) await onSuccess();
      }
    } finally {
      toast.dismiss("upload-batch");
      setUploading(false);
    }
  };

  const handleAdd = async () => {
    if (uploading) return;
    const trimmedLink = modLink.trim();
    const path = localPath || trimmedLink;
    if (!path) return;
    const isUrl = /^https?:\/\//i.test(trimmedLink);

    if (!localPath && trimmedLink) {
      if (/^nxm:\/\//i.test(trimmedLink)) {
        await handleDirectNxm(trimmedLink);
        return;
      }
      if (isUrl) {
        const nexusDetails = parseNexusModLink(trimmedLink);
        if (nexusDetails) {
          await handleNexusLink(nexusDetails);
          return;
        }
      }
    }

    setBusy(true);
    try {
      const res = await addMod({
        localPath: path,
        ...(isUrl ? { sourceUrl: trimmedLink } : {}),
      });
      if (res.ok) {
        toast.success(
          `Added: ${res.name} ${
            res.ingested_paks
              ? `(${res.ingested_paks} pak${
                  res.ingested_paks !== 1 ? "s" : ""
                })`
              : ""
          }`,
        );
        if (res.ingest_warning) {
          toast.warning(res.ingest_warning);
        }
        if (res.metadata_warning) {
          toast.warning(res.metadata_warning);
        } else if (res.synced_mod_id) {
          toast.success(`Synced Nexus metadata for mod #${res.synced_mod_id}`);
        }
        setModLink("");
        setLocalPath("");
        setUploadInfo(null);
        await finalizeSuccess();
      } else {
        toast.error("Add mod failed");
      }
    } catch (e: any) {
      if (e?.status === 409) {
        toast.info(e.message || "Mod already exists", {
          description: "This version is already in your library.",
        });
        await finalizeSuccess();
      } else {
        toast.error(e?.message || "Failed to add mod");
      }
    } finally {

      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-lg max-h-[90vh] overflow-y-auto bg-card border border-border rounded-lg shadow-xl"
        style={{ width: "50%" }}
      >
        <DialogHeader>
          <DialogTitle className="text-xl font-semibold">Add a Mod</DialogTitle>
        </DialogHeader>
        <div className="space-y-6">
          {/* Paste Link */}
          <div className="space-y-2">
            <label
              htmlFor="mod-link"
              className="block text-sm font-medium text-foreground"
            >
              Paste Mod Link or Local Path
            </label>
            <input
              id="mod-link"
              type="text"
              value={modLink}
              onChange={(e) => {
                const value = e.target.value;
                setModLink(value);
                if (uploadInfo) {
                  setUploadInfo(null);
                }
                setLocalPath("");
              }}
              placeholder="https://... or C:\\path\\to\\mod.zip"
              className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
            />
          </div>
          {/* Drag and Drop */}
          <div
            className={`relative border-2 border-dashed rounded-lg flex flex-col items-center justify-center py-12 px-6 text-muted-foreground cursor-pointer transition-all duration-200 ${
              isDragging
                ? "border-primary bg-primary/10"
                : "border-muted-foreground/25 hover:border-primary hover:bg-primary/5"
            }`}
            data-uploading={uploading ? "true" : "false"}
            onDragEnter={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsDragging(true);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onDragLeave={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsDragging(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsDragging(false);
              // Tauri's onDragDropEvent handles OS file drops with native paths.
              // This React handler only fires in browser-context (non-Tauri) or
              // when the Tauri handler is NOT already processing the drop.
              if (isDragProcessingRef.current) return;
              const files = e.dataTransfer.files;
              if (files && files.length > 0) {
                handleBrowse(files);
              }
            }}
          >
            <div className="text-2xl mb-3">📁</div>
            <span className="text-base font-medium mb-1">
              Drag & Drop Mod Files Here
            </span>
            <span className="text-xs">
              {uploading
                ? "Uploading..."
                : uploadInfo
                  ? `Saved as ${uploadInfo.relative_path}`
                  : "or click to browse"}
            </span>
            <input
              type="file"
              multiple
              className="absolute opacity-0 w-full h-full cursor-pointer"
              style={{ left: 0, top: 0 }}
              onChange={(e) => handleBrowse(e.target.files)}
            />
          </div>
          {localPath && (
            <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              <div className="flex items-center justify-between gap-2">
                <div className="font-medium text-foreground">
                  Selected source
                </div>
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto px-0 text-xs"
                  onClick={() => {
                    setLocalPath("");
                    setUploadInfo(null);
                  }}
                >
                  Clear
                </Button>
              </div>
              <div className="mt-1 break-all">{localPath}</div>
              {(() => {
                const fileName = localPath.split(/[/\\]/).pop() || "";
                if (isNexusOfficialNaming(fileName)) {
                  const meta = parseNexusFilename(fileName);
                  return (
                    <div className="mt-2 flex items-center gap-2 text-[10px] font-medium text-primary bg-primary/10 w-fit px-2 py-0.5 rounded border border-primary/20">
                      <span className="opacity-70">Nexus Official Naming:</span>
                      <span>
                        Mod #{meta?.modId} · v{meta?.version}
                      </span>
                    </div>
                  );
                }
                return null;
              })()}
              {uploadInfo && (
                <div className="mt-1">
                  Stored under downloads as {uploadInfo.relative_path} (
                  {formatBytes(uploadInfo.size)})
                </div>
              )}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-3 mt-6">
          <DialogClose asChild>
            <Button variant="ghost" className="px-4 py-2">
              Cancel
            </Button>
          </DialogClose>
          <Button
            variant="default"
            disabled={busy || uploading || (!modLink.trim() && !localPath)}
            onClick={handleAdd}
            className="px-4 py-2"
          >
            {busy ? "Adding..." : "Add Mod"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
