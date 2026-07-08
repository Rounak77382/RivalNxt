import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Bell, Settings, RefreshCw, Rocket, Play, Archive, PowerOff, ShieldAlert } from "lucide-react";
import { open } from "@tauri-apps/plugin-shell";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "./ui/alert-dialog";

interface TabHeaderProps {
  activeTab: "downloads" | "active" | "collections";
  onTabChange: (tab: "downloads" | "active" | "collections") => void;
  downloadsCount: number;
  activeCount: number;
  collectionsCount?: number;
  updatesCount?: number;
  activeModsCount?: number;
  onRefresh?: () => void;
  onOpenSettings?: () => void;
  onOpenBootstrap?: () => void;
  onOpenBackup?: () => void;
  onDisableAllMods?: () => void;
  /** Called when the user clicks "Last Crash" to re-open the crash modal */
  onViewLastCrash?: () => void;
  /** Whether there is a crash available to view */
  hasLastCrash?: boolean;
}

export function TabHeader({
  activeTab,
  onTabChange,
  downloadsCount,
  activeCount,
  collectionsCount = 0,
  updatesCount = 0,
  activeModsCount = 0,
  onRefresh,
  onOpenSettings,
  onOpenBootstrap,
  onOpenBackup,
  onDisableAllMods,
  onViewLastCrash,
  hasLastCrash = false,
}: TabHeaderProps) {
  return (
    <div className="border-b border-border bg-card" style={{ contain: 'layout paint' }}>
      <div className="flex items-center p-4 justify-between">
        <div className="flex gap-1">
          <Button
            variant={activeTab === "downloads" ? "secondary" : "ghost"}
            onClick={() => onTabChange("downloads")}
            className="gap-2"
          >
            Downloads
            <Badge variant="secondary" className="text-xs">
              {downloadsCount}
            </Badge>
          </Button>

          <Button
            variant={activeTab === "active" ? "secondary" : "ghost"}
            onClick={() => onTabChange("active")}
            className="gap-2"
          >
            Active Mods
            <Badge variant="secondary" className="text-xs">
              {activeCount}
            </Badge>
          </Button>

          <Button
            variant={activeTab === "collections" ? "secondary" : "ghost"}
            onClick={() => onTabChange("collections")}
            className="gap-2"
          >
            Collections
            <Badge variant="secondary" className="text-xs">
              {collectionsCount}
            </Badge>
          </Button>
        </div>

        <div className="flex items-center gap-6">

          <div className="flex items-center gap-2">

            {hasLastCrash && onViewLastCrash && (
              <Button
                variant="outline"
                size="sm"
                onClick={onViewLastCrash}
                className="header-action-btn"
                style={{
                  borderColor: "rgba(234,88,12,0.6)",
                  color: "#fb923c",
                  animation: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
                }}
                title="View last crash report"
              >
                <ShieldAlert className="w-4 h-4 shrink-0" />
                <span className="header-action-text">Last Crash</span>
              </Button>
            )}
            {onOpenBootstrap && (
              <Button
                variant="outline"
                size="sm"
                onClick={onOpenBootstrap}
                className="header-action-btn"
              >
                <Rocket className="w-4 h-4 shrink-0" />
                <span className="header-action-text">
                  Setup
                </span>
              </Button>
            )}
            {onOpenBackup && (
              <Button
                variant="outline"
                size="sm"
                onClick={onOpenBackup}
                className="header-action-btn"
              >
                <Archive className="w-4 h-4 shrink-0" />
                <span className="header-action-text">
                  Backup
                </span>
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => open('steam://rungameid/2767030')}
              className="header-action-btn"
            >
              <Play className="w-4 h-4 shrink-0" />
              <span className="header-action-text">
                Start Game
              </span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onRefresh}
              className="header-action-btn"
            >
              <RefreshCw className="w-4 h-4 shrink-0" />
              <span className="header-action-text">
                Refresh
              </span>
            </Button>

            {onDisableAllMods && (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="destructive" size="sm" className="header-action-btn">
                    <PowerOff className="w-4 h-4 shrink-0" />
                    <span className="header-action-text">
                      Disable All
                    </span>
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Disable All Mods</AlertDialogTitle>
                    <AlertDialogDescription>
                      Are you sure you want to disable all active mods? Make sure to backup your active mods before disabling them if you want to restore them later.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={onDisableAllMods}>
                      Disable All
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={onOpenSettings}
            >
              <Settings className="w-4 h-4 shrink-0" />
              Settings
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
