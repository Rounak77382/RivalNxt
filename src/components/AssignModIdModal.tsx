import React, { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import type { Mod } from "./ModCard";
import { AlertCircle, CheckCircle2, RefreshCw } from "lucide-react";
import { assignModId } from "../lib/api";

interface AssignModIdModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mod: Mod | null;
  onSuccess: (modId: string, nexusId: number) => void;
}

export function AssignModIdModal({ open, onOpenChange, mod, onSuccess }: AssignModIdModalProps) {
  const [nexusIdInput, setNexusIdInput] = useState("");
  const [status, setStatus] = useState<"idle" | "verifying" | "renamed" | "failed">("idle");
  const [errorMsg, setErrorMsg] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!mod || !nexusIdInput.trim()) return;

    const parsedId = parseInt(nexusIdInput.trim(), 10);
    if (isNaN(parsedId)) {
      setStatus("failed");
      setErrorMsg("Please enter a valid numeric Mod ID.");
      return;
    }

    setStatus("verifying");
    setErrorMsg("");

    try {
      const response = await assignModId({
        local_paths: mod.sourcePaths || [mod.id],
        nexus_mod_id: parsedId,
        game: "marvelrivals"
      });

      if (response.ok) {
        setStatus("renamed");
        onSuccess(mod.id, parsedId);
        setTimeout(() => {
          onOpenChange(false);
          setStatus("idle");
          setNexusIdInput("");
        }, 1500);
      } else {
        setStatus("failed");
        setErrorMsg(response.error || "Failed to assign Mod ID.");
      }
    } catch (err: any) {
      setStatus("failed");
      setErrorMsg(err.message || "An unexpected error occurred.");
    }
  };

  const handleClose = () => {
    if (status === "verifying") return;
    onOpenChange(false);
    setTimeout(() => {
      setStatus("idle");
      setNexusIdInput("");
      setErrorMsg("");
    }, 200);
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Assign Mod ID</DialogTitle>
          <DialogDescription>
            Enter the Nexus Mod ID for <strong>{mod?.name}</strong> to link it to the Nexus API and enable updates. 
            The ID can be found in the URL of the mod's Nexus page (e.g., .../mods/<strong>123</strong>).
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          <div className="space-y-2">
            <Input
              id="nexusId"
              placeholder="e.g. 123"
              value={nexusIdInput}
              onChange={(e) => setNexusIdInput(e.target.value)}
              disabled={status === "verifying" || status === "renamed"}
              autoFocus
            />
          </div>
          {status === "verifying" && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <RefreshCw className="w-4 h-4 animate-spin" />
              Verifying files with Nexus API...
            </div>
          )}
          {status === "renamed" && (
            <div className="flex items-center gap-2 text-sm text-emerald-500">
              <CheckCircle2 className="w-4 h-4" />
              Mod ID assigned and files renamed!
            </div>
          )}
          {status === "failed" && (
            <div className="flex items-start gap-2 text-sm text-destructive">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <p>{errorMsg}</p>
            </div>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={handleClose}
              disabled={status === "verifying"}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!nexusIdInput.trim() || status === "verifying" || status === "renamed"}
            >
              Verify & Assign
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
