/**
 * F7: the filter + sort chains must not recompute on unrelated re-renders.
 *
 * ActiveModsView, DownloadsPage and BrowsePage each ran a category filter, a
 * hierarchical character/skin filter and a multi-key sort — 130-200 lines of
 * array work — inline in the component body. That re-ran on EVERY render,
 * including renders caused entirely by unrelated parent state such as a polling
 * tick or a toast.
 *
 * These tests exercise the memoisation contract on a faithful miniature of the
 * pattern, then assert the real components declare useMemo over the chain.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { useMemo, useState } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

type Item = { name: string; active: boolean; rank: number };

const ITEMS: Item[] = Array.from({ length: 200 }, (_, i) => ({
  name: `mod-${String(i).padStart(3, "0")}`,
  active: i % 3 !== 0,
  // Descending so rank-order and name-order have DIFFERENT first elements.
  // An earlier `(i * 37) % 200` gave item 0 a rank of 0, making mod-000 first
  // under both sorts, so the "order actually changed" assertion was checking a
  // coincidence rather than the sort.
  rank: 200 - i,
}));

describe("memoised derivation", () => {
  it("does not recompute when an unrelated state value changes", () => {
    const compute = vi.fn((items: Item[], sortBy: string) => {
      const out = [...items];
      out.sort((a, b) => (sortBy === "rank" ? a.rank - b.rank : a.name.localeCompare(b.name)));
      return {
        active: out.filter((m) => m.active),
        inactive: out.filter((m) => !m.active),
      };
    });

    function Host() {
      const [sortBy] = useState("rank");
      // Stands in for a polling tick / toast / parent re-render.
      const [unrelated, setUnrelated] = useState(0);

      const { active, inactive } = useMemo(
        () => compute(ITEMS, sortBy),
        [sortBy],
      );

      return (
        <>
          <button onClick={() => setUnrelated((n) => n + 1)}>tick</button>
          <span data-testid="counts">{`${active.length}/${inactive.length}`}</span>
          <span data-testid="unrelated">{unrelated}</span>
        </>
      );
    }

    render(<Host />);
    expect(compute).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("counts")).toHaveTextContent("133/67");

    // Five unrelated re-renders.
    for (let i = 0; i < 5; i++) fireEvent.click(screen.getByText("tick"));

    expect(screen.getByTestId("unrelated")).toHaveTextContent("5");
    expect(
      compute,
      "the chain recomputed on renders that changed none of its inputs",
    ).toHaveBeenCalledTimes(1);
  });

  it("DOES recompute when a real input changes", () => {
    const compute = vi.fn((items: Item[], sortBy: string) => {
      const out = [...items];
      out.sort((a, b) => (sortBy === "rank" ? a.rank - b.rank : a.name.localeCompare(b.name)));
      return out;
    });

    function Host() {
      const [sortBy, setSortBy] = useState("rank");
      const sorted = useMemo(() => compute(ITEMS, sortBy), [sortBy]);
      return (
        <>
          <button onClick={() => setSortBy("name")}>sort by name</button>
          <span data-testid="first">{sorted[0]?.name}</span>
        </>
      );
    }

    render(<Host />);
    expect(compute).toHaveBeenCalledTimes(1);
    const firstByRank = screen.getByTestId("first").textContent;

    fireEvent.click(screen.getByText("sort by name"));
    expect(compute).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("first")).toHaveTextContent("mod-000");
    expect(screen.getByTestId("first").textContent).not.toBe(firstByRank);
  });

  it("returns a stable reference across unrelated renders", () => {
    // Reference stability is what lets memoised children (ModCard,
    // InstalledModCard) skip re-rendering too.
    const seen: unknown[] = [];

    function Host() {
      const [, setTick] = useState(0);
      const derived = useMemo(() => ITEMS.filter((m) => m.active), []);
      seen.push(derived);
      return <button onClick={() => setTick((n) => n + 1)}>tick</button>;
    }

    render(<Host />);
    fireEvent.click(screen.getByText("tick"));
    fireEvent.click(screen.getByText("tick"));

    expect(seen.length).toBeGreaterThan(1);
    for (const ref of seen) expect(ref).toBe(seen[0]);
  });
});

// ---------------------------------------------------------------------------
// The real components must actually use it
// ---------------------------------------------------------------------------
function source(file: string): string {
  return readFileSync(resolve(__dirname, "../../components", file), "utf8");
}

describe("components memoise their derivation", () => {
  it("ActiveModsView memoises the active/inactive split", () => {
    const s = source("ActiveModsView.tsx");
    // Tolerant of the multi-line destructure the memo actually uses.
    expect(s).toMatch(
      /const \{[\s\S]{0,200}?filteredActiveMods,[\s\S]{0,200}?filteredInactiveMods,[\s\S]{0,200}?\} = useMemo\(/,
    );
    // The sort must live inside THIS memo, not in the render body. Anchor on the
    // destructure — ActiveModsView contains other, unrelated useMemo calls, and a
    // bare indexOf("useMemo(() => {") matched the first of those.
    const declMatch = /const \{[\s\S]*?filteredInactiveMods,[\s\S]*?\} = useMemo\(\(\) => \{/.exec(s);
    expect(declMatch, "memo declaration not found").not.toBeNull();
    const memoStart = declMatch!.index;
    const memoEnd = s.indexOf("return {", memoStart);
    expect(memoEnd).toBeGreaterThan(memoStart);
    expect(s.slice(memoStart, memoEnd)).toMatch(/filteredMods\.sort\(/);
    // The empty-state copy must read counts off the memo, not recompute them.
    expect(s).toMatch(/filteredCount === 0/);
    expect(s).toMatch(/installedCount === 0/);
  });

  it.each(["DownloadsPage.tsx", "BrowsePage.tsx"])(
    "%s memoises its filter+sort chain",
    (file) => {
      const s = source(file);
      expect(s).toMatch(/const filteredMods = useMemo\(/);
      const memoStart = s.indexOf("const filteredMods = useMemo(");
      const memoEnd = s.indexOf("return filteredMods;", memoStart);
      expect(memoEnd).toBeGreaterThan(memoStart);
      expect(s.slice(memoStart, memoEnd)).toMatch(/filteredMods\.sort\(/);
    },
  );

  it.each(["ActiveModsView.tsx", "DownloadsPage.tsx", "BrowsePage.tsx"])(
    "%s no longer sorts in the render body",
    (file) => {
      const s = source(file);
      // Any `filteredMods.sort(` at two-space indent would be top-level in the
      // component body rather than inside the memo callback.
      const topLevelSorts = s
        .split(/\r?\n/)
        .filter((ln) => /^ {2}filteredMods\.sort\(/.test(ln));
      expect(topLevelSorts).toEqual([]);
    },
  );
});
