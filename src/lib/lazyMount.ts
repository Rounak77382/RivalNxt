import { useEffect, useState } from "react";

/**
 * Latching flag: false until `flag` is first true, true forever after.
 *
 * Used to defer mounting (and therefore the dynamic import) of heavy modals until
 * the user first opens them, while keeping them mounted afterwards so their
 * internal state, exit animations and prop-driven effects behave exactly as they
 * did when they were always mounted.
 *
 * Without the latch, gating purely on `open` would unmount on every close, which
 * would discard modal state and could drop prop-driven effects — the reason
 * GameUpdateModal is deliberately NOT deferred: it auto-dismisses from an effect
 * keyed on `phase`, which the parent can change while the modal is closed.
 */
export function useHasBeenTrue(flag: boolean): boolean {
  const [latched, setLatched] = useState(flag);

  useEffect(() => {
    if (flag && !latched) setLatched(true);
  }, [flag, latched]);

  // Return the live value too, so the very first `true` render already mounts
  // rather than waiting a frame for the effect.
  return latched || flag;
}
