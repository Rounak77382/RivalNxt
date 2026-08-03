/**
 * F2: no CSS transition may animate a layout property.
 *
 * Animating width/height/padding/margin/gap/max-width/top/left forces the browser
 * to re-run layout on every frame of the transition, for the animated element and
 * everything after it in flow. `transition: all` is equally bad: it makes the
 * browser watch every animatable property, layout ones included.
 *
 * These tests parse the real src/index.css rather than a fixture, so they fail if
 * anyone reintroduces the pattern.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const CSS_PATH = resolve(__dirname, "../../index.css");
const cssRaw = readFileSync(CSS_PATH, "utf8");

/**
 * Strip /* *​/ comments but keep line numbering intact, so reported line numbers
 * still match the file. A line-prefix heuristic is not enough: prose inside a
 * block comment can mention `will-change: padding, gap` mid-line, which is
 * exactly what tripped the first run of this suite.
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, (block) =>
    block.replace(/[^\n]/g, " "),
  );
}

const css = stripComments(cssRaw);

/** Properties whose animation forces layout (reflow) every frame. */
const LAYOUT_PROPS: string[] = [
  "width",
  "height",
  "min-width",
  "min-height",
  "max-width",
  "max-height",
  "padding",
  "padding-top",
  "padding-right",
  "padding-bottom",
  "padding-left",
  "margin",
  "margin-top",
  "margin-right",
  "margin-bottom",
  "margin-left",
  "gap",
  "row-gap",
  "column-gap",
  "top",
  "right",
  "bottom",
  "left",
  "border-width",
  "font-size",
  "flex-basis",
];

type Decl = { prop: string; value: string; line: number };

/** Collect every `transition` / `transition-property` declaration with its line. */
function collectTransitionDecls(source: string): Decl[] {
  const out: Decl[] = [];
  source.split(/\r?\n/).forEach((line, i) => {
    // Comments are already blanked by stripComments(), so no prefix heuristic.
    const match = /(^|[\s;{])(transition(?:-property)?)\s*:\s*([^;}]+)/.exec(line);
    if (!match) return;
    out.push({ prop: match[2], value: match[3].trim(), line: i + 1 });
  });
  return out;
}

const decls = collectTransitionDecls(css);

/**
 * Split a shorthand `transition` value into one entry per targeted property,
 * keeping whether that entry uses a STEP timing function.
 *
 * The harm being guarded against is *interpolated* layout animation: ~12 reflows
 * over a 200ms transition. `step-start` / `step-end` / `steps()` snap the value
 * exactly once, costing a single reflow. So a layout property is permitted only
 * when it is stepped — and the test below asserts the step function is really
 * there, so removing it re-fails.
 */
type Target = { prop: string; stepped: boolean };

function targetedProps(value: string): Target[] {
  return value
    .split(",")
    .map((raw) => {
      const part = raw.trim();
      const stepped = /\b(step-start|step-end)\b|\bsteps\(/.test(part);
      const prop =
        part
          .replace(/\b\d*\.?\d+m?s\b/g, "")
          .replace(
            /\b(ease|ease-in|ease-out|ease-in-out|linear|step-start|step-end)\b/g,
            "",
          )
          .replace(/\bcubic-bezier\([^)]*\)/g, "")
          .replace(/\bsteps\([^)]*\)/g, "")
          .trim()
          .split(/\s+/)[0]
          ?.toLowerCase() ?? "";
      return { prop, stepped };
    })
    .filter((t) => Boolean(t.prop));
}

