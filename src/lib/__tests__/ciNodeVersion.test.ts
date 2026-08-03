/**
 * The CI Node version must satisfy the test toolchain's own engine ranges.
 *
 * This exists because of a silent 15-minute failure. `.github/workflows/ci.yml`
 * pinned Node 18, but vitest 4 requires ^20 || ^22 || >=24 and jsdom 27 requires
 * ^20.19 || ^22.12 || >=24. `npm ci` only *warned* (EBADENGINE), typecheck and
 * build passed, then `vitest run` printed its banner and produced no further
 * output until the job hit `timeout-minutes: 15`. GitHub reports a timed-out job
 * as "cancelled", so it did not even look like a version problem.
 *
 * Nothing else in the repo would catch that: the suite passes locally on Node 26,
 * and a lockfile bump to a package with a newer engine range breaks CI only.
 */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const REPO = resolve(__dirname, "../../..");
const CI_YML = resolve(REPO, ".github/workflows/ci.yml");

/** Packages whose engine range the CI runtime must satisfy to run the suite. */
const TEST_TOOLCHAIN = ["vitest", "jsdom"];

type Version = { major: number; minor: number; patch: number };

function parseVersion(text: string): Version | null {
  const m = text.trim().match(/^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?/);
  if (!m) return null;
  return {
    major: Number(m[1]),
    minor: Number(m[2] ?? 0),
    patch: Number(m[3] ?? 0),
  };
}

function gte(a: Version, b: Version): boolean {
  if (a.major !== b.major) return a.major > b.major;
  if (a.minor !== b.minor) return a.minor > b.minor;
  return a.patch >= b.patch;
}

/**
 * Minimal semver-range check covering the two forms npm engine fields actually
 * use here: `^A.B.C` and `>=A.B.C`, joined by `||`. `semver` is not a dependency
 * of this project, so rather than add one for a single assertion this handles the
 * subset and is unit-tested below. Anything it cannot parse is reported, not
 * silently treated as satisfied.
 */
export function satisfies(version: string, range: string): boolean {
  const v = parseVersion(version);
  if (!v) throw new Error(`unparseable version: ${version}`);

  return range.split("||").some((clause) => {
    const part = clause.trim();
    if (part === "*" || part === "") return true;

    if (part.startsWith("^")) {
      const base = parseVersion(part.slice(1));
      if (!base) throw new Error(`unparseable range clause: ${part}`);
      // ^ allows changes that do not modify the leftmost non-zero digit. Every
      // range in play here has a non-zero major.
      return v.major === base.major && gte(v, base);
    }
    if (part.startsWith(">=")) {
      const base = parseVersion(part.slice(2));
      if (!base) throw new Error(`unparseable range clause: ${part}`);
      return gte(v, base);
    }
    throw new Error(`unsupported range clause: ${part} (in "${range}")`);
  });
}

/** The node-version pinned inside a named job block of ci.yml. */
function nodeVersionForJob(yml: string, job: string): string | null {
  const lines = yml.split(/\r?\n/);
  const start = lines.findIndex((l) => l === `  ${job}:`);
  if (start === -1) return null;
  // Job keys sit at exactly two spaces of indent; the block ends at the next one.
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^ {2}\S/.test(lines[i])) {
      end = i;
      break;
    }
  }
  for (const line of lines.slice(start, end)) {
    const m = line.match(/node-version:\s*"?([^"\s#]+)"?/);
    if (m) return m[1];
  }
  return null;
}

function enginesNode(pkg: string): string | null {
  const path = resolve(REPO, "node_modules", pkg, "package.json");
  if (!existsSync(path)) return null;
  const json = JSON.parse(readFileSync(path, "utf8"));
  return json?.engines?.node ?? null;
}

describe("satisfies (minimal range check)", () => {
  // Load-bearing for the assertions below, so it is verified rather than trusted
  // -- including the exact real-world case that was missed.
  it("handles the caret form", () => {
    expect(satisfies("20.1.0", "^20.0.0")).toBe(true);
    expect(satisfies("21.0.0", "^20.0.0")).toBe(false);
    expect(satisfies("19.9.9", "^20.0.0")).toBe(false);
  });

  it("respects minor and patch floors inside a caret clause", () => {
    // jsdom's ^22.12.0 is why "22" is required rather than any 22.x.
    expect(satisfies("22.12.0", "^22.12.0")).toBe(true);
    expect(satisfies("22.20.0", "^22.12.0")).toBe(true);
    expect(satisfies("22.11.0", "^22.12.0")).toBe(false);
  });

  it("handles the >= form", () => {
    expect(satisfies("24.0.0", ">=24.0.0")).toBe(true);
    expect(satisfies("26.6.0", ">=24.0.0")).toBe(true);
    expect(satisfies("23.9.9", ">=24.0.0")).toBe(false);
  });

  it("handles alternation", () => {
    const range = "^20.0.0 || ^22.0.0 || >=24.0.0";
    expect(satisfies("22", range)).toBe(true);
    expect(satisfies("26", range)).toBe(true);
    expect(satisfies("18", range), "Node 18 must not read as compatible").toBe(false);
    expect(satisfies("23", range)).toBe(false);
  });

  it("treats a bare major as .0.0", () => {
    expect(satisfies("22", "^22.0.0")).toBe(true);
    // And therefore reports the jsdom floor honestly rather than optimistically.
    expect(satisfies("22", "^22.12.0")).toBe(false);
  });

  it("throws rather than passing on a clause it cannot parse", () => {
    expect(() => satisfies("22", "~22.0.0")).toThrow(/unsupported range clause/);
  });
});

describe("ci.yml frontend job Node version", () => {
  const yml = readFileSync(CI_YML, "utf8");

  it("declares a node-version", () => {
    expect(nodeVersionForJob(yml, "frontend"), "no node-version in the frontend job").toBeTruthy();
  });

  it("is not the Node 18 that hung the job", () => {
    expect(nodeVersionForJob(yml, "frontend")).not.toBe("18");
  });

  it.each(TEST_TOOLCHAIN)("satisfies the engine range of %s", (pkg) => {
    const range = enginesNode(pkg);
    if (!range) {
      // Not installed (or no engines field): nothing to assert, and skipping is
      // honest -- but the package list is small and pinned, so this should not
      // happen on a normal checkout.
      expect(existsSync(resolve(REPO, "node_modules", pkg)), `${pkg} is not installed`).toBe(true);
      return;
    }
    const pinned = nodeVersionForJob(yml, "frontend")!;

    // A bare major like "22" resolves at runtime to the newest 22.x, which
    // satisfies a ^22.12.0 floor even though "22.0.0" would not. Compare against
    // that resolved reading, otherwise a correct pin looks broken.
    const resolvedFloor = /^\d+$/.test(pinned) ? `${pinned}.9999.9999` : pinned;

    expect(
      satisfies(resolvedFloor, range),
      `ci.yml frontend job pins Node ${pinned}, but ${pkg} requires ${range}`,
    ).toBe(true);
  });

  it("the build-only jobs may stay on an older Node, and that is recorded", () => {
    // Not a bug: vite 6 supports 18 and those jobs never run vitest. Asserted so
    // that if someone bumps them, the reason this job differs is re-examined
    // rather than silently drifting.
    expect(nodeVersionForJob(yml, "desktop")).toBe("18");
    expect(yml).toMatch(/desktop job below and release\.yml stay on 18/);
  });
});
