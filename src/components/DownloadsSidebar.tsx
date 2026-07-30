import { Button } from "./ui/button";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ModConflictModal } from "./ModConflictModal";
import { CheckForUpdatesModal, type ModStatus } from "./CheckForUpdatesModal";
// import { mockConflicts } from "./mockConflicts";
import { Badge } from "./ui/badge";
import { Separator } from "./ui/separator";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "./ui/collapsible";

import {
  Users,
  Palette,
  Map as MapIcon,
  Settings,
  RefreshCw,
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  Heart,
  Tag,
} from "lucide-react";


import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";

import type { Mod } from "./ModCard";
import {
  listConflicts,
  refreshConflicts,
  type ApiConflict,
  getDownloadsSummary,
  type ApiDownloadsSummary,
  type TagLookupResponse,
  lookupTags,
} from "../lib/api";
import { toast } from "sonner";
import {
  deriveCategoryTags,
  extractNonCategoryTags,
} from "../lib/categoryUtils";
import { openInBrowser } from "../lib/tauri-utils";
import { getIconUrl } from "../lib/iconManager";
import { useTheme } from "./ThemeProvider";

interface DownloadsSidebarProps {
  selectedCategory: string;
  onCategoryChange: (category: string) => void;
  installedCounts: Record<string, number>;
  updatesCount: number;
  selectedCharacters?: string[];
  onCharacterToggle?: (character: string) => void;
  selectedCustomTags?: string[];
  onCustomTagToggle?: (tag: string) => void;
  mods: Mod[];
  conflictsReloadToken?: number;
  onRefreshMods?: () => void;
  onUpdateMod?: (modId: string, targetFileId?: number) => void;
}

const categories = [
  { id: "all", label: "All Installed", icon: CheckCircle },
  { id: "characters", label: "Characters", icon: Users },
  { id: "ui", label: "User Interface", icon: Palette },
  { id: "maps", label: "Maps & Environments", icon: MapIcon },
  { id: "audio", label: "Audio & Music", icon: Settings },
];

