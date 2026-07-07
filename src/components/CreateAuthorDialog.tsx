import React, { useState, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "./ui/dialog";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";
import { createOrUpdateAuthor, assignModAuthor } from "../lib/api";
import { Loader2, Upload, X } from "lucide-react";

interface CreateAuthorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  modKey: string;
  onSave: () => void;
  initialName?: string;
}

export function CreateAuthorDialog({
  open,
  onOpenChange,
  modKey,
  onSave,
  initialName = "",
}: CreateAuthorDialogProps) {
  const [name, setName] = useState(initialName);
  const [avatarBase64, setAvatarBase64] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Reset state when opened
  React.useEffect(() => {
    if (open) {
      setName(initialName);
      setAvatarBase64(null);
      setIsSaving(false);
    }
  }, [open, initialName]);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const img = new Image();
      img.onload = () => {
        // Resize image to max 200x200 for avatar
        const canvas = document.createElement("canvas");
        const MAX_SIZE = 200;
        let width = img.width;
        let height = img.height;

        if (width > height) {
          if (width > MAX_SIZE) {
            height *= MAX_SIZE / width;
            width = MAX_SIZE;
          }
        } else {
          if (height > MAX_SIZE) {
            width *= MAX_SIZE / height;
            height = MAX_SIZE;
          }
        }

        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.drawImage(img, 0, 0, width, height);
          const dataUrl = canvas.toDataURL("image/webp", 0.8);
          setAvatarBase64(dataUrl);
        }
      };
      if (typeof event.target?.result === "string") {
        img.src = event.target.result;
      }
    };
    reader.readAsDataURL(file);
    // Clear input so same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleSave = async () => {
    if (!name.trim()) return;
    setIsSaving(true);
    try {
      const author = await createOrUpdateAuthor({
        display_name: name.trim(),
        author_type: "custom",
        avatar_base64: avatarBase64,
      });
      await assignModAuthor(modKey, author.id);
      onSave();
      onOpenChange(false);
    } catch (err) {
      console.error("Failed to create custom author:", err);
      // You could add toast here
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[400px]">
        <DialogHeader>
          <DialogTitle>Create Custom Author</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="flex flex-col items-center gap-4">
            <div className="relative group">
              <Avatar className="w-24 h-24 border">
                <AvatarImage src={avatarBase64 || undefined} />
                <AvatarFallback className="text-2xl">
                  {name.trim() ? name.trim().substring(0, 2).toUpperCase() : "?"}
                </AvatarFallback>
              </Avatar>
              <div 
                className="absolute inset-0 bg-black/50 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload className="w-6 h-6 text-white" />
              </div>
              {avatarBase64 && (
                <button
                  className="absolute top-0 right-0 bg-destructive text-destructive-foreground rounded-full p-1 shadow-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setAvatarBase64(null);
                  }}
                  title="Remove avatar"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
            <input
              type="file"
              accept="image/*"
              className="hidden"
              ref={fileInputRef}
              onChange={handleFileChange}
            />
            <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
              {avatarBase64 ? "Change Avatar" : "Upload Avatar"}
            </Button>
          </div>
          
          <div className="grid gap-2">
            <Label htmlFor="author-name">Author Name</Label>
            <Input
              id="author-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. My Custom Author"
              autoFocus
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSaving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!name.trim() || isSaving}>
            {isSaving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            Save Author
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
