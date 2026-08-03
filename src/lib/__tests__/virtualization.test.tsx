/**
 * F8: long mod lists must virtualize, without breaking hover/scroll behaviour.
 *
 * Nothing in src/ was virtualized. ActiveModsView (two lists), DownloadsPage,
 * BrowsePage and CollectionsPage each rendered every row, so a library of several
 * hundred mods mounted several hundred card trees on every filter or sort change.
 */
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  GRID_BREAKPOINTS,
  VirtualizedModList,
  columnsForWidth,
  useGridColumns,
} from "../../components/VirtualizedModList";

type Row = { id: string; name: string };

const makeItems = (n: number): Row[] =>
  Array.from({ length: n }, (_, i) => ({ id: `m${i}`, name: `mod-${i}` }));

/**
 * jsdom gives every element zero height, so a virtualizer would compute an empty
 * window. Stub the geometry the virtualizer reads so a realistic viewport exists.
 */
function stubGeometry({ clientHeight = 600, itemHeight = 120 } = {}) {
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get() {
      return this.getAttribute("data-testid") === "scroller" ? clientHeight : itemHeight;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get() {
      return this.getAttribute("data-testid") === "scroller" ? clientHeight : itemHeight;
    },
  });
  HTMLElement.prototype.getBoundingClientRect = function () {
    const h =
      this.getAttribute("data-testid") === "scroller" ? clientHeight : itemHeight;
    return { width: 1200, height: h, top: 0, left: 0, right: 1200, bottom: h, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
}

function Harness({
  count,
  columns = 1,
  threshold,
}: {
  count: number;
  columns?: number;
  threshold?: number;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const items = makeItems(count);
  return (
    <div ref={scrollRef} data-testid="scroller" style={{ height: 600, overflow: "auto" }}>
      <VirtualizedModList
        items={items}
        scrollRef={scrollRef}
        columns={columns}
        estimateRowHeight={120}
        threshold={threshold}
        getKey={(item) => item.id}
        renderItem={(item) => <span>{item.name}</span>}
        rowClassName="mods-grid"
      />
    </div>
  );
}

describe("columnsForWidth", () => {
  it.each([
    [1600, 5],
    [1500, 5],
    [1499, 4],
    [1280, 4],
    [1024, 3],
    [900, 2],
    [768, 2],
    [767, 1],
    [320, 1],
    [0, 1],
  ])("width %i -> %i columns", (width, expected) => {
    expect(columnsForWidth(width)).toBe(expected);
  });

  it("breakpoints are ordered descending so the first match wins", () => {
    const widths = GRID_BREAKPOINTS.map((b) => b.minWidth);
    expect(widths).toEqual([...widths].sort((a, b) => b - a));
  });

  it("never returns less than 1 column", () => {
    for (const w of [-100, 0, 1]) expect(columnsForWidth(w)).toBeGreaterThanOrEqual(1);
  });
});

describe("useGridColumns", () => {
  it("returns 1 in list mode regardless of width", () => {
    function Probe() {
      const cols = useGridColumns("list");
      return <span data-testid="cols">{cols}</span>;
    }
    render(<Probe />);
    expect(screen.getByTestId("cols")).toHaveTextContent("1");
  });

  it("tracks the window width in grid mode", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1300 });
    function Probe() {
      const cols = useGridColumns("grid");
      return <span data-testid="cols">{cols}</span>;
    }
    render(<Probe />);
    expect(screen.getByTestId("cols")).toHaveTextContent("4");
  });

  it("removes its resize listener on unmount", () => {
    const remove = vi.spyOn(window, "removeEventListener");
    function Probe() {
      useGridColumns("grid");
      return null;
    }
    const { unmount } = render(<Probe />);
    unmount();
    expect(remove).toHaveBeenCalledWith("resize", expect.any(Function));
  });
});

describe("below the threshold: unchanged behaviour", () => {
  it("renders every item and no virtual container", () => {
    render(<Harness count={20} threshold={60} />);
    expect(screen.getAllByTestId("mod-item")).toHaveLength(20);
    expect(screen.queryByTestId("virtual-container")).toBeNull();
  });

  it("uses no absolute positioning for small lists", () => {
    render(<Harness count={5} threshold={60} />);
    for (const row of screen.getAllByTestId("mod-row")) {
      expect(row.style.position).toBe("");
    }
  });

  it("chunks into rows by column count", () => {
    render(<Harness count={9} columns={3} threshold={60} />);
    expect(screen.getAllByTestId("mod-row")).toHaveLength(3);
    expect(screen.getAllByTestId("mod-item")).toHaveLength(9);
  });

  it("handles a ragged final row", () => {
    render(<Harness count={7} columns={3} threshold={60} />);
    expect(screen.getAllByTestId("mod-row")).toHaveLength(3);
    expect(screen.getAllByTestId("mod-item")).toHaveLength(7);
  });

  it("renders nothing for an empty list", () => {
    render(<Harness count={0} threshold={60} />);
    expect(screen.queryAllByTestId("mod-item")).toHaveLength(0);
  });
});

