import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode, RefObject } from "react";

/**
 * Row-based virtualizer for the mod lists.
 *
 * Nothing in src/ was virtualized: ActiveModsView (two lists), DownloadsPage,
 * BrowsePage and CollectionsPage all rendered every row, so a library of several
 * hundred mods mounted several hundred InstalledModCard/ModCard trees on every
 * filter or sort change.
 *
 * Design constraints this has to respect, all of which came out of the existing
 * markup rather than being invented:
 *
 * 1. SHARED SCROLL PARENT. The two ActiveModsView lists live inside one
 *    `flex-1 overflow-auto` container along with their headings, so the
 *    virtualizer cannot own the scroller. It takes a ref to the existing one.
 * 2. RESPONSIVE GRID. Grid mode is 1-5 columns via CSS media queries. Items are
 *    chunked into rows of `columns` and ROWS are virtualized, which keeps the
 *    real CSS grid intact inside each row.
 * 3. HOVER SCALE MUST NOT CLIP. `.card-container:hover` scales to 1.05 with
 *    `z-index: 10`. Absolutely-positioned virtual rows would clip that at the
 *    viewport edge and let a later row paint over it, so rows carry
 *    `overflow: visible` and a generous default overscan, and the row wrapper
 *    creates no stacking context of its own.
 * 4. SMALL LISTS ARE UNTOUCHED. Below `threshold` items everything renders
 *    exactly as before — no absolute positioning, no measurement — so the common
 *    case carries zero behavioural risk.
 */

export type VirtualizedModListProps<T> = {
  items: T[];
  /** The existing scroll container. */
  scrollRef: RefObject<HTMLElement | null>;
  renderItem: (item: T, index: number) => ReactNode;
  getKey: (item: T, index: number) => string;
  /** Columns per row; 1 for list mode. */
  columns: number;
  /** Approximate row height in px, used before real measurement. */
  estimateRowHeight: number;
  /** Class applied to each row (the CSS grid / flex row). */
  rowClassName?: string;
  /**
   * Render everything below this many items. Defaults to 60: enough that typical
   * libraries behave exactly as before, low enough that large ones get the win.
   */
  threshold?: number;
  /** Extra rows rendered above and below the viewport. */
  overscan?: number;
};

export function VirtualizedModList<T>({
  items,
  scrollRef,
  renderItem,
  getKey,
  columns,
  estimateRowHeight,
  rowClassName,
  threshold = 60,
  overscan = 4,
}: VirtualizedModListProps<T>) {
  const safeColumns = Math.max(1, Math.floor(columns) || 1);

  const rows = useMemo(() => {
    const out: T[][] = [];
    for (let i = 0; i < items.length; i += safeColumns) {
      out.push(items.slice(i, i + safeColumns));
    }
    return out;
  }, [items, safeColumns]);

  // Below the threshold, behave exactly like the original markup.
  const shouldVirtualize = items.length >= threshold;

  // The scroll container belongs to the PARENT, so on our first render
  // scrollRef.current is still null and the virtualizer has no viewport to
  // measure. Holding it in state guarantees a re-render at the moment the element
  // becomes available, which is when the virtualizer computes its first window.
  // Reading the ref directly in getScrollElement is not enough: nothing would
  // tell React to render again, so the list stayed empty.
  const [scrollEl, setScrollEl] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setScrollEl(scrollRef.current ?? null);
  }, [scrollRef, shouldVirtualize]);

  const virtualizer = useVirtualizer({
    count: shouldVirtualize ? rows.length : 0,
    getScrollElement: () => scrollEl,
    estimateSize: () => estimateRowHeight,
    overscan,
  });

  if (!shouldVirtualize) {
    return (
      <>
        {rows.map((row, rowIndex) => (
          <div
            key={`row-${rowIndex}`}
            className={rowClassName}
            data-testid="mod-row"
          >
            {row.map((item, colIndex) => {
              const flatIndex = rowIndex * safeColumns + colIndex;
              return (
                <div key={getKey(item, flatIndex)} data-testid="mod-item">
                  {renderItem(item, flatIndex)}
                </div>
              );
            })}
          </div>
        ))}
      </>
    );
  }

  const virtualRows = virtualizer.getVirtualItems();

  return (
    <div
      data-testid="virtual-container"
      style={{
        height: virtualizer.getTotalSize(),
        position: "relative",
        width: "100%",
        // Hover scale (1.05) must be able to paint outside the row box.
        overflow: "visible",
      }}
    >
      {virtualRows.map((virtualRow) => {
        const row = rows[virtualRow.index];
        if (!row) return null;
        return (
          <div
            key={virtualRow.key}
            ref={virtualizer.measureElement}
            data-index={virtualRow.index}
            data-testid="mod-row"
            className={rowClassName}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${virtualRow.start}px)`,
              overflow: "visible",
            }}
          >
            {row.map((item, colIndex) => {
              const flatIndex = virtualRow.index * safeColumns + colIndex;
              return (
                <div key={getKey(item, flatIndex)} data-testid="mod-item">
                  {renderItem(item, flatIndex)}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Column count matching the `.mods-grid` media queries in the page styles.
 *
 * The grid is defined in CSS, but the virtualizer has to know how many items sit
 * on a row to chunk them, so the breakpoints are mirrored here. Keep in sync with
 * the `.mods-grid` blocks in ActiveModsView / DownloadsPage / BrowsePage.
 */
export const GRID_BREAKPOINTS: ReadonlyArray<{ minWidth: number; columns: number }> = [
  { minWidth: 1500, columns: 5 },
  { minWidth: 1280, columns: 4 },
  { minWidth: 1024, columns: 3 },
  { minWidth: 768, columns: 2 },
  { minWidth: 0, columns: 1 },
];

export function columnsForWidth(width: number): number {
  for (const bp of GRID_BREAKPOINTS) {
    if (width >= bp.minWidth) return bp.columns;
  }
  return 1;
}

/** Live column count for grid mode; always 1 in list mode. */
export function useGridColumns(viewMode: "grid" | "list"): number {
  const [columns, setColumns] = useState(() =>
    typeof window === "undefined" ? 1 : columnsForWidth(window.innerWidth),
  );

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const update = () => setColumns(columnsForWidth(window.innerWidth));
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  return viewMode === "grid" ? columns : 1;
}
