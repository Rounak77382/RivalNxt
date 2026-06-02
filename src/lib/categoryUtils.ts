const CATEGORY_KEYWORDS: Record<string, string[]> = {
  characters: [
    "character",
    "characters",
    "mesh",
    "meshes",
    "material",
    "materials",
    "texture",
    "textures",
    "animation",
    "animations",
    "vfx",
    "effects",
    "blueprint",
    "blueprints",
  ],
  ui: ["ui", "hud", "menu", "interface", "widgets", "widget", "umg", "slate"],
  maps: [
    "map",
    "maps",
    "level",
    "levels",
    "environment",
    "environments",
    "world",
    "worlds",
  ],
  audio: [
    "audio",
    "sound",
    "sounds",
    "music",
    "voice",
    "voices",
    "sfx",
    "wwise",
  ],
};

const CATEGORY_TOKEN_SET = new Set<string>(
  Object.values(CATEGORY_KEYWORDS).flat()
);

function normalizeTag(tag?: string | null): string | undefined {
  if (tag == null) return undefined;
  const trimmed = String(tag).trim().toLowerCase();
  return trimmed.length > 0 ? trimmed : undefined;
}

function buildTagSet(tags: Array<string | null | undefined> | undefined) {
  const set = new Set<string>();
  if (!tags) {
    return set;
  }
  for (const tag of tags) {
    const normalized = normalizeTag(tag);
    if (normalized) {
      set.add(normalized);
    }
  }
  return set;
}

export function deriveCategoryTags(
  tags: Array<string | null | undefined> | undefined
): string[] {
  const tagSet = buildTagSet(tags);
  const categories: string[] = [];
  for (const [category, keywords] of Object.entries(CATEGORY_KEYWORDS)) {
    if (keywords.some((keyword) => tagSet.has(keyword))) {
      categories.push(category);
    }
  }
  return categories;
}

export function categoriesMatchTag(
  tags: Array<string | null | undefined> | undefined,
  categoryId: string
): boolean {
  const keywords = CATEGORY_KEYWORDS[categoryId];
  if (!keywords) return false;
  const tagSet = buildTagSet(tags);
  return keywords.some((keyword) => tagSet.has(keyword));
}

export function getCategoryKeywords(): Record<string, string[]> {
  return CATEGORY_KEYWORDS;
}

export function getCategoryTokenSet(): Set<string> {
  return CATEGORY_TOKEN_SET;
}

export function extractNonCategoryTags(
  tags: Array<string | null | undefined> | undefined
): string[] {
  if (!tags) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const tag of tags) {
    if (tag == null) continue;
    const original = String(tag).trim();
    if (!original) continue;
    const normalized = original.toLowerCase();
    if (CATEGORY_TOKEN_SET.has(normalized)) continue;
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(original);
  }
  return result;
}