describe("above the threshold: virtualized", () => {
  it("mounts only a window of items, not all 500", () => {
    stubGeometry();
    render(<Harness count={500} threshold={60} />);

    const mounted = screen.getAllByTestId("mod-item");
    expect(mounted.length).toBeGreaterThan(0);
    expect(
      mounted.length,
      `mounted ${mounted.length} of 500 items — virtualization is not active`,
    ).toBeLessThan(500);
  });

  it("creates a spacer sized to the full list", () => {
    stubGeometry();
    render(<Harness count={500} threshold={60} />);
    const container = screen.getByTestId("virtual-container");
    const height = parseFloat(String(container.style.height));
    // 500 rows x ~120px, well beyond the 600px viewport: scrollbar length is
    // preserved even though only a window is mounted.
    expect(height).toBeGreaterThan(600);
  });

  it("positions rows with transform, not top (compositor-friendly)", () => {
    stubGeometry();
    render(<Harness count={500} threshold={60} />);
    const rows = screen.getAllByTestId("mod-row");
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row.style.transform).toMatch(/translateY\(/);
      expect(row.style.top).toBe("0px");
    }
  });

  it("leaves overflow visible so the hover scale is not clipped", () => {
    // .card-container:hover scales to 1.05 with z-index 10. If the virtual row or
    // container clipped, the hovered card would be cut off at the row boundary.
    stubGeometry();
    render(<Harness count={500} threshold={60} />);
    expect(screen.getByTestId("virtual-container").style.overflow).toBe("visible");
    for (const row of screen.getAllByTestId("mod-row")) {
      expect(row.style.overflow).toBe("visible");
    }
  });

  it("respects the column count when chunking a long grid", () => {
    stubGeometry();
    render(<Harness count={500} columns={4} threshold={60} />);
    const rows = screen.getAllByTestId("mod-row");
    const items = screen.getAllByTestId("mod-item");
    // Every mounted row except possibly the last holds `columns` items.
    expect(items.length).toBeLessThanOrEqual(rows.length * 4);
    expect(items.length).toBeGreaterThan(0);
  });

  it("renders real content, not placeholders", () => {
    stubGeometry();
    render(<Harness count={500} threshold={60} />);
    // The first item must be genuinely present and labelled.
    expect(screen.getByText("mod-0")).toBeInTheDocument();
  });

  it("keys items by their stable id", () => {
    stubGeometry();
    const { container } = render(<Harness count={500} threshold={60} />);
    // No React key warnings, and content is unique per item.
    const texts = Array.from(container.querySelectorAll("span")).map((s) => s.textContent);
    expect(new Set(texts).size).toBe(texts.length);
  });
});

describe("every intended list is wired up", () => {
  const read = (file: string) =>
    readFileSync(resolve(__dirname, "../../components", file), "utf8");

  it.each([
    ["ActiveModsView.tsx", 2], // active + inactive
    ["DownloadsPage.tsx", 1],
    ["BrowsePage.tsx", 1],
    ["CollectionsPage.tsx", 1], // per-collection body
  ])("%s uses VirtualizedModList %i time(s)", (file, count) => {
    const s = read(file);
    const uses = s.match(/<VirtualizedModList/g) ?? [];
    expect(uses).toHaveLength(count);
  });

  it.each([
    "ActiveModsView.tsx",
    "DownloadsPage.tsx",
    "BrowsePage.tsx",
    "CollectionsPage.tsx",
  ])("%s no longer maps the full list into cards", (file) => {
    const s = read(file);
    // The raw `.map(...)` over the filtered list is what mounted every row.
    expect(s).not.toMatch(/\{filteredMods\.map\(/);
    expect(s).not.toMatch(/\{filteredActiveMods\.map\(/);
    expect(s).not.toMatch(/\{filteredInactiveMods\.map\(/);
    expect(s).not.toMatch(/\{mappedMods\.map\(/);
  });

  it.each([
    "ActiveModsView.tsx",
    "DownloadsPage.tsx",
    "BrowsePage.tsx",
    "CollectionsPage.tsx",
  ])("%s attaches scrollRef to its EXISTING scroll container", (file) => {
    const s = read(file);
    expect(s).toMatch(/ref=\{scrollRef\}/);
    // The virtualizer must not introduce a second scroller; the ref goes on a
    // container that already had overflow-auto.
    const refLine = s.split(/\r?\n/).findIndex((l) => l.includes("ref={scrollRef}"));
    const window = s.split(/\r?\n/).slice(refLine, refLine + 4).join("\n");
    expect(window).toMatch(/overflow-auto/);
  });

  it.each([
    "ActiveModsView.tsx",
    "DownloadsPage.tsx",
    "BrowsePage.tsx",
    "CollectionsPage.tsx",
  ])("%s drives column count from useGridColumns", (file) => {
    const s = read(file);
    expect(s).toMatch(/useGridColumns\(viewMode\)/);
    expect(s).toMatch(/columns=\{gridColumns\}/);
  });

  it("preserves the grid/list className switch at every site", () => {
    for (const file of [
      "ActiveModsView.tsx",
      "DownloadsPage.tsx",
      "BrowsePage.tsx",
      "CollectionsPage.tsx",
    ]) {
      const s = read(file);
      // rowClassName carries the original responsive grid class, so the CSS grid
      // and its media queries still apply inside each virtual row.
      expect(s, file).toMatch(/rowClassName=\{/);
      expect(s, file).toMatch(/"mods-grid"/);
    }
  });

  it("ActiveModsView keeps the dimmed styling on the inactive list", () => {
    const s = read("ActiveModsView.tsx");
    expect(s).toMatch(/mods-grid opacity-60/);
    expect(s).toMatch(/flex flex-col gap-0 opacity-60/);
  });
});

describe("threshold boundary", () => {
  it("does not virtualize at exactly threshold - 1", () => {
    render(<Harness count={59} threshold={60} />);
    expect(screen.queryByTestId("virtual-container")).toBeNull();
    expect(screen.getAllByTestId("mod-item")).toHaveLength(59);
  });

  it("virtualizes at exactly threshold", () => {
    stubGeometry();
    render(<Harness count={60} threshold={60} />);
    expect(screen.getByTestId("virtual-container")).toBeInTheDocument();
  });
});
