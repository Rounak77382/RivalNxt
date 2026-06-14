import { useEffect, useRef } from "react";
import { toast } from "sonner";
import {
  listNxmHandoffs,
  ingestNxmHandoff,
  cancelNxmHandoff,
  dismissNxmHandoff,
  deleteLocalDownloads,
  type ApiNxmHandoffSummary,
} from "../lib/api";
import { createNxmProgressController, getModLabel } from "../lib/nxmHelpers";
import { createToastDeduplicator, calculateBackoff } from "../lib/toastHelpers";

interface NxmBackgroundListenerProps {
  enabled: boolean;
  onModAdded?: () => Promise<void> | void;
  isHandoffExcluded?: (handoff: ApiNxmHandoffSummary) => boolean;
}

// Circuit breaker configuration
const MAX_RETRIES = 3;
const TOAST_DEDUPE_WINDOW_MS = 5000; // Don't show same error within 5 seconds
const BASE_POLLING_INTERVAL_MS = 2000;
const IDLE_POLLING_INTERVAL_MS = 5000; // Slower polling when no work

interface HandoffFailure {
  count: number;
  lastAttempt: number;
  lastError: string;
  permanentlyFailed: boolean;
}

/**
 * Background listener that continuously monitors for NXM handoffs
 * and automatically processes them without user interaction.
 *
 * Features anti-loop protection:
 * - Exponential backoff on failures (5s, 10s, 20s, 40s, 60s)
 * - Maximum 3 retry attempts before permanent failure
 * - Toast deduplication to prevent spam
 * - Cancellation tracking
 */
