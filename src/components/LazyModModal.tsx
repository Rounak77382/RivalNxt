import { Suspense, lazy } from "react";
import type { ComponentProps } from "react";
import type { ModModal as ModModalType } from "./ModModal";

/**
 * Code-split entry point for ModModal.
 *
 * ModModal is the largest module in the app (112 KB of source) and was imported
 * statically by five components — ActiveModsView, BrowsePage, CollectionsPage,
 * DownloadsPage and ModConflictModal — which put it in the initial bundle
 * unconditionally even though it is only reachable after a user opens a mod.
 *
 * `lazy()` only begins fetching when the component is actually rendered, so every
 * call site must stay behind its `{selectedMod && …}` guard for the split to pay
 * off. Import this wrapper instead of ModModal directly.
 */
const ModModalImpl = lazy(() =>
  import("./ModModal").then((m) => ({ default: m.ModModal })),
);

export type LazyModModalProps = ComponentProps<typeof ModModalType>;

export function LazyModModal(props: LazyModModalProps) {
  return (
    // fallback={null} deliberately: the modal is an overlay, so a spinner behind
    // it would flash against the page underneath. The chunk is small enough that
    // the gap is imperceptible locally, and a skeleton would be more jarring.
    <Suspense fallback={null}>
      <ModModalImpl {...props} />
    </Suspense>
  );
}
