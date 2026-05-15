import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Button } from "./ui/button";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Sparkles,
  ShieldCheck,
  User,
} from "lucide-react";

export type GameUpdateStep = {
  key: string;
  label: string;
  status: "pending" | "running" | "succeeded" | "failed";
  error?: string | null;
};

export type GameUpdatePhase =
  | "checking"
  | "uptodate"
  | "updating"
  | "done";

interface GameUpdateModalProps {
  open: boolean;
  phase: GameUpdatePhase;
  steps: GameUpdateStep[];
  latestFile: string | null;
  newCharacters?: string[];
  newSkins?: string[];
  onDismiss: () => void;
}

export function GameUpdateModal({
  open,
  phase,
  steps,
  latestFile,
  newCharacters = [],
  newSkins = [],
  onDismiss,
}: GameUpdateModalProps) {
  const allDone =
    phase === "done" ||
    (phase === "updating" &&
      steps.length > 0 &&
      steps.every((s) => s.status === "succeeded" || s.status === "failed"));
  const anyFailed = steps.some((s) => s.status === "failed");
  const anyRunning =
    phase === "checking" || steps.some((s) => s.status === "running");
  const canDismiss = phase === "uptodate" || allDone;

  // Animated dots for running state
  const [dots, setDots] = useState("");
  useEffect(() => {
    if (!anyRunning) {
      setDots("");
      return;
    }
    const interval = setInterval(() => {
      setDots((prev) => (prev.length >= 3 ? "" : prev + "."));
    }, 500);
    return () => clearInterval(interval);
  }, [anyRunning]);

  // Auto-close when up to date
  useEffect(() => {
    if (phase !== "uptodate") return;
    const timer = setTimeout(() => {
      onDismiss();
    }, 2000);
    return () => clearTimeout(timer);
  }, [phase, onDismiss]);

  // Determine accent color
  const accentGradient =
    phase === "uptodate"
      ? "linear-gradient(90deg, #22c55e, #10b981)"
      : phase === "checking"
        ? "linear-gradient(90deg, #6366f1, #8b5cf6, #a855f7)"
        : anyFailed
          ? "linear-gradient(90deg, #ef4444, #f97316)"
          : allDone
            ? "linear-gradient(90deg, #22c55e, #10b981)"
            : "linear-gradient(90deg, #6366f1, #8b5cf6, #a855f7)";

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen && !canDismiss) return;
        if (!isOpen) onDismiss();
      }}
    >
      <DialogContent
        className="max-w-md"
        onPointerDownOutside={(e) => {
          if (!canDismiss) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (!canDismiss) e.preventDefault();
        }}
        style={{
          border: "1px solid hsl(var(--border))",
          borderRadius: "16px",
          overflow: "hidden",
        }}
      >
        {/* Gradient accent bar */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "4px",
            background: accentGradient,
            transition: "background 0.5s ease",
          }}
        />

        <DialogHeader className="pt-2">
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <div
              style={{
                width: "44px",
                height: "44px",
                borderRadius: "12px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background:
                  phase === "uptodate"
                    ? "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(16,185,129,0.15))"
                    : phase === "checking"
                      ? "linear-gradient(135deg, rgba(99,102,241,0.15), rgba(168,85,247,0.15))"
                      : allDone
                        ? anyFailed
                          ? "linear-gradient(135deg, rgba(239,68,68,0.15), rgba(249,115,22,0.15))"
                          : "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(16,185,129,0.15))"
                        : "linear-gradient(135deg, rgba(99,102,241,0.15), rgba(168,85,247,0.15))",
                transition: "background 0.5s ease",
                flexShrink: 0,
              }}
            >
              {phase === "checking" ? (
                <Loader2 className="h-6 w-6 text-violet-400 animate-spin" />
              ) : phase === "uptodate" ? (
                <ShieldCheck className="h-6 w-6 text-emerald-400" />
              ) : allDone ? (
                anyFailed ? (
                  <XCircle className="h-6 w-6 text-red-400" />
                ) : (
                  <Sparkles className="h-6 w-6 text-emerald-400" />
                )
              ) : (
                <RefreshCw
                  className="h-6 w-6 text-violet-400"
                  style={{
                    animation: anyRunning
                      ? "spin 2s linear infinite"
                      : "none",
                  }}
                />
              )}
            </div>
            <div>
              <DialogTitle
                className="text-lg font-semibold"
                style={{ lineHeight: "1.3" }}
              >
                {phase === "checking"
                  ? "Checking for Game Update"
                  : phase === "uptodate"
                    ? "Game is Up to Date"
                    : allDone
                      ? anyFailed
                        ? "Update Rebuild Failed"
                        : "Update Rebuild Complete"
                      : "Game Update Detected"}
              </DialogTitle>
              <p
                className="text-sm text-muted-foreground"
                style={{ marginTop: "2px" }}
              >
                {phase === "checking"
                  ? `Scanning game files${dots}`
                  : phase === "uptodate"
                    ? "No changes detected. Closing automatically..."
                    : allDone
                      ? anyFailed
                        ? "Some steps failed. You can retry from Settings."
                        : "Character data and tags are now up to date!"
                      : `Marvel Rivals files have changed. Refreshing data${dots}`}
              </p>
            </div>
          </div>
        </DialogHeader>

        {/* Steps (only show during updating/done) */}
        {(phase === "updating" || (phase === "done" && steps.length > 0)) && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "12px",
              padding: "8px 0",
            }}
          >
            {steps.map((step, idx) => (
              <div
                key={step.key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                  padding: "12px 16px",
                  borderRadius: "10px",
                  background:
                    step.status === "running"
                      ? "hsl(var(--accent) / 0.5)"
                      : step.status === "succeeded"
                        ? "hsl(var(--accent) / 0.3)"
                        : step.status === "failed"
                          ? "rgba(239,68,68,0.08)"
                          : "hsl(var(--accent) / 0.15)",
                  transition: "background 0.3s ease",
                }}
              >
                {/* Step icon */}
                <div
                  style={{
                    width: "32px",
                    height: "32px",
                    borderRadius: "8px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                    background:
                      step.status === "succeeded"
                        ? "rgba(34,197,94,0.2)"
                        : step.status === "failed"
                          ? "rgba(239,68,68,0.2)"
                          : step.status === "running"
                            ? "rgba(99,102,241,0.2)"
                            : "hsl(var(--muted))",
                    transition: "background 0.3s ease",
                  }}
                >
                  {step.status === "running" ? (
                    <Loader2 className="h-4 w-4 text-violet-400 animate-spin" />
                  ) : step.status === "succeeded" ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                  ) : step.status === "failed" ? (
                    <XCircle className="h-4 w-4 text-red-400" />
                  ) : (
                    <span className="text-xs font-medium text-muted-foreground">
                      {idx + 1}
                    </span>
                  )}
                </div>

                {/* Label + error */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <p
                    className={`text-sm font-medium ${
                      step.status === "running"
                        ? "text-foreground"
                        : step.status === "succeeded"
                          ? "text-emerald-400"
                          : step.status === "failed"
                            ? "text-red-400"
                            : "text-muted-foreground"
                    }`}
                    style={{ transition: "color 0.3s ease" }}
                  >
                    {step.label}
                  </p>
                  {step.status === "failed" && step.error && (
                    <p
                      className="text-xs text-red-400/80"
                      style={{
                        marginTop: "2px",
                        wordBreak: "break-word",
                      }}
                    >
                      {step.error}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* New Content Section */}
        {allDone && !anyFailed && (newCharacters.length > 0 || newSkins.length > 0) && (
          <div
            style={{
              marginTop: "4px",
              padding: "16px",
              borderRadius: "12px",
              background: "linear-gradient(135deg, rgba(139,92,246,0.1), rgba(168,85,247,0.1))",
              border: "1px solid rgba(139,92,246,0.2)",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Sparkles className="h-4 w-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-violet-300">What's New</h3>
            </div>
            
            <div 
              style={{ 
                maxHeight: "150px", 
                overflowY: "auto",
                display: "flex",
                flexDirection: "column",
                gap: "8px",
                paddingRight: "4px"
              }}
              className="custom-scrollbar"
            >
              {newCharacters.map(char => (
                <div key={char} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <div style={{ 
                    width: "20px", height: "20px", borderRadius: "50%", 
                    background: "rgba(139,92,246,0.2)", display: "flex", 
                    alignItems: "center", justifyContent: "center" 
                  }}>
                    <User className="h-3 w-3 text-violet-400" />
                  </div>
                  <span className="text-xs font-medium text-foreground">New Character: <span className="text-violet-200">{char}</span></span>
                </div>
              ))}
              
              {newSkins.map(skin => (
                <div key={skin} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <div style={{ 
                    width: "20px", height: "20px", borderRadius: "50%", 
                    background: "rgba(16,185,129,0.2)", display: "flex", 
                    alignItems: "center", justifyContent: "center" 
                  }}>
                    <Sparkles className="h-3 w-3 text-emerald-400" />
                  </div>
                  <span className="text-xs font-medium text-foreground">New Skin: <span className="text-emerald-200">{skin}</span></span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Latest file info */}
        {latestFile && phase === "updating" && !allDone && (
          <p
            className="text-xs text-muted-foreground"
            style={{ padding: "0 4px", opacity: 0.7 }}
          >
            Changed file detected:{" "}
            <span className="font-mono">{latestFile}</span>
          </p>
        )}

        {/* Footer - only show Done/Close button when rebuild is complete */}
        {allDone && (
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              paddingTop: "4px",
            }}
          >
            <Button
              onClick={onDismiss}
              className="min-w-[100px]"
              style={{
                background: anyFailed
                  ? "hsl(var(--destructive))"
                  : "linear-gradient(135deg, #22c55e, #10b981)",
                border: "none",
                fontWeight: 600,
              }}
            >
              {anyFailed ? "Close" : "Done"}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
