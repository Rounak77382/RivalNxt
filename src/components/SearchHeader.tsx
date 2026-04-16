import { Button } from "./ui/button";
import { AddModModal } from "./AddModModal";
import { useState, useEffect } from "react";
import { Input } from "./ui/input";
import {
  Search,
  SlidersHorizontal,
  Grid3X3,
  List,
  Plus,
  ArrowUpDown,
  Moon,
  Sun,
  X,
  ChevronDown,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from "./ui/dropdown-menu";
import { useTheme } from "./ThemeProvider";
import { useNsfwFilter } from "./NSFWFilterProvider";

interface SearchHeaderProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  viewMode: "grid" | "list";
  onViewModeChange: (mode: "grid" | "list") => void;
  sortBy: string;
  onSortChange: (sort: string) => void;
  /** optional controlled sort order: 'asc' or 'desc' */
  sortOrder?: "asc" | "desc";
  /** optional callback when sort order changes */
  onSortOrderChange?: (order: "asc" | "desc") => void;
  onModAdded?: () => Promise<void> | void;
}

export function SearchHeader({
  searchQuery,
  onSearchChange,
  viewMode,
  onViewModeChange,
  sortBy,
  onSortChange,
  sortOrder,
  onSortOrderChange,
  onModAdded,
}: SearchHeaderProps) {
  const { theme, toggleTheme } = useTheme();
  const { nsfwBlurEnabled, toggleNsfwBlur } = useNsfwFilter();
  const isDark = theme === "dark";
  const [addModOpen, setAddModOpen] = useState(false);

  // Sort options for the dropdown
  const sortOptions = [
    "Name",
    "Recent",
    "Uploaded",
    "Updated",
    "Popular",
    "Rating",
    "Favourites",
  ];

  // Manage ascending/descending sort order (controlled if prop provided)
  const [orderState, setOrderState] = useState<"asc" | "desc">(
    sortOrder ?? "desc",
  );

  useEffect(() => {
    if (sortOrder) setOrderState(sortOrder);
  }, [sortOrder]);

  const toggleOrder = () => {
    const next = orderState === "asc" ? "desc" : "asc";
    setOrderState(next);
    if (onSortOrderChange) onSortOrderChange(next);
  };

  return (
    <div className="bg-card border-b border-border p-4">
      <div className="flex items-center gap-4">
        {/* Search Bar */}
        <div className="flex-1 relative max-w-md">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            type="text"
            placeholder="Search mods..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="pl-10 pr-8"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => onSearchChange("")}
              className="absolute right-2 top-1/2 transform -translate-y-1/2 p-1 rounded-full hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Clear search"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Sort Dropdown */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="gap-2">
              <SlidersHorizontal className="w-4 h-4" />
              Sort: {sortBy}
              <ChevronDown className="w-4 h-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuRadioGroup value={sortBy} onValueChange={onSortChange}>
              {sortOptions.map((option) => (
                <DropdownMenuRadioItem key={option} value={option}>
                  {option}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Sort order toggle (asc/desc) placed to the right of Sort button */}
        <Button
          variant="outline"
          size="sm"
          className="gap-2"
          onClick={toggleOrder}
          title={`Toggle sort order (currently ${orderState})`}
          aria-label={`Toggle sort order (currently ${orderState})`}
        >
          <ArrowUpDown
            className={`w-4 h-4 transition-transform ${
              orderState === "asc" ? "rotate-180" : ""
            }`}
          />
        </Button>

        {/* View Mode Toggle */}
        <div className="flex border border-border rounded-lg">
          <Button
            variant={viewMode === "grid" ? "secondary" : "ghost"}
            size="sm"
            onClick={() => onViewModeChange("grid")}
            className="rounded-r-none"
          >
            <Grid3X3 className="w-4 h-4" />
          </Button>
          <Button
            variant={viewMode === "list" ? "secondary" : "ghost"}
            size="sm"
            onClick={() => onViewModeChange("list")}
            className="rounded-l-none border-l"
          >
            <List className="w-4 h-4" />
          </Button>
        </div>

        {/* Add Mods Button */}
        <Button
          variant="outline"
          size="sm"
          className="gap-2"
          onClick={() => setAddModOpen(true)}
        >
          <Plus className="w-4 h-4" />
          Add Mods
        </Button>

        <Button variant="outline" size="sm" onClick={toggleTheme}>
          {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={toggleNsfwBlur}
          title={
            nsfwBlurEnabled
              ? "NSFW content is blurred (click to show)"
              : "NSFW content is visible (click to blur)"
          }
          className="relative px-2"
          style={!nsfwBlurEnabled ? { border: "2px solid #ef4444" } : undefined}
        >
          <img
            src="/icons/18-plus.svg"
            alt="18+"
            className="w-4 h-4"
            style={{ filter: "brightness(0) invert(1)" }}
          />
          {nsfwBlurEnabled && (
            <span className="absolute inset-0 flex items-center justify-center">
              <span
                className="w-[70%] h-0.5 rotate-[-20deg] rounded-full"
                style={{ backgroundColor: "#ef4444" }}
              />
            </span>
          )}
        </Button>
      </div>
      {/* Add Mods Modal */}
      <AddModModal
        open={addModOpen}
        onOpenChange={setAddModOpen}
        onSuccess={onModAdded}
      />
    </div>
  );
}