describe("index.css transition hygiene", () => {
  it("parses transition declarations from the real stylesheet", () => {
    // Guards the test itself: if the regex stops matching, the assertions below
    // would vacuously pass.
    expect(decls.length).toBeGreaterThan(0);
  });

  it("no rule INTERPOLATES a layout property", () => {
    const offenders: string[] = [];
    for (const d of decls) {
      for (const target of targetedProps(d.value)) {
        if (LAYOUT_PROPS.includes(target.prop) && !target.stepped) {
          offenders.push(
            `index.css:${d.line} -> ${d.prop}: ${d.value} (interpolates layout prop "${target.prop}")`,
          );
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("any stepped layout property really carries a step timing function", () => {
    // Prevents the exception above from being abused: if someone drops the
    // `step-end` from `.header-action-text`, the previous test starts failing.
    const stepped = decls.flatMap((d) =>
      targetedProps(d.value)
        .filter((t) => LAYOUT_PROPS.includes(t.prop))
        .map((t) => ({ line: d.line, value: d.value, ...t })),
    );
    for (const s of stepped) {
      expect(s.stepped, `index.css:${s.line} -> ${s.value}`).toBe(true);
      expect(s.value).toMatch(/step-(start|end)|steps\(/);
    }
    // The width reveal is the only place this exception is used today.
    expect(stepped.every((s) => s.prop === "width")).toBe(true);
  });

  it("the three former `transition: all` rules now name their properties", () => {
    for (const selector of [".mod-file-item", ".card-container", ".card-list-item"]) {
      const block = new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`).exec(css);
      expect(block, `${selector} block not found`).not.toBeNull();
      const body = block![1];
      expect(body, `${selector} still uses transition: all`).not.toMatch(/transition:\s*all/);
      expect(body, `${selector} lost its transition`).toMatch(/transition:/);
    }
  });

  it(".header-action-btn no longer animates padding or gap", () => {
    const block = /\.header-action-btn\s*\{([^}]*)\}/.exec(css);
    expect(block).not.toBeNull();
    const body = block![1];
    const transition = /transition:\s*([^;]+)/.exec(body)?.[1] ?? "";
    expect(transition).not.toMatch(/\bpadding\b/);
    expect(transition).not.toMatch(/\bgap\b/);
    // will-change on layout props reserves resources for something the compositor
    // cannot accelerate.
    const willChange = /will-change:\s*([^;]+)/.exec(body)?.[1] ?? "";
    expect(willChange).not.toMatch(/\bpadding\b/);
    expect(willChange).not.toMatch(/\bgap\b/);
  });

  it(".header-action-text animates transform, not max-width", () => {
    const block = /\.header-action-text\s*\{([^}]*)\}/.exec(css);
    expect(block).not.toBeNull();
    const body = block![1];
    const transition = /transition:\s*([^;]+)/.exec(body)?.[1] ?? "";
    expect(transition).toMatch(/\btransform\b/);
    expect(transition).not.toMatch(/\bmax-width\b/);

    const willChange = /will-change:\s*([^;]+)/.exec(body)?.[1] ?? "";
    expect(willChange).not.toMatch(/\bmax-width\b/);
    expect(willChange).toMatch(/\btransform\b/);
  });

  it("the hover reveal still exists (behaviour preserved)", () => {
    // The label must still become visible on hover, or the fix broke the UI.
    expect(css).toMatch(/\.header-action-btn:hover\s+\.header-action-text/);
    const hoverBlock = /\.header-action-btn:hover\s+\.header-action-text[^{]*\{([^}]*)\}/.exec(css);
    expect(hoverBlock).not.toBeNull();
    expect(hoverBlock![1]).toMatch(/opacity:\s*1/);
    expect(hoverBlock![1]).toMatch(/scaleX\(1\)/);
  });

  it("respects prefers-reduced-motion for the reveal", () => {
    expect(css).toMatch(/prefers-reduced-motion/);
  });

  it("will-change is never applied to a layout property anywhere", () => {
    const offenders: string[] = [];
    css.split(/\r?\n/).forEach((line, i) => {
      const m = /will-change:\s*([^;}]+)/.exec(line);
      if (!m) return;
      for (const raw of m[1].split(",")) {
        const p = raw.trim().toLowerCase();
        if (LAYOUT_PROPS.includes(p)) {
          offenders.push(`index.css:${i + 1} -> will-change: ${m[1].trim()}`);
        }
      }
    });
    expect(offenders).toEqual([]);
  });
});