export function NxmBackgroundListener({
  enabled,
  onModAdded,
  isHandoffExcluded,
}: NxmBackgroundListenerProps) {
  const processedHandoffsRef = useRef<Set<string>>(new Set());
  const cancelledHandoffsRef = useRef<Set<string>>(new Set());
  const processingRef = useRef<Set<string>>(new Set());
  const failedHandoffsRef = useRef<Map<string, HandoffFailure>>(new Map());
  const toastDeduplicator = useRef(
    createToastDeduplicator(TOAST_DEDUPE_WINDOW_MS),
  );

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }

    let cancelled = false;
    let timeoutId: number | null = null;

    const checkAndProcessHandoffs = async () => {
      if (cancelled) return;

      try {
        const handoffs = await listNxmHandoffs();
        let hasWork = false;

        for (const handoff of handoffs) {
          // Skip if the handoff has expired (TTL exceeded) to avoid false-positive auto-process errors on startup
          const nowSeconds = Date.now() / 1000;
          if (handoff.expires_at && nowSeconds > handoff.expires_at) {
            continue;
          }

          // Skip if the handoff has already failed in a previous session (persistent/synthetic failed record)
          if (handoff.progress?.stage === "failed") {
            continue;
          }

          // Skip if already successfully processed or cancelled
          if (
            processedHandoffsRef.current.has(handoff.id) ||
            cancelledHandoffsRef.current.has(handoff.id)
          ) {
            continue;
          }

          // Skip if currently processing
          if (processingRef.current.has(handoff.id)) {
            continue;
          }

          // Skip if this handoff is being managed by the update flow
          if (isHandoffExcluded?.(handoff)) {
            continue;
          }

          // Check if this handoff has permanently failed
          const failure = failedHandoffsRef.current.get(handoff.id);
          if (failure?.permanentlyFailed) {
            console.info(
              `[NxmBackgroundListener] Skipping permanently failed handoff ${handoff.id} (${failure.count} failed attempts)`,
            );
            continue;
          }

          // Check if we should skip due to backoff
          if (failure && failure.count > 0) {
            const backoffMs = calculateBackoff(failure.count - 1);
            const timeSinceLastAttempt = Date.now() - failure.lastAttempt;

            if (timeSinceLastAttempt < backoffMs) {
              console.debug(
                `[NxmBackgroundListener] Handoff ${
                  handoff.id
                } in backoff period (${Math.round(
                  (backoffMs - timeSinceLastAttempt) / 1000,
                )}s remaining)`,
              );
              hasWork = true; // Still has pending work
              continue;
            }
          }

          // Mark as processing to prevent duplicate processing
          processingRef.current.add(handoff.id);
          hasWork = true;

          // Process this handoff in the background
          void processHandoff(handoff);
        }

        // Use slower polling if no work to do
        const nextInterval = hasWork
          ? BASE_POLLING_INTERVAL_MS
          : IDLE_POLLING_INTERVAL_MS;

        if (!cancelled) {
          timeoutId = window.setTimeout(checkAndProcessHandoffs, nextInterval);
        }
      } catch (err) {
        console.warn("[NxmBackgroundListener] Failed to list handoffs:", err);

        // Continue polling even if listing fails
        if (!cancelled) {
          timeoutId = window.setTimeout(
            checkAndProcessHandoffs,
            BASE_POLLING_INTERVAL_MS,
          );
        }
      }
    };

    const processHandoff = async (handoff: ApiNxmHandoffSummary) => {
      const modLabel = await getModLabel(handoff);

      const controller = createNxmProgressController(handoff.id, {
        label: `Auto-downloading ${modLabel}`,
        initialMessage: "Processing Nexus handoff...",
        onCancel: async () => {
          // Mark as cancelled immediately — the catch block checks this
          // so it can suppress error toasts and retries.
          cancelledHandoffsRef.current.add(handoff.id);
          processingRef.current.delete(handoff.id);
          failedHandoffsRef.current.delete(handoff.id);
          // Show a "stopping..." toast. The backend will abort the download
          // at the next 1 MiB chunk boundary and delete the partial file.
          toast.loading(`Stopping download…`, {
            id: controller.toastId,
            duration: Infinity,
          });
          // Hit the real cancel endpoint — sets the per-handoff flag in
          // the backend download loop and removes the handoff record.
          try {
            await cancelNxmHandoff(handoff.id);
          } catch {
            // If cancel fails (e.g. already finished), that's fine —
            // the catch block below will handle the resulting 404/499.
          }
        },
      });

      try {
        const ingest = await ingestNxmHandoff(handoff.id, {
          activate: false,
          deactivateExisting: false,
        });

        controller.stop();

        // ── Post-cancel cleanup ──────────────────────────────────────────────
        // The Rust backend cannot abort an in-flight download. If the user
        // cancelled while ingest was running, it completed anyway and created a
        // download record. Delete it now so it doesn't appear in the mod list.
        if (cancelledHandoffsRef.current.has(handoff.id)) {
          const downloadId: number | undefined =
            typeof ingest.download_id === "number" ? ingest.download_id
            : typeof (ingest as any).local_download_id === "number" ? (ingest as any).local_download_id
            : undefined;

          if (downloadId != null) {
            toast.loading(`Removing cancelled download...`, {
              id: controller.toastId,
              duration: Infinity,
            });
            try {
              await deleteLocalDownloads([downloadId]);
              toast.info(`Cancelled: ${modLabel} removed`, {
                id: controller.toastId,
                duration: 3000,
              });
            } catch (delErr) {
              console.warn("[NxmBackgroundListener] Failed to delete cancelled download:", delErr);
              toast.info(`Cancelled: ${modLabel} (you may need to remove it manually)`, {
                id: controller.toastId,
                duration: 5000,
              });
            }
          } else {
            // No download id to clean up — just confirm cancellation
            toast.info(`Cancelled: ${modLabel}`, {
              id: controller.toastId,
              duration: 3000,
            });
          }

          // Mark processed so polling doesn't pick it up again
          processedHandoffsRef.current.add(handoff.id);
          processingRef.current.delete(handoff.id);
          return;
        }

        const modName =
          typeof ingest.mod_name === "string" && ingest.mod_name.trim()
            ? ingest.mod_name
            : `Mod #${ingest.mod_id}`;

        const fileName =
          ingest.selected_file &&
          typeof ingest.selected_file["name"] === "string"
            ? (ingest.selected_file["name"] as string)
            : undefined;

        toast.success(`Auto-added ${modName}`, {
          id: controller.toastId,
          description: fileName ?? controller.getLastDescription(),
          duration: 4000,
        });

        if (ingest.activation_warning) {
          toast.warning(ingest.activation_warning);
        }

        // Mark as successfully processed and clear any failure record
        processedHandoffsRef.current.add(handoff.id);
        processingRef.current.delete(handoff.id);
        failedHandoffsRef.current.delete(handoff.id);

        // Notify parent component
        if (onModAdded) {
          await onModAdded();
        }
      } catch (err) {
        controller.stop();

        const errorMessage =
          err instanceof Error ? err.message : String(err ?? "Unknown error");

        // ── Cancellation short-circuit ──────────────────────────────────────
        // If the user clicked Cancel, the backend returns 499 (cancelled)
        // or 404 (handoff was already removed by cancel endpoint).
        // Either way: no error toast, no retry.
        const wasCancelled = cancelledHandoffsRef.current.has(handoff.id);

        const isCancelledByBackend =
          wasCancelled ||
          // 499 = our custom "cancelled by user" status
          (typeof err === "object" &&
            err !== null &&
            "status" in err &&
            (err as any).status === 499) ||
          (err instanceof Error &&
            errorMessage.toLowerCase().includes("cancelled by user"));

        if (isCancelledByBackend) {
          console.info(
            `[NxmBackgroundListener] Handoff ${handoff.id} cancelled — suppressing error.`,
          );
          toast.info(`Download cancelled: ${modLabel}`, {
            id: controller.toastId,
            duration: 3000,
          });
          processedHandoffsRef.current.add(handoff.id);
          processingRef.current.delete(handoff.id);
          return;
        }

        // Treat 404 (Not Found / Expired) from normal (non-cancelled) operation
        // as a clean skip — the handoff expired before we could ingest it.
        const isNotFound =
          (err instanceof Error &&
            (errorMessage.includes("404") ||
              errorMessage.toLowerCase().includes("not found") ||
              errorMessage.toLowerCase().includes("expired"))) ||
          (typeof err === "object" &&
            err !== null &&
            "status" in err &&
            (err as any).status === 404);

        if (isNotFound) {
          console.info(
            `[NxmBackgroundListener] Handoff ${handoff.id} not found/expired — skipping silently.`,
          );
          processingRef.current.delete(handoff.id);
          return;
        }

        // Special handling for duplicate downloads (HTTP 409)
        // Treat duplicates as a success case, not an error
        const isDuplicate =
          (err instanceof Error &&
            (errorMessage.toLowerCase().includes("already exists") ||
              errorMessage.toLowerCase().includes("duplicate"))) ||
          (typeof err === "object" &&
            err !== null &&
            "status" in err &&
            (err as any).status === 409);

        if (isDuplicate) {
          // Extract mod name and version from error if available
          let duplicateMessage =
            "This mod version is already in your downloads";
          if (
            typeof err === "object" &&
            err !== null &&
            "body" in err &&
            typeof (err as any).body === "object"
          ) {
            const detail = (err as any).body?.detail;
            if (typeof detail === "object" && detail?.message) {
              duplicateMessage = detail.message;
            } else if (typeof detail === "string") {
              duplicateMessage = detail;
            }
          } else if (errorMessage) {
            duplicateMessage = errorMessage;
          }

          toast.info(`${modLabel} already downloaded`, {
            id: controller.toastId,
            description: duplicateMessage,
            duration: 4000,
          });

          // Mark as successfully processed to prevent retries
          processedHandoffsRef.current.add(handoff.id);
          processingRef.current.delete(handoff.id);
          failedHandoffsRef.current.delete(handoff.id);

          // Dismiss (DELETE) from backend queue so polling stops picking it up.
          // Do NOT use cancelNxmHandoff here — that endpoint adds the ID to the
          // cancellation set and deletes the record prematurely, which can break
          // a concurrent ingest and leaves a stale entry in _CANCELLED_HANDOFFS.
          try {
            await dismissNxmHandoff(handoff.id);
          } catch (dismissErr) {
            console.warn(
              "[NxmBackgroundListener] Failed to dismiss duplicate handoff",
              dismissErr,
            );
          }

          // Notify parent component
          if (onModAdded) {
            await onModAdded();
          }
          return;
        }

        // Track this failure
        const currentFailure = failedHandoffsRef.current.get(handoff.id);
        const failureCount = (currentFailure?.count ?? 0) + 1;
        const permanentlyFailed = failureCount >= MAX_RETRIES;

        failedHandoffsRef.current.set(handoff.id, {
          count: failureCount,
          lastAttempt: Date.now(),
          lastError: errorMessage,
          permanentlyFailed,
        });

        // Only show toast if it's not a duplicate within the deduplication window
        const toastKey = `nxm-error:${handoff.id}:${errorMessage}`;
        if (toastDeduplicator.current.shouldShow(toastKey)) {
          let description = permanentlyFailed
            ? `${errorMessage} (Max retries reached, giving up)`
            : `${errorMessage} (Attempt ${failureCount}/${MAX_RETRIES})`;

          // Add advice for common causes of persistent failure
          if (
            permanentlyFailed ||
            errorMessage.toLowerCase().includes("api key") ||
            errorMessage.includes("401") ||
            errorMessage.includes("403")
          ) {
            description += ". Please check your Nexus API Key in Settings.";
          }

          toast.error(`Failed to auto-process ${modLabel}`, {
            id: controller.toastId,
            description,
            duration: permanentlyFailed ? 8000 : 5000,
          });
        }

        console.error(
          `[NxmBackgroundListener] Failed to process handoff ${handoff.id} (attempt ${failureCount}/${MAX_RETRIES}):`,
          err,
        );

        if (permanentlyFailed) {
          console.error(
            `[NxmBackgroundListener] Handoff ${handoff.id} permanently failed after ${MAX_RETRIES} attempts. Last error: ${errorMessage}`,
          );
        }

        // Remove from processing set
        processingRef.current.delete(handoff.id);
      }
    };

    // Start checking
    void checkAndProcessHandoffs();

    return () => {
      cancelled = true;
      if (timeoutId != null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [enabled, onModAdded, isHandoffExcluded]);

  // This component doesn't render anything
  return null;
}
