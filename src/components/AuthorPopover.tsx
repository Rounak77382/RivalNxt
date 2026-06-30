import React, { useState, useEffect } from "react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "./ui/popover";
import { Input } from "./ui/input";
import { Button } from "./ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";
import { Plus, Trash, Loader2 } from "lucide-react";
import { CustomAuthor, searchAuthors, assignModAuthor, clearModAuthor } from "../lib/api";
import { CreateAuthorDialog } from "./CreateAuthorDialog";

interface AuthorPopoverProps {
  children: React.ReactNode;
  modKey: string;
  currentAuthorName?: string | null;
  onSave: () => void;
}

export function AuthorPopover({ children, modKey, currentAuthorName, onSave }: AuthorPopoverProps) {
  const [open, setOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [results, setResults] = useState<CustomAuthor[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  // Debounced search
  useEffect(() => {
    if (!open) return;
    
    const timeout = setTimeout(async () => {
      setIsLoading(true);
      try {
        const res = await searchAuthors(searchQuery);
        setResults(res);
      } catch (err) {
        console.error("Failed to search authors:", err);
      } finally {
        setIsLoading(false);
      }
    }, 300);

    return () => clearTimeout(timeout);
  }, [searchQuery, open]);

  // Reset when closed
  useEffect(() => {
    if (!open) {
      setSearchQuery("");
      setResults([]);
    }
  }, [open]);

  const handleAssign = async (authorId: number) => {
    try {
      await assignModAuthor(modKey, authorId);
      onSave();
      setOpen(false);
    } catch (err) {
      console.error("Failed to assign author:", err);
    }
  };

  const handleClear = async () => {
    try {
      await clearModAuthor(modKey);
      onSave();
      setOpen(false);
    } catch (err) {
      console.error("Failed to clear author:", err);
    }
  };

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          {children}
        </PopoverTrigger>
        <PopoverContent className="w-80 p-0" align="start">
          <div className="p-3 border-b">
            <div className="relative">
              <Input
                placeholder="Search authors..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                autoFocus
              />
            </div>
          </div>
          
          <div
            className="scrollbar-hidden"
            style={{ maxHeight: "240px", overflowY: "auto" }}
            onWheel={(e) => e.stopPropagation()}
          >
            {isLoading ? (
              <div className="p-4 flex items-center justify-center text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
              </div>
            ) : results.length > 0 ? (
              <div className="p-1">
                {results.map((author) => (
                  <button
                    key={`${author.author_type}-${author.id || author.display_name}`}
                    className="w-full flex items-center gap-3 p-2 hover:bg-accent hover:text-accent-foreground rounded-sm text-left transition-colors"
                    onClick={async () => {
                      if (author.id) {
                        handleAssign(author.id);
                      } else {
                        // Nexus author from the mods table (no custom_authors row yet).
                        // Silently persist them then assign — no dialog needed.
                        try {
                          const { createOrUpdateAuthor } = await import("../lib/api");
                          const saved = await createOrUpdateAuthor({
                            display_name: author.display_name,
                            author_type: "nexus",
                            nexus_member_id: author.nexus_member_id ?? undefined,
                            avatar_base64: null,
                          });
                          handleAssign(saved.id);
                        } catch (err) {
                          console.error("Failed to persist Nexus author:", err);
                        }
                      }
                    }}
                  >
                    <Avatar className="w-8 h-8">
                      <AvatarImage 
                        src={
                          author.avatar_base64 || 
                          (author.nexus_member_id ? `https://avatars.nexusmods.com/${author.nexus_member_id}/100` : undefined)
                        } 
                        onError={(e: React.SyntheticEvent<HTMLImageElement>) => {
                          const img = e.currentTarget;
                          if (img.src.endsWith('/100') && author.nexus_member_id) {
                            img.src = `https://avatars.nexusmods.com/${author.nexus_member_id}/100.png`;
                          }
                        }}
                      />
                      <AvatarFallback className="text-[10px]">
                        {author.display_name.substring(0, 2).toUpperCase()}
                      </AvatarFallback>
                    </Avatar>
                    <div className="flex flex-col flex-1 truncate">
                      <span className="text-sm font-medium truncate">{author.display_name}</span>
                      <span className="text-xs text-muted-foreground">
                        {author.author_type === "nexus" ? "Nexus Author" : "Custom Author"}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="p-4 text-center text-sm text-muted-foreground">
                No authors found.
              </div>
            )}
          </div>
          
          <div className="p-2 border-t bg-muted/50 flex flex-col gap-2">
            <Button 
              variant="outline" 
              className="w-full justify-start text-muted-foreground" 
              onClick={() => {
                setCreateDialogOpen(true);
                setOpen(false);
              }}
            >
              <Plus className="w-4 h-4 mr-2" />
              Create Custom Author
            </Button>
            
            {currentAuthorName && (
              <Button 
                variant="ghost" 
                className="w-full justify-start text-destructive hover:text-destructive hover:bg-destructive/10"
                onClick={handleClear}
              >
                <Trash className="w-4 h-4 mr-2" />
                Clear Author Assignment
              </Button>
            )}
          </div>
        </PopoverContent>
      </Popover>

      <CreateAuthorDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        modKey={modKey}
        onSave={onSave}
        initialName={searchQuery}
      />
    </>
  );
}
