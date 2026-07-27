/**
 * Adaptive polling helpers for the NXM handoff queue.
 *
 * The handoff list used to be polled on a fixed 1000ms interval with an empty
 * dependency array, so it ran for as long as the page was mounted regardless of
 * whether any download was in flight. Every poll opened a backend DB connection
 * (and, before this change, issued DELETEs from the GET path), so an idle app
 * generated continuous database traffic.
 *
 * These helpers keep a fast cadence only while work is actually happening and
 * back off hard when the queue is quiet.
 */

/** Poll cadence while at least one handoff is progressing. */
export const ACTIVE_POLL_MS = 1000;

/** Poll cadence when the queue is empty or fully settled. */
export const IDLE_POLL_MS = 10000;

/**
 * Progress stages that mean "this handoff is still doing something".
 * Terminal stages (complete, downloaded, failed, cancelled) are excluded, as is
 * a handoff with no progress object at all -- registered but not yet started.
 */
const IN_FLIGHT_STAGES = new Set([
  "pending",
  "processing",
  "preparing",
  "resolving",
  "connecting",
  "authorized",
  "downloading",
  "retrying",
  "cancelling",
  "collect",
  "running",
]);

const TERMINAL_STAGES = new Set([
  "complete",
  "downloaded",
  "failed",
  "cancelled",
  "error",
]);

export type PollableHandoff = {
  progress?: { stage?: string | null } | null;
};

/** True when this handoff is mid-flight and worth polling fast for. */
export function isHandoffInFlight(handoff: PollableHandoff | null | undefined): boolean {
  if (!handoff) return false;
  const stage = handoff.progress?.stage;
  if (typeof stage !== "string" || stage.length === 0) {
    // No progress reported yet. A registered handoff is about to start, so treat
    // it as in flight rather than letting it sit behind a 10s poll.
    return true;
  }
  const normalized = stage.trim().toLowerCase();
  if (TERMINAL_STAGES.has(normalized)) return false;
  return IN_FLIGHT_STAGES.has(normalized);
}

/** True when any handoff in the list is mid-flight. */
export function anyHandoffInFlight(
  handoffs: readonly (PollableHandoff | null | undefined)[] | null | undefined
): boolean {
  if (!handoffs || handoffs.length === 0) return false;
  return handoffs.some(isHandoffInFlight);
}

/**
 * Choose the next poll delay. Fast while work is in flight, slow when idle.
 */
export function nextPollDelay(
  handoffs: readonly (PollableHandoff | null | undefined)[] | null | undefined,
  options: { activeMs?: number; idleMs?: number } = {}
): number {
  const activeMs = options.activeMs ?? ACTIVE_POLL_MS;
  const idleMs = options.idleMs ?? IDLE_POLL_MS;
  return anyHandoffInFlight(handoffs) ? activeMs : idleMs;
}