const calculateTotalSize = (mods: Mod[]): string => {
  // Sum up file sizes from all mod entries
  // Assuming each mod might have a size property or we estimate from version/download data
  // For now, return "Calculating..." if no data or sum available sizes
  let totalBytes = 0;
  for (const _mod of mods) {
    // If mod has a size property, use it; otherwise estimate
    // This can be extended when the Mod interface includes a size field
    // totalBytes += _mod.size || 0;
  }

  if (totalBytes === 0) {
    return "Calculating...";
  }

  if (totalBytes >= 1024 * 1024 * 1024) {
    return (totalBytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  } else if (totalBytes >= 1024 * 1024) {
    return (totalBytes / (1024 * 1024)).toFixed(1) + " MB";
  }
  return (totalBytes / 1024).toFixed(1) + " KB";
};

export function DownloadsSidebar({
  selectedCategory,
  onCategoryChange,
  installedCounts,
  updatesCount,
  selectedCharacters = [],
  onCharacterToggle,
  selectedCustomTags = [],
  onCustomTagToggle,
  mods,
  conflictsReloadToken = 0,
  onRefreshMods,
  onUpdateMod,
}: DownloadsSidebarProps) {
  const { theme } = useTheme();
  const isLightMode = theme === "light";
  const installedMods = useMemo(
    () => mods.filter((mod) => mod.isInstalled),
    [mods],
  );

  // Load character/skin map from database for proper tag identification
  const [tagLookupMap, setTagLookupMap] = useState<TagLookupResponse>({});
  const [_isLoadingTagMap, setIsLoadingTagMap] = useState(true);

  // Calculate a stable signature of all tags to prevent unnecessary re-fetches
  // when other mod properties (like download progress) change
  const tagsSignature = useMemo(() => {
    const allTags = new Set<string>();
    for (const mod of installedMods) {
      const tags = extractNonCategoryTags(mod.tags);
      tags.forEach((t) => allTags.add(t));
    }
    return Array.from(allTags).sort().join("|");
  }, [installedMods]);

  // Compute all custom tags used by installed mods
  const customTagsInMods = useMemo(() => {
    const ct = new Set<string>();
    for (const mod of installedMods) {
      const tags = extractNonCategoryTags(mod.tags);
      for (const tag of tags) {
        if (tag.toLowerCase() === "required" || tag.toLowerCase() === "optional") continue;
        const tagInfo = tagLookupMap[tag];
        // If it's neither a character nor a skin, it's a custom tag
        if (!tagInfo || (tagInfo.type !== "character" && tagInfo.type !== "skin")) {
          ct.add(tag);
        }
      }
    }
    return Array.from(ct).sort((a, b) => a.localeCompare(b));
  }, [installedMods, tagLookupMap]);

  const totalModsWithCustomTags = useMemo(() => {
    return installedMods.filter((mod) =>
      customTagsInMods.some((tag) =>
        mod.tags.some((t) => t.toLowerCase() === tag.toLowerCase()),
      ),
    ).length;
  }, [installedMods, customTagsInMods]);

  // Load all unique tags and lookup their types
  useEffect(() => {
    let cancelled = false;

    async function loadTagMap() {
      if (!tagsSignature) {
        if (!cancelled) {
          setTagLookupMap({});
          setIsLoadingTagMap(false);
        }
        return;
      }

      try {
        const tags = tagsSignature.split("|");
        // Lookup which tags are characters vs skins
        const lookup = await lookupTags(tags);
        if (!cancelled) {
          setTagLookupMap(lookup);
          setIsLoadingTagMap(false);
        }
      } catch (err) {
        console.error("Failed to load tag lookup map:", err);
        if (!cancelled) {
          setTagLookupMap({});
          setIsLoadingTagMap(false);
        }
      }
    }

    loadTagMap();

    return () => {
      cancelled = true;
    };
  }, [tagsSignature]);

  // Build hierarchical character-to-skins structure per category using database lookup
  const categoryCharacterHierarchy = useMemo(() => {
    const hierarchy: Record<string, Record<string, Set<string>>> = {};

    for (const mod of installedMods) {
      const categoriesForMod = deriveCategoryTags(mod.tags);
      if (categoriesForMod.length === 0) {
        continue;
      }

      const tags = extractNonCategoryTags(mod.tags);
      if (tags.length === 0) {
        continue;
      }

      // Separate tags into characters and skins using database lookup
      const characters: string[] = [];
      const skinsByParent: Map<string, string[]> = new Map();

      // First pass: Identify all explicit character tags in this mod
      for (const tag of tags) {
        const tagInfo = tagLookupMap[tag];
        if (tagInfo?.type === "character") {
          characters.push(tag);
        }
      }

      // Second pass: Process skins, using identified characters for context-aware disambiguation
      for (const tag of tags) {
        const tagInfo = tagLookupMap[tag];
        if (!tagInfo || tagInfo.type !== "skin") continue;

        // Resolve effective parents (handle both legacy 'parent' and new 'parents' list)
        let validParents: string[] = [];
        if (tagInfo.parents && tagInfo.parents.length > 0) {
          validParents = tagInfo.parents;
        } else if (tagInfo.parent) {
          validParents = [tagInfo.parent];
        }

        if (validParents.length > 0) {
          // Context-aware disambiguation:
          // If the current mod also tags any of the valid parents,
          // we assign the skin ONLY to those parents.
          const activeParentsInMod = validParents.filter((p) =>
            characters.includes(p),
          );

          // If we found specific parents in this mod, use them.
          // Otherwise, fallback to all valid parents (ambiguous case).
          const targetParents =
            activeParentsInMod.length > 0 ? activeParentsInMod : validParents;

          for (const parent of targetParents) {
            if (!skinsByParent.has(parent)) {
              skinsByParent.set(parent, []);
            }
            skinsByParent.get(parent)!.push(tag);
          }
        }
      }

      for (const categoryId of categoriesForMod) {
        if (!hierarchy[categoryId]) {
          hierarchy[categoryId] = {};
        }

        // Add each character found in this mod
        for (const character of characters) {
          if (!hierarchy[categoryId][character]) {
            hierarchy[categoryId][character] = new Set();
          }

          // Add skins that belong to this character
          const skins = skinsByParent.get(character) || [];
          for (const skin of skins) {
            hierarchy[categoryId][character].add(skin);
          }
        }

        // Also add characters that are parents of skins (even if not explicitly tagged)
        for (const [parentChar, skins] of skinsByParent.entries()) {
          if (!hierarchy[categoryId][parentChar]) {
            hierarchy[categoryId][parentChar] = new Set();
          }
          for (const skin of skins) {
            hierarchy[categoryId][parentChar].add(skin);
          }
        }
      }
    }

    return hierarchy;
  }, [installedMods, tagLookupMap]);

  // Extract sorted character names per category
  const sortedCharactersByCategory = useMemo(() => {
    const result: Record<string, string[]> = {};
    for (const [categoryId, characterMap] of Object.entries(
      categoryCharacterHierarchy,
    )) {
      result[categoryId] = Object.keys(characterMap).sort((a, b) =>
        a.localeCompare(b),
      );
    }
    return result;
  }, [categoryCharacterHierarchy]);
  const [conflictModalOpen, setConflictModalOpen] = useState(false);
  const [conflicts, setConflicts] = useState<ApiConflict[] | null>(null);
  const [downloadsSummary, setDownloadsSummary] =
    useState<ApiDownloadsSummary | null>(null);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingConflicts, setLoadingConflicts] = useState(false);
  const [conflictCount, setConflictCount] = useState<number>(0);
  const showActiveOnly = true;
  const [checkUpdatesModalOpen, setCheckUpdatesModalOpen] = useState(false);
  const [upiModalOpen, setUpiModalOpen] = useState(false);
  // Lifted update-check state so results persist when the modal is closed/reopened
  const [updateStatuses, setUpdateStatuses] = useState<Record<string, ModStatus>>({});
  const [updateChecked, setUpdateChecked] = useState(false);
  const [updateIsCheckingAll, setUpdateIsCheckingAll] = useState(false);

  // Track which characters are expanded to show their skins (default: all collapsed)
  const [expandedCharacters, setExpandedCharacters] = useState<Set<string>>(
    new Set(),
  );

  const toggleCharacterExpand = useCallback((character: string) => {
    setExpandedCharacters((prev) => {
      const next = new Set(prev);
      if (next.has(character)) {
        next.delete(character);
      } else {
        next.add(character);
      }
      return next;
    });
  }, []);

  const uniqueModIds = useMemo(() => {
    const ids = new Set<number>();
    for (const mod of installedMods) {
      if (typeof mod.backendModId === "number" && mod.backendModId > 0) {
        ids.add(mod.backendModId);
      }
    }
    return Array.from(ids);
  }, [installedMods]);


  const fetchConflicts = useCallback(async () => {
    let cancelled = false;
    try {
      setLoadingConflicts(true);
      try {
        await refreshConflicts();
      } catch (err) {
        if (!cancelled) {
          // toast.error(err instanceof Error ? err.message : "Failed to refresh conflicts"); // optional: suppress noise
        }
      }
      const data = await listConflicts(50, showActiveOnly);
      if (!cancelled) {
        setConflicts(data);
        // Also update the sidebar badge count
        setConflictCount(Array.isArray(data) ? data.length : 0);
      }
    } catch (err) {
      if (!cancelled) {
        const message =
          err instanceof Error ? err.message : "Failed to load conflicts";
        toast.error(message);
        setConflicts([]);
      }
    } finally {
      if (!cancelled) setLoadingConflicts(false);
    }
  }, [showActiveOnly]);

  useEffect(() => {
    if (!conflictModalOpen) return;
    fetchConflicts();
  }, [conflictModalOpen, conflictsReloadToken, fetchConflicts]);

  // Lightweight effect to keep a conflict count for the sidebar button
  useEffect(() => {
    let cancelled = false;
    async function loadCount() {
      try {
        const data = await listConflicts(200, showActiveOnly);
        if (!cancelled) setConflictCount(Array.isArray(data) ? data.length : 0);
      } catch (err) {
        if (!cancelled) setConflictCount(0);
      }
    }
    loadCount();
    return () => {
      cancelled = true;
    };
  }, [mods, conflictsReloadToken]);

  useEffect(() => {
    let cancelled = false;
    async function loadSummary() {
      setLoadingSummary(true);
      try {
        const s = await getDownloadsSummary();
        if (!cancelled) setDownloadsSummary(s);
      } catch (err) {
        console.error("Failed to load downloads summary", err);
        if (!cancelled) setDownloadsSummary(null);
      } finally {
        if (!cancelled) setLoadingSummary(false);
      }
    }
    loadSummary();
    return () => {
      cancelled = true;
    };
  }, [mods, conflictsReloadToken]);

  const formattedConflicts = (conflicts || []).map((mc) => ({
    asset_path: mc.asset_path,
    category: mc.category,
    conflicting_mod_count: mc.conflicting_mod_count,
    total_paks: mc.total_paks,
    participants: mc.participants,
    detected_at: mc.detected_at,
  }));

  const handleDonateClick = (platform: "kofi" | "upi") => {
    if (platform === "kofi") {
      // Open Ko-fi in browser
      openInBrowser("https://ko-fi.com/rsted");
    } else if (platform === "upi") {
      // Show UPI modal
      setUpiModalOpen(true);
    }
  };

  return (
    <div
      className="bg-card border-r border-border h-full flex flex-col overflow-y-auto sidebar-hide-scrollbar"
      style={{
        width: "18rem",
        minWidth: "18rem",
        maxWidth: "18rem",
        flex: "0 0 18rem",
        scrollbarWidth: "none", // Firefox
        msOverflowStyle: "none", // IE 10+
        overflowY: "auto",
      }}
    >
      {/* Hide scrollbar for Chrome, Safari and Opera */}
      <style>{`
        .sidebar-hide-scrollbar::-webkit-scrollbar {
          display: none;
        }
        .donate-button {
          height: 32px;
          padding: 5px;
        }
      `}</style>
      <div className="p-4">
        <div className="space-y-1">
          {categories.map((category) => {
            const Icon = category.icon;
            const count = installedCounts[category.id] || 0;
            const characterHierarchy =
              categoryCharacterHierarchy[category.id] || {};
            const charactersForCategory =
              sortedCharactersByCategory[category.id] || [];

            // If any selected category (except 'all') is clicked again, go to 'all'.
            // If 'all' is clicked again, collapse (deselect).
            const handleCategoryClick = () => {
              if (selectedCategory === category.id) {
                if (category.id === "all") {
                  onCategoryChange("");
                } else {
                  onCategoryChange("all");
                }
              } else {
                onCategoryChange(category.id);
              }
            };

            return (
              <div key={category.id}>
                <Button
                  variant={
                    selectedCategory === category.id ? "secondary" : "ghost"
                  }
                  className="w-full justify-start gap-3 h-10 min-w-0"
                  onClick={handleCategoryClick}
                  disabled={false}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span className="flex-1 truncate text-left">
                    {category.label}
                  </span>
                  {count > 0 && (
                    <Badge variant="secondary" className="shrink-0 text-xs">
                      {count}
                    </Badge>
                  )}
                </Button>

                {/* Character Subcategories Dropdown for all except 'all' */}
                {category.id !== "all" &&
                  selectedCategory === category.id &&
                  onCharacterToggle &&
                  count > 0 && (
                    <Collapsible defaultOpen className="mt-2 ml-6">
                      <CollapsibleTrigger asChild>
                        <Button
                          variant="ghost"
                          className="w-full justify-start gap-2 h-8 text-sm"
                        >
                          <ChevronDown className="w-3 h-3" />
                          Filter by Character
                          {selectedCharacters.length > 0 && (
                            <Badge
                              variant="secondary"
                              className="text-xs ml-auto"
                            >
                              {selectedCharacters.length}
                            </Badge>
                          )}
                        </Button>
                      </CollapsibleTrigger>
                      <CollapsibleContent className="space-y-1 mt-2">
                        {charactersForCategory.map((character) => {
                          const skins = Array.from(
                            characterHierarchy[character] || new Set(),
                          ).sort((a, b) => a.localeCompare(b));

                          // Count mods that have this character AND belong to the current category
                          const modCount = installedMods.filter((mod) => {
                            const tags = extractNonCategoryTags(mod.tags);
                            const hasCharacter = tags.includes(character);
                            if (!hasCharacter) return false;

                            const modCategories = deriveCategoryTags(mod.tags);
                            return modCategories.includes(category.id);
                          }).length;

                          return (
                            <div key={character} className="flex flex-col mb-0.5 w-full">
                              <Button
                                variant={selectedCharacters.includes(character) ? "secondary" : "ghost"}
                                className={`w-full justify-start h-8 px-2 transition-colors ${
                                  selectedCharacters.includes(character) ? "font-medium text-foreground" : "font-normal text-muted-foreground hover:text-foreground"
                                }`}
                                onClick={() => {
                                  const isCharacterSelected = selectedCharacters.includes(character);

                                  if (isCharacterSelected) {
                                    // Deselect character and all skins
                                    onCharacterToggle(character);
                                    skins.forEach((skin) => {
                                      if (selectedCharacters.includes(skin)) {
                                        onCharacterToggle(skin);
                                      }
                                    });
                                  } else {
                                    // Select character ONLY - do NOT auto-select all skins
                                    onCharacterToggle(character);
                                  }
                                }}
                              >
                                <span className="truncate flex-1 text-left text-sm font-semibold">{character.toLowerCase()}</span>
                                {modCount > 0 && (
                                  <Badge variant="secondary" className="shrink-0 text-xs">
                                    {modCount}
                                  </Badge>
                                )}
                              </Button>

                              {/* Skin sub-items as a Collapsible */}
                              {skins.length > 0 && selectedCharacters.includes(character) && (
                                <Collapsible 
                                  open={expandedCharacters.has(character)}
                                  onOpenChange={() => toggleCharacterExpand(character)}
                                  className="mt-1 ml-6 mb-1"
                                >
                                  <CollapsibleTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      className="w-full justify-start gap-2 h-7 text-xs text-muted-foreground"
                                    >
                                      <ChevronDown 
                                        className={`w-3 h-3 transition-transform ${
                                          expandedCharacters.has(character) ? "" : "-rotate-90"
                                        }`} 
                                      />
                                      Filter by Skins
                                    </Button>
                                  </CollapsibleTrigger>
                                  
                                  <CollapsibleContent className="space-y-1 mt-1 border-l border-border/30 pl-1.5 ml-1 flex flex-col">
                                    {skins.map((skin) => {
                                      const isSkinSelected = selectedCharacters.includes(character) && selectedCharacters.includes(skin);

                                      return (
                                        <Button
                                          key={`${character}-${skin}`}
                                          variant={isSkinSelected ? "secondary" : "ghost"}
                                          style={{ marginLeft: '4px' }}
                                          className={`w-full justify-start h-7 px-2 text-xs transition-colors ${
                                            isSkinSelected ? "font-medium text-foreground" : "font-normal text-muted-foreground hover:text-foreground"
                                          }`}
                                          onClick={() => {
                                            if (isSkinSelected) {
                                              onCharacterToggle(skin);
                                            } else {
                                              if (!selectedCharacters.includes(character)) {
                                                onCharacterToggle(character);
                                              }
                                              onCharacterToggle(skin);
                                            }
                                          }}
                                        >
                                          <span className="truncate">{skin.toLowerCase()}</span>
                                        </Button>
                                      );
                                    })}
                                  </CollapsibleContent>
                                </Collapsible>
                              )}
                            </div>
                          );
                        })}
                        {selectedCharacters.length > 0 && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="w-full text-xs mt-2"
                            onClick={() => {
                              selectedCharacters.forEach((char) =>
                                onCharacterToggle(char),
                              );
                            }}
                          >
                            Clear Selection
                          </Button>
                        )}
                      </CollapsibleContent>
                    </Collapsible>
                  )}
              </div>
            );
          })}

          {/* Custom Tags Section */}
          {customTagsInMods.length > 0 && onCustomTagToggle && (
            <div>
              <Collapsible>
                <CollapsibleTrigger asChild>
                  <Button
                    variant={selectedCustomTags.length > 0 ? "secondary" : "ghost"}
                    className="w-full justify-start gap-3 h-10 min-w-0"
                  >
                    <Tag className="h-4 w-4 shrink-0" />
                    <span className="flex-1 truncate text-left">
                      Custom Tags
                    </span>
                    {totalModsWithCustomTags > 0 && (
                      <Badge variant="secondary" className="shrink-0 text-xs">
                        {totalModsWithCustomTags}
                      </Badge>
                    )}
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent className="space-y-1 mt-2 ml-6">
                  {customTagsInMods.map((tag) => {
                    const isSelected = selectedCustomTags.includes(tag);
                    const modCount = installedMods.filter((mod) =>
                      mod.tags.some((t) => t.toLowerCase() === tag.toLowerCase()),
                    ).length;

                    return (
                      <Button
                        key={tag}
                        variant={isSelected ? "secondary" : "ghost"}
                        className={`w-full justify-start h-8 px-2 text-sm transition-colors ${
                          isSelected
                            ? "font-medium text-foreground"
                            : "font-normal text-muted-foreground hover:text-foreground"
                        }`}
                        onClick={() => onCustomTagToggle(tag)}
                      >
                        <span className="truncate flex-1 text-left">{tag.toLowerCase()}</span>
                        {modCount > 0 && (
                          <Badge variant="secondary" className="shrink-0 text-xs opacity-70">
                            {modCount}
                          </Badge>
                        )}
                      </Button>
                    );
                  })}
                  {selectedCustomTags.length > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full text-xs mt-2"
                      onClick={() => {
                        selectedCustomTags.forEach((tag) =>
                          onCustomTagToggle(tag),
                        );
                      }}
                    >
                      Clear Selection
                    </Button>
                  )}
                </CollapsibleContent>
              </Collapsible>
            </div>
          )}
        </div>
      </div>

      <Separator />

      <div className="p-6">
        <h3 className="font-medium mb-3">Quick Actions</h3>
        <div className="space-y-2">
          <Button
            variant="outline"
            className="w-full justify-start gap-3"
            disabled={uniqueModIds.length === 0}
            onClick={() => setCheckUpdatesModalOpen(true)}
          >
            <RefreshCw className="w-4 h-4" />
            <span className="flex-1 text-left">Check for Updates</span>
            {updatesCount > 0 && (
              <Badge variant="destructive" className="text-xs">
                {updatesCount}
              </Badge>
            )}
          </Button>
          {/* CheckForUpdatesModal */}
          <CheckForUpdatesModal
            open={checkUpdatesModalOpen}
            onOpenChange={setCheckUpdatesModalOpen}
            mods={mods}
            onUpdateMod={onUpdateMod}
            onRefreshMods={onRefreshMods}
            statuses={updateStatuses}
            onStatusesChange={setUpdateStatuses}
            checked={updateChecked}
            onCheckedChange={setUpdateChecked}
            isCheckingAll={updateIsCheckingAll}
            onIsCheckingAllChange={setUpdateIsCheckingAll}
          />

          <Button
            variant="outline"
            className="w-full justify-start gap-3"
            onClick={() => setConflictModalOpen(true)}
          >
            <AlertTriangle className="w-4 h-4" />
            <span className="flex-1 text-left">Check for Conflicts</span>
            {conflictCount > 0 && (
              <Badge variant="destructive" className="text-xs">
                {conflictCount}
              </Badge>
            )}
          </Button>
          {/* Mod Conflict Modal */}
          <ModConflictModal
            open={conflictModalOpen}
            onOpenChange={setConflictModalOpen}
            conflicts={formattedConflicts}
            title={
              showActiveOnly ? "Active Mod Conflicts" : "All Mod Conflicts"
            }
            onConflictStateChanged={fetchConflicts}
            onRefreshMods={onRefreshMods}
            mods={mods}
          />
          {conflictModalOpen && (
            <div className="mt-2 text-xs text-muted-foreground">
              {loadingConflicts ? "Loading conflicts…" : ""}
            </div>
          )}
        </div>


      </div>

      <Separator />

      <div className="flex-1 p-6">
        <h3 className="font-medium mb-3">Installation Info</h3>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Total Installed:</span>
            <span>{installedCounts.all || 0}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Needs Updates:</span>
            <span className={updatesCount > 0 ? "text-destructive" : ""}>
              {updatesCount}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Total Size:</span>
            <span>
              {loadingSummary
                ? "Calculating..."
                : downloadsSummary
                  ? downloadsSummary.total_size_human
                  : calculateTotalSize(installedMods)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Last Check:</span>
            <span>
              {loadingSummary
                ? "..."
                : downloadsSummary && downloadsSummary.last_check
                  ? (() => {
                      try {
                        const d = new Date(
                          downloadsSummary.last_check as string,
                        );
                        const now = Date.now();
                        const diffMs = now - d.getTime();
                        const diffMins = Math.floor(diffMs / (1000 * 60));
                        if (diffMins < 1) return "Just now";
                        if (diffMins < 60)
                          return `${diffMins} min${
                            diffMins !== 1 ? "s" : ""
                          } ago`;
                        const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
                        if (diffHours < 24)
                          return `${diffHours} hour${
                            diffHours !== 1 ? "s" : ""
                          } ago`;
                        const diffDays = Math.floor(
                          diffMs / (1000 * 60 * 60 * 24),
                        );
                        if (diffDays < 30)
                          return `${diffDays} day${
                            diffDays !== 1 ? "s" : ""
                          } ago`;
                        return d.toLocaleDateString();
                      } catch (e) {
                        return String(downloadsSummary.last_check);
                      }
                    })()
                  : "Never"}
            </span>
          </div>
        </div>
      </div>

      <Separator />

      <div style={{ padding: "10px 10px 15px" }}>
        <h4 className="font-medium mb-4 flex justify-center items-center gap-2">
          <Heart
            className="w-4 h-4 text-red-500"
            style={{ paddingTop: "2px" }}
          />
          Support Development
        </h4>
        <div className="flex gap-4 justify-center">
          <Button
            className="donate-button"
            style={{ width: "80px" }}
            variant="outline"
            size="sm"
            onClick={() => handleDonateClick("kofi")}
          >
            <img
              src={getIconUrl("kofi.svg")}
              alt="Ko-fi"
              style={{
                width: "40px",
                height: "15px",
                filter: isLightMode ? "invert(1)" : "none",
              }}
            />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="donate-button"
            style={{ width: "80px" }}
            onClick={() => handleDonateClick("upi")}
          >
            <img
              src={getIconUrl("upi.svg")}
              alt="UPI"
              style={{
                width: "40px",
                height: "12px",
                filter: isLightMode ? "invert(1)" : "none",
              }}
            />
          </Button>
        </div>
      </div>

      {/* UPI Donation Modal */}
      <Dialog open={upiModalOpen} onOpenChange={setUpiModalOpen}>
        <DialogContent className="sm:max-w-xs max-w-[280px]">
          <DialogHeader>
            <DialogTitle className="text-base">UPI Donation</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col items-center space-y-3 py-2">
            <img
              src={getIconUrl("qr.png")}
              alt="UPI QR Code"
              className="object-contain"
              style={{ width: "300px" }}
            />
            <div className="text-center">
              <p className="text-xs text-muted-foreground mb-1">UPI ID:</p>
              <p className="font-mono text-sm font-semibold">
                rounaks255@oksbi
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Scan with UPI app
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
