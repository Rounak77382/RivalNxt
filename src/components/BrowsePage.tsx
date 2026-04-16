import { useState } from "react";
import { SearchHeader } from "./SearchHeader";
import { ModCard } from "./ModCard";
import { ModModal } from "./ModModal";
import type { Mod } from "./ModCard";

interface BrowsePageProps {
  mods: Mod[];
  onInstall: (modId: string) => void;
  onFavorite: (modId: string) => void;
}

export function BrowsePage({ mods, onInstall, onFavorite }: BrowsePageProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [sortBy, setSortBy] = useState("Popular");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [selectedMod, setSelectedMod] = useState<Mod | null>(null);

  // Build filtered mods from live data (no mock helpers)
  let filteredMods = [...mods];

  // No category or character filters - BrowsePage shows all mods

  // No tag filters

  // Search filter
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    filteredMods = filteredMods.filter(
      (mod) =>
        mod.name.toLowerCase().includes(q) ||
        mod.description.toLowerCase().includes(q) ||
        mod.author.toLowerCase().includes(q) ||
        (mod.tags || []).some((t) => t.toLowerCase().includes(q)),
    );
  }

  // Sorting
  const toNullableTimestamp = (value?: string | null): number | null => {
    if (!value) return null;
    const time = Date.parse(value);
    return Number.isNaN(time) ? null : time;
  };
  const applyOrder = (val: number) => (sortOrder === "asc" ? -val : val);

  switch (sortBy) {
    case "Popular":
    case "Downloads":
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
      // Updated: prioritize mods that have an update available, then sort by updated_at
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

  // Event handlers

  const handleViewMod = (mod: Mod) => {
    setSelectedMod(mod);
  };

  return (
    <div className="h-full flex">
      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <SearchHeader
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          sortBy={sortBy}
          onSortChange={setSortBy}
          sortOrder={sortOrder}
          onSortOrderChange={setSortOrder}
        />

        {/* Content Area */}
        <style>{`.mods-grid {
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
        <div className="flex-1 overflow-auto p-6">
          {/* Results Info */}
          <div className="mb-6">
            <h2 className="text-2xl font-semibold mb-2">All Mods</h2>
            <p className="text-muted-foreground">
              {filteredMods.length} mod{filteredMods.length !== 1 ? "s" : ""}{" "}
              found
              {searchQuery && ` for "${searchQuery}"`}
            </p>
          </div>

          {/* Mods Grid/List */}
          {filteredMods.length === 0 ? (
            <div className="text-center py-12">
              <div className="text-muted-foreground mb-4">
                <svg
                  className="w-16 h-16 mx-auto mb-4 opacity-50"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.5}
                    d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
                  />
                </svg>
              </div>
              <h3 className="text-lg font-medium mb-2">No mods found</h3>
              <p className="text-muted-foreground">
                Try adjusting your search criteria or browse different
                categories.
              </p>
            </div>
          ) : (
            <div className={viewMode === "grid" ? "mods-grid" : "space-y-0"}>
              {filteredMods.map((mod) => (
                <ModCard
                  key={mod.id}
                  mod={mod}
                  viewMode={viewMode}
                  onInstall={onInstall}
                  onFavorite={onFavorite}
                  onView={handleViewMod}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Mod Details Modal */}
      <ModModal
        mod={selectedMod}
        isOpen={!!selectedMod}
        onClose={() => setSelectedMod(null)}
        onInstall={onInstall}
        onFavorite={onFavorite}
      />
    </div>
  );
}
