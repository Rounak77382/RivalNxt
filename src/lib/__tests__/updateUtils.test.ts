import { describe, it, expect } from "vitest";
import {
  normalizeVersionForCheck,
  versionsEquivalent,
  getVariantIdentityKey,
  isVariantActuallyUpdatable,
} from "../updateUtils";

describe("updateUtils", () => {
  describe("normalizeVersionForCheck", () => {
    it("handles null and undefined", () => {
      expect(normalizeVersionForCheck(null)).toBe("");
      expect(normalizeVersionForCheck(undefined)).toBe("");
    });

    it("normalizes version strings", () => {
      expect(normalizeVersionForCheck("1.2")).toBe("v1.2");
      expect(normalizeVersionForCheck("v1.2")).toBe("v1.2");
      expect(normalizeVersionForCheck("V1.2")).toBe("v1.2");
      expect(normalizeVersionForCheck("1.2.1763999096")).toBe("v1.2");
    });
  });

  describe("versionsEquivalent", () => {
    it("handles equal versions", () => {
      expect(versionsEquivalent("1.0", "1.0")).toBe(true);
      expect(versionsEquivalent("v1.0", "1.0")).toBe(true);
      expect(versionsEquivalent("1.0.0", "1.0")).toBe(true);
      expect(versionsEquivalent("2.0.1743611945", "2.0")).toBe(true);
    });

    it("detects real updates correctly (directional)", () => {
      expect(versionsEquivalent("2", "2.5")).toBe(false);
      expect(versionsEquivalent("1.0", "1.1")).toBe(false);
      expect(versionsEquivalent("2", "2.177.1")).toBe(false);
    });
  });

  describe("getVariantIdentityKey", () => {
    it("strips extensions, versions, timestamps and punctuation", () => {
      const k1 = getVariantIdentityKey("IronMan-123-1-0-1763999096.zip", 123);
      const k2 = getVariantIdentityKey("IronMan-123-2-0-1774999096.zip", 123);
      expect(k1).toBe("ironman");
      expect(k2).toBe("ironman");
      expect(k1).toBe(k2);
    });

    it("distinguishes different variants", () => {
      const k1 = getVariantIdentityKey("IronMan_Red_Skin-123-1-0-1763999096.zip", 123);
      const k2 = getVariantIdentityKey("IronMan_Blue_Skin-123-1-0-1763999096.zip", 123);
      expect(k1).not.toBe(k2);
    });
  });

  describe("isVariantActuallyUpdatable", () => {
    it("returns false if variant is already matching latest", () => {
      const v1 = {
        name: "Mod.zip",
        version: "2.0",
        latest_version: "2.0",
        needs_update: true, // stale flag
      };
      expect(isVariantActuallyUpdatable(v1, [v1])).toBe(false);
    });

    it("returns false for an old download when a newer download of same variant is up-to-date", () => {
      const oldDl = {
        name: "Mod_v1.0.zip",
        version: "1.0",
        latest_version: "2.0",
        local_version_key: "00001.00000.00000.00000",
        latest_version_key: "00002.00000.00000.00000",
        needs_update: true,
        created_at: "2024-01-01T00:00:00Z",
      };
      const newDl = {
        name: "Mod_v2.0.zip",
        version: "2.0",
        latest_version: "2.0",
        local_version_key: "00002.00000.00000.00000",
        latest_version_key: "00002.00000.00000.00000",
        needs_update: false,
        created_at: "2024-01-02T00:00:00Z",
      };
      expect(isVariantActuallyUpdatable(oldDl, [oldDl, newDl])).toBe(false);
      expect(isVariantActuallyUpdatable(newDl, [oldDl, newDl])).toBe(false);
    });

    it("returns true when a variant genuinely has an update and is not superseded", () => {
      const variantA = {
        name: "SkinA.zip",
        version: "1.0",
        latest_version: "2.0",
        local_version_key: "00001.00000.00000.00000",
        latest_version_key: "00002.00000.00000.00000",
        needs_update: true,
        created_at: "2024-01-01T00:00:00Z",
      };
      const variantB = {
        name: "SkinB.zip",
        version: "1.0",
        latest_version: "1.0",
        local_version_key: "00001.00000.00000.00000",
        latest_version_key: "00001.00000.00000.00000",
        needs_update: false,
        created_at: "2024-01-01T00:00:00Z",
      };
      expect(isVariantActuallyUpdatable(variantA, [variantA, variantB])).toBe(true);
      expect(isVariantActuallyUpdatable(variantB, [variantA, variantB])).toBe(false);
    });
  });
});
