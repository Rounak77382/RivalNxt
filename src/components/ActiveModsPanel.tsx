import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Switch } from "./ui/switch";
import { Separator } from "./ui/separator";
import { ScrollArea } from "./ui/scroll-area";
import {
  Settings,
  AlertTriangle,
  CheckCircle,
  Activity,
  Cpu,
  HardDrive,
  Zap,
  Pause,
  Play,
  RotateCcw,
} from "lucide-react";
import type { Mod } from "./ModCard";

interface ActiveModsPanelProps {
  mods: Mod[];
  onToggleMod: (modId: string) => void;
  onDisableAll: () => void;
  onEnableAll: () => void;
}

export function ActiveModsPanel({
  mods,
  onToggleMod,
  onDisableAll,
  onEnableAll,
}: ActiveModsPanelProps) {
  const [sortBy, setSortBy] = useState<"name" | "category" | "performance">(
    "category"
  );

  const installedMods = mods.filter((mod) => mod.isInstalled);
  const activeMods = installedMods.filter((mod) => mod.isActive !== false);
  const inactiveMods = installedMods.filter((mod) => mod.isActive === false);

  // Sort mods
  const sortedActiveMods = [...activeMods].sort((a, b) => {
    const categoryA = a.categoryTags?.[0] ?? a.category ?? "";
    const categoryB = b.categoryTags?.[0] ?? b.category ?? "";
    switch (sortBy) {
      case "name":
        return a.name.localeCompare(b.name);
      case "category":
        return categoryA.localeCompare(categoryB);
      case "performance":
        return (b.performanceImpact || 0) - (a.performanceImpact || 0);
      default:
        return 0;
    }
  });

  const getPerformanceColor = (impact: number) => {
    if (impact <= 2) return "text-green-500";
    if (impact <= 4) return "text-yellow-500";
    return "text-red-500";
  };

  const getPerformanceLabel = (impact: number) => {
    if (impact <= 2) return "Low";
    if (impact <= 4) return "Medium";
    return "High";
  };

  return (
    <div className="w-96 bg-card border-l border-border flex flex-col h-full">
      {/* Header */}
      <CardHeader className="pb-4">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <Activity className="w-5 h-5" />
            Active Mods
          </CardTitle>
          <Badge variant="secondary">
            {activeMods.length}/{installedMods.length}
          </Badge>
        </div>

        {/* Quick Actions */}
        <div className="flex gap-2 mt-4">
          <Button
            variant="outline"
            size="sm"
            onClick={onDisableAll}
            className="flex-1 gap-2"
            disabled={activeMods.length === 0}
          >
            <Pause className="w-3 h-3" />
            Disable All
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onEnableAll}
            className="flex-1 gap-2"
            disabled={inactiveMods.length === 0}
          >
            <Play className="w-3 h-3" />
            Enable All
          </Button>
        </div>
      </CardHeader>

      <Separator />

      {/* System Status */}
      <CardContent className="py-4">
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <div className="flex items-center gap-2">
              <Cpu className="w-4 h-4 text-muted-foreground" />
              <span>CPU Impact</span>
            </div>
            <span className="font-medium">12%</span>
          </div>

          <div className="flex items-center justify-between text-sm">
            <div className="flex items-center gap-2">
              <HardDrive className="w-4 h-4 text-muted-foreground" />
              <span>Memory Usage</span>
            </div>
            <span className="font-medium">2.4 GB</span>
          </div>

          <div className="flex items-center justify-between text-sm">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-muted-foreground" />
              <span>Load Time</span>
            </div>
            <span className="font-medium">+1.2s</span>
          </div>
        </div>
      </CardContent>

      <Separator />

      {/* Sort Options */}
      <CardContent className="py-4">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">Sort by:</span>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as any)}
            className="text-sm bg-background border border-border rounded px-2 py-1"
          >
            <option value="category">Category</option>
            <option value="name">Name</option>
            <option value="performance">Performance</option>
          </select>
        </div>
      </CardContent>

      <Separator />

      {/* Active Mods List */}
      <div className="flex-1 overflow-hidden">
        <ScrollArea className="h-full">
          <CardContent className="py-4">
            {installedMods.length === 0 ? (
              <div className="text-center py-8">
                <Activity className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p className="text-sm text-muted-foreground">
                  No mods installed
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {sortedActiveMods.map((mod) => {
                  const impact =
                    mod.performanceImpact || Math.floor(Math.random() * 5) + 1;
                  const categoryLabelSource =
                    mod.categoryTags && mod.categoryTags.length > 0
                      ? mod.categoryTags.join(", ")
                      : mod.category;
                  const categoryLabel = categoryLabelSource
                    ? categoryLabelSource.trim()
                    : "";

                  return (
                    <Card key={mod.id} className="p-3">
                      <div className="flex items-start gap-3">
                        <div className="w-10 h-8 bg-muted rounded overflow-hidden flex-shrink-0">
                          <img
                            src={mod.images[0]}
                            alt={mod.name}
                            className="w-full h-full object-cover"
                          />
                        </div>

                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between mb-1">
                            <h4 className="text-sm font-medium truncate">
                              {mod.name}
                            </h4>
                            <Switch
                              checked={mod.isActive !== false}
                              onCheckedChange={() => onToggleMod(mod.id)}
                            />
                          </div>

                          <div className="flex items-center gap-2 mb-2">
                            {categoryLabel ? (
                              <Badge variant="outline" className="text-xs">
                                {categoryLabel}
                              </Badge>
                            ) : null}
                            <span
                              className={`text-xs ${getPerformanceColor(
                                impact
                              )}`}
                            >
                              {getPerformanceLabel(impact)} Impact
                            </span>
                          </div>

                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            {mod.hasUpdate ? (
                              <div className="flex items-center gap-1 text-destructive">
                                <AlertTriangle className="w-3 h-3" />
                                Update Available
                              </div>
                            ) : (
                              <div className="flex items-center gap-1 text-green-500">
                                <CheckCircle className="w-3 h-3" />
                                Up to date
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </Card>
                  );
                })}

                {inactiveMods.length > 0 && (
                  <>
                    <div className="pt-4">
                      <h3 className="text-sm font-medium text-muted-foreground mb-3">
                        Disabled Mods ({inactiveMods.length})
                      </h3>
                    </div>

                    {inactiveMods.map((mod) => {
                      const categoryLabelSource =
                        mod.categoryTags && mod.categoryTags.length > 0
                          ? mod.categoryTags.join(", ")
                          : mod.category;
                      const categoryLabel = categoryLabelSource
                        ? categoryLabelSource.trim()
                        : "";

                      return (
                        <Card key={mod.id} className="p-3 opacity-50">
                          <div className="flex items-start gap-3">
                            <div className="w-10 h-8 bg-muted rounded overflow-hidden flex-shrink-0">
                              <img
                                src={mod.images[0]}
                                alt={mod.name}
                                className="w-full h-full object-cover grayscale"
                              />
                            </div>

                            <div className="flex-1 min-w-0">
                              <div className="flex items-center justify-between mb-1">
                                <h4 className="text-sm font-medium truncate">
                                  {mod.name}
                                </h4>
                                <Switch
                                  checked={false}
                                  onCheckedChange={() => onToggleMod(mod.id)}
                                />
                              </div>

                              <div className="flex items-center gap-2">
                                {categoryLabel ? (
                                  <Badge variant="outline" className="text-xs">
                                    {categoryLabel}
                                  </Badge>
                                ) : null}
                                <span className="text-xs text-muted-foreground">
                                  Disabled
                                </span>
                              </div>
                            </div>
                          </div>
                        </Card>
                      );
                    })}
                  </>
                )}
              </div>
            )}
          </CardContent>
        </ScrollArea>
      </div>

      <Separator />

      {/* Footer Actions */}
      <CardContent className="py-4">
        <div className="space-y-2">
          <Button variant="outline" size="sm" className="w-full gap-2">
            <Settings className="w-3 h-3" />
            Mod Load Order
          </Button>
          <Button variant="outline" size="sm" className="w-full gap-2">
            <RotateCcw className="w-3 h-3" />
            Reset to Default
          </Button>
        </div>
      </CardContent>
    </div>
  );
}
