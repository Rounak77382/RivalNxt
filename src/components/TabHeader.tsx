import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Bell, Settings, RefreshCw, Rocket, Play } from "lucide-react";
import { open } from "@tauri-apps/plugin-shell";

interface TabHeaderProps {
  activeTab: "downloads" | "active";
  onTabChange: (tab: "downloads" | "active") => void;
  downloadsCount: number;
  activeCount: number;
  updatesCount?: number;
  activeModsCount?: number;
  onRefresh?: () => void;
  onOpenSettings?: () => void;
  onOpenBootstrap?: () => void;
}

export function TabHeader({
  activeTab,
  onTabChange,
  downloadsCount,
  activeCount,
  updatesCount = 0,
  activeModsCount = 0,
  onRefresh,
  onOpenSettings,
  onOpenBootstrap,
}: TabHeaderProps) {
  return (
    <div className="border-b border-border bg-card">
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
        </div>

        <div className="flex items-center gap-6">

          <div className="flex items-center gap-2">
            {onOpenBootstrap && (
              <Button
                variant="outline"
                size="sm"
                onClick={onOpenBootstrap}
                className="gap-2"
              >
                <Rocket className="w-4 h-4" />
                Setup
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => open('steam://rungameid/2767030')}
              className="gap-2"
            >
              <Play className="w-4 h-4" />
              Start Game
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onRefresh}
              className="gap-2"
            >
              <RefreshCw className="w-4 h-4" />
              Refresh
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={onOpenSettings}
            >
              <Settings className="w-4 h-4" />
              Settings
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
