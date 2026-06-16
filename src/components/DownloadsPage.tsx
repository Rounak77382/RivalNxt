import { useEffect, useMemo, useState } from "react";
import type { Mod } from "./ModCard";
import { InstalledModCard } from "./InstalledModCard";
import { SearchHeader } from "./SearchHeader";
import { ModModal } from "./ModModal";
import {
  categoriesMatchTag,
  extractNonCategoryTags,
} from "../lib/categoryUtils";
import { lookupTags, type TagLookupResponse } from "../lib/api";

interface DownloadsPageProps {
  mods: Mod[];
  onUpdate: (modId: string) => void | Promise<void>;
  onCheckUpdate: (modId: string) => void | Promise<void>;
  onUninstall: (modId: string) => void | Promise<void>;
  onFavorite: (modId: string) => void;
  selectedCategory: string;
  selectedCharacters: string[];
  onModAdded?: () => Promise<void> | void;
  onConflictStateChanged?: () => void;
  viewMode: "grid" | "list";
  onViewModeChange: (mode: "grid" | "list") => void;
  onRefresh?: () => void;
}

export function DownloadsPage({
  mods,
  onUpdate,
  onCheckUpdate,
  onUninstall,
  onFavorite,
  selectedCategory,
  selectedCharacters,
  onModAdded,
  onConflictStateChanged,
  viewMode,
  onViewModeChange,
  onRefresh,
}: DownloadsPageProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<string>("Recent");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [selectedMod, setSelectedMod] = useState<Mod | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalInitialTab, setModalInitialTab] = useState<
    "overview" | "files" | "changelog" | "images" | "assets"
  >("overview");
  const [tagLookupMap, setTagLookupMap] = useState<TagLookupResponse>({});

  // Build a stable signature of all tags so we re-fetch only when tags actually change
  const tagsSignature = useMemo(() => {
    const all = new Set<string>();
    for (const mod of mods) {
      extractNonCategoryTags(mod.tags).forEach((t) => all.add(t));
    }
    return Array.from(all).sort().join("|");
  }, [mods]);

  // Fetch DB-backed character/skin classification for all tags
  useEffect(() => {
    let cancelled = false;
    if (!tagsSignature) { setTagLookupMap({}); return; }
    lookupTags(tagsSignature.split("|"))
      .then((map) => { if (!cancelled) setTagLookupMap(map); })
      .catch(() => { if (!cancelled) setTagLookupMap({}); });
    return () => { cancelled = true; };
  }, [tagsSignature]);

  useEffect(() => {
    if (!selectedMod) return;
    const updated = mods.find((mod) => {
      if (
        selectedMod.backendModId != null &&
        mod.backendModId != null &&
        mod.backendModId === selectedMod.backendModId
      ) {
        return true;
      }
      return mod.id === selectedMod.id;
    });
    if (!updated) {
      setSelectedMod(null);
      setIsModalOpen(false);
      return;
    }
    if (updated !== selectedMod) {
      setSelectedMod(updated);
    }
  }, [mods, selectedMod]);

  const handleOpenFilesTab = (modId: string) => {
    const mod = mods.find((m) => m.id === modId);
    if (mod) {
      setSelectedMod(mod);
      setModalInitialTab("files");
      setIsModalOpen(true);
    }
  };

  useEffect(() => {
    const handleOpenModModal = (e: Event) => {
      const customEvent = e as CustomEvent;
      const { modId, tab } = customEvent.detail;
      const mod = mods.find((m) => m.id === modId);
      if (mod) {
        setSelectedMod(mod);
        if (tab === "files") {
          setModalInitialTab("files");
        } else {
          setModalInitialTab("overview");
        }
        setIsModalOpen(true);
      }
    };
    window.addEventListener("open-mod-modal", handleOpenModModal);
    return () => window.removeEventListener("open-mod-modal", handleOpenModModal);
  }, [mods]);

  // Base: show only installed mods
  let filteredMods = mods.filter((mod) => mod.isInstalled);

  // Filter by category
  if (selectedCategory && selectedCategory !== "all") {
    filteredMods = filteredMods.filter(
      (mod) =>
        (Array.isArray(mod.categoryTags) &&
          mod.categoryTags.includes(selectedCategory)) ||
        categoriesMatchTag(mod.tags, selectedCategory),
    );
  }

  // Hierarchical filter using DB-backed tag classification.
  if (selectedCharacters && selectedCharacters.length > 0) {
    // Classify selected entries using the DB lookup map
    const selectedCharacterNames = new Set<string>(
      selectedCharacters.filter((t) => tagLookupMap[t]?.type === "character"),
    );
    const selectedSkinNames = selectedCharacters.filter(
      (t) => tagLookupMap[t]?.type === "skin",
    );

    filteredMods = filteredMods.filter((mod) => {
      const tags = extractNonCategoryTags(mod.tags);
      if (tags.length === 0) return false;

      // Find this mod's character tags and skin tags via DB classification
      const modCharacters = tags.filter(
        (t) => tagLookupMap[t]?.type === "character",
      );
      const modSkins = tags.filter((t) => tagLookupMap[t]?.type === "skin");

      // Step 1: at least one of the mod's characters must be selected (OR logic)
      const matchedChar = modCharacters.find((c) =>
        selectedCharacterNames.has(c),
      );
      if (!matchedChar) return false;

      // Step 2: find selected skins whose DB parent(s) include the matched character
      const selectedSkinsForChar = selectedSkinNames.filter((skin) => {
        const info = tagLookupMap[skin];
        const parents =
          info?.parents && info.parents.length > 0
            ? info.parents
            : info?.parent
              ? [info.parent]
              : [];
        return parents.includes(matchedChar);
      });

      // If no skins selected for this character, show all its mods
      if (selectedSkinsForChar.length === 0) return true;

      // Otherwise only show mods that carry at least one of the selected skins
      return modSkins.some((s) => selectedSkinsForChar.includes(s));
    });
  }

  // Search filter
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    filteredMods = filteredMods.filter(
      (mod) =>
        mod.name.toLowerCase().includes(q) ||
        mod.description.toLowerCase().includes(q) ||
        mod.author.toLowerCase().includes(q) ||
        mod.tags.some((t) => t.toLowerCase().includes(q)),
    );
  }

  // Sorting
  const applyOrder = (val: number) => (sortOrder === "asc" ? -val : val);
  const toNullableTimestamp = (value?: string | null): number | null => {
    if (!value) return null;
    const time = Date.parse(value);
    return Number.isNaN(time) ? null : time;
  };

  switch (sortBy) {
    case "Popular":
      filteredMods.sort((a, b) =>
        applyOrder((b.downloads || 0) - (a.downloads || 0)),
      );
      break;
    case "Uploaded":
      // Uploaded: sort by backendModId (numeric), then by installDate for missing ids
      filteredMods.sort((a, b) => {
        const aId = a.backendModId;
        const bId = b.backendModId;

        // If both have mod ids, sort by mod id
        if (aId != null && bId != null) {
          const idDiff = sortOrder === "asc" ? aId - bId : bId - aId;
          if (idDiff !== 0) return idDiff;
          // If mod ids are equal, fallback to install date
          const aDate = toNullableTimestamp(a.installDate);
          const bDate = toNullableTimestamp(b.installDate);
          if (aDate == null && bDate == null) return 0;
          if (aDate == null) return 1;
          if (bDate == null) return -1;
          return sortOrder === "asc" ? aDate - bDate : bDate - aDate;
        }

        // If only one has mod id, that one comes first (regardless of sort order)
        if (aId != null && bId == null) return -1;
        if (aId == null && bId != null) return 1;

        // If neither has mod id, sort by install date
        const aDate = toNullableTimestamp(a.installDate);
        const bDate = toNullableTimestamp(b.installDate);
        if (aDate == null && bDate == null) return 0;
        if (aDate == null) return 1;
        if (bDate == null) return -1;
        return sortOrder === "asc" ? aDate - bDate : bDate - aDate;
      });
      break;
    case "Recent":
      // Recent: sort by install date
      filteredMods.sort((a, b) => {
        const aDate = toNullableTimestamp(a.installDate);
        const bDate = toNullableTimestamp(b.installDate);
        if (aDate == null && bDate == null) return 0;
        if (aDate == null) return 1;
        if (bDate == null) return -1;
        return sortOrder === "asc" ? aDate - bDate : bDate - aDate;
      });
      break;
    case "Updated":
      // Sort by mods.updated_at (mapped to lastUpdatedRaw / lastUpdated); prioritize updates available
      filteredMods.sort((a, b) => {
        const aUpdate = a.hasUpdate || a.isUpdating ? 1 : 0;
        const bUpdate = b.hasUpdate || b.isUpdating ? 1 : 0;
        if (aUpdate !== bUpdate) {
          return bUpdate - aUpdate;
        }

        const ta = toNullableTimestamp(
          a.lastUpdatedRaw ?? a.lastUpdated ?? null,
        );
        const tb = toNullableTimestamp(
          b.lastUpdatedRaw ?? b.lastUpdated ?? null,
        );
        if (ta == null && tb == null) return 0;
        if (ta == null) return 1;
        if (tb == null) return -1;
        if (ta === tb) return 0;
        return sortOrder === "asc" ? ta - tb : tb - ta;
      });
      break;
    case "Rating":
      filteredMods.sort((a, b) =>
        applyOrder((b.rating || 0) - (a.rating || 0)),
      );
      break;
    case "Downloads":
      filteredMods.sort((a, b) =>
        applyOrder((b.downloads || 0) - (a.downloads || 0)),
      );
      break;
    case "Performance":
      filteredMods.sort((a, b) =>
        applyOrder((b.performanceImpact || 0) - (a.performanceImpact || 0)),
      );
      break;
    case "Name":
      filteredMods.sort((a, b) => applyOrder(a.name.localeCompare(b.name)));
      break;
    case "Category":
      filteredMods.sort((a, b) => {
        const categoryA = a.categoryTags?.[0] ?? a.category ?? "";
        const categoryB = b.categoryTags?.[0] ?? b.category ?? "";
        return applyOrder(categoryA.localeCompare(categoryB));
      });
      break;
    case "Favourites":
      filteredMods.sort((a, b) => {
        const aFav = a.isFavorited ? 1 : 0;
        const bFav = b.isFavorited ? 1 : 0;
        if (bFav !== aFav) return applyOrder(bFav - aFav);
        return a.name.localeCompare(b.name);
      });
      break;
    default:
      break;
  }

  return (
    <>
      <div className="flex flex-col h-full">
        {/* Search & view controls */}
        <SearchHeader
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          viewMode={viewMode}
          onViewModeChange={onViewModeChange}
          sortBy={sortBy}
          onSortChange={setSortBy}
          sortOrder={sortOrder}
          onSortOrderChange={setSortOrder}
          onModAdded={onModAdded}
        />

        {/* Mods grid/list */}
        <style>{`.custom-scrollbar::-webkit-scrollbar {
            width: 8px;
          }
          .custom-scrollbar::-webkit-scrollbar-track {
            background: transparent;
          }
          .custom-scrollbar::-webkit-scrollbar-thumb {
            background: rgba(100, 100, 100, 0.5);
            border-radius: 4px;
          }
          .custom-scrollbar::-webkit-scrollbar-thumb:hover {
            background: rgba(100, 100, 100, 0.7);
          }
          .custom-scrollbar {
            scrollbar-color: rgba(100, 100, 100, 0.5) transparent;
            scrollbar-width: thin;
          }
          .mods-grid {
            display: grid;
            gap: 1.5rem;
            grid-template-columns: 1fr;
          }
          @media (min-width: 768px) {
            .mods-grid {
              grid-template-columns: repeat(2, 1fr);
            }
          }
          @media (min-width: 1024px) {
            .mods-grid {
              grid-template-columns: repeat(3, 1fr);
            }
          }
          @media (min-width: 1280px) {
            .mods-grid {
              grid-template-columns: repeat(4, 1fr);
            }
          }
          @media (min-width: 1500px) {
            .mods-grid {
              grid-template-columns: repeat(5, 1fr);
            }
          }`}</style>
        <div
          className="flex-1 overflow-auto custom-scrollbar"
          style={{
            overflowY: "auto",
          }}
        >
          <div className="p-6">
            {filteredMods.length > 0 ? (
              <div
                className={
                  viewMode === "grid" ? "mods-grid" : "flex flex-col gap-0"
                }
              >
                {filteredMods.map((mod) => (
                  <InstalledModCard
                    key={`mod-${mod.backendModId ?? mod.id}`}
                    mod={mod}
                    viewMode={viewMode}
                    onUninstall={onUninstall}
                    onUpdate={onUpdate}
                    onCheckUpdate={onCheckUpdate}
                    onView={(m) => {
                      setSelectedMod(m);
                      setModalInitialTab("overview");
                      setIsModalOpen(true);
                    }}
                    onFavorite={onFavorite}
                    onOpenFilesTab={handleOpenFilesTab}
                  />
                ))}
              </div>
            ) : (
              <div className="text-center py-12">
                <h3 className="text-lg font-medium mb-2">No mods found</h3>
                <p className="text-muted-foreground">
                  Try adjusting your filters or search.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
      {selectedMod && (
        <ModModal
          mod={selectedMod}
          isOpen={isModalOpen}
          onClose={() => {
            setIsModalOpen(false);
            setModalInitialTab("overview");
          }}
          onInstall={() => {}}
          onFavorite={onFavorite}
          onConflictStateChanged={onConflictStateChanged}
          onRefresh={onRefresh}
          initialTab={modalInitialTab}
          onUpdate={onUpdate}
        />
      )}
    </>
  );
}
