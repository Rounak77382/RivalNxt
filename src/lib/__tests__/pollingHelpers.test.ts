/**
 * L6: adaptive handoff polling.
 *
 * CollectionsPage previously ran `setInterval(fetchHandoffs, 1000)` with an
 * empty dependency array, so it polled the backend once per second for as long
 * as the page was mounted -- regardless of whether anything was downloading.
 *
 * NOTE: this suite has not been executed. Node.js/npm are not installed in the
 * environment where it was written, so `npm run test` could not run. It is
 * expected to pass under the `frontend` CI job.
 */
import { describe, expect, it } from "vitest";
import {
  ACTIVE_POLL_MS,
  IDLE_POLL_MS,
  anyHandoffInFlight,
  isHandoffInFlight,
  nextPollDelay,
} from "../pollingHelpers";

const h = (stage?: string | null) => ({ progress: stage === undefined ? null : { stage } });

describe("isHandoffInFlight", () => {
  it("treats active stages as in flight", () => {
    for (const stage of [
      "pending",
      "processing",
      "preparing",
      "resolving",
      "connecting",
      "authorized",
      "downloading",
      "retrying",
      "cancelling",
    ]) {
      expect(isHandoffInFlight(h(stage))).toBe(true);
    }
  });

  it("treats terminal stages as settled", () => {
    for (const stage of ["complete", "downloaded", "failed", "cancelled", "error"]) {
      expect(isHandoffInFlight(h(stage))).toBe(false);
    }
  });

  it("is case and whitespace insensitive", () => {
    expect(isHandoffInFlight(h("  DOWNLOADING "))).toBe(true);
    expect(isHandoffInFlight(h("Complete"))).toBe(false);
  });

  it("treats a handoff with no progress yet as in flight", () => {
    // Registered but not started: it is about to run, so poll fast.
    expect(isHandoffInFlight(h())).toBe(true);
    expect(isHandoffInFlight({ progress: { stage: "" } })).toBe(true);
  });

  it("returns false for null/undefined", () => {
    expect(isHandoffInFlight(null)).toBe(false);
    expect(isHandoffInFlight(undefined)).toBe(false);
  });

  it("ignores unknown stages", () => {
    expect(isHandoffInFlight(h("some_future_stage"))).toBe(false);
  });
});

describe("anyHandoffInFlight", () => {
  it("is false for an empty queue", () => {
    expect(anyHandoffInFlight([])).toBe(false);
    expect(anyHandoffInFlight(null)).toBe(false);
    expect(anyHandoffInFlight(undefined)).toBe(false);
  });

  it("is false when every handoff is settled", () => {
    expect(anyHandoffInFlight([h("complete"), h("failed"), h("cancelled")])).toBe(false);
  });

  it("is true when at least one is in flight", () => {
    expect(anyHandoffInFlight([h("complete"), h("downloading")])).toBe(true);
  });
});

describe("nextPollDelay", () => {
  it("backs off to 10s when the queue is empty", () => {
    expect(nextPollDelay([])).toBe(10000);
    expect(nextPollDelay([])).toBe(IDLE_POLL_MS);
  });

  it("backs off to 10s when all downloads have settled", () => {
    // The exact regression: this used to stay at 1000ms forever.
    expect(nextPollDelay([h("complete"), h("failed")])).toBe(10000);
  });

  it("polls fast while a download is in flight", () => {
    expect(nextPollDelay([h("downloading")])).toBe(1000);
    expect(nextPollDelay([h("downloading")])).toBe(ACTIVE_POLL_MS);
  });

  it("honours custom cadences", () => {
    expect(nextPollDelay([], { activeMs: 5000, idleMs: 30000 })).toBe(30000);
    expect(nextPollDelay([h("downloading")], { activeMs: 5000, idleMs: 30000 })).toBe(5000);
  });

  it("never returns the fast cadence for a settled queue", () => {
    const settled = [h("complete"), h("downloaded"), h("failed"), h("cancelled")];
    expect(nextPollDelay(settled)).toBeGreaterThanOrEqual(IDLE_POLL_MS);
  });
});
