import React from "react";
import { Badge } from "./ui/badge";

interface TagListProps {
  tags: string[];
  className?: string;
  /** Number of tags to show before collapsing to a simple +N */
  maxVisible?: number;
}

/**
 * Lightweight TagList: show up to `maxVisible` tags and a cheap +N count.
 * This removes measurement/resize overhead and avoids expensive calculations.
 */
export function TagList({ tags, className, maxVisible = 3 }: TagListProps) {
  const visible = Array.isArray(tags) ? tags.slice(0, maxVisible) : [];
  const extra = Math.max(
    0,
    (Array.isArray(tags) ? tags.length : 0) - visible.length
  );

  return (
    <div className={className}>
      <div className="flex items-center gap-1 overflow-hidden flex-nowrap">
        {visible.map((tag) => (
          <Badge
            key={`tag-${tag}`}
            variant="secondary"
            className="text-xs whitespace-nowrap"
          >
            {tag.toLowerCase()}
          </Badge>
        ))}
        {extra > 0 && (
          <Badge variant="outline" className="text-xs whitespace-nowrap">
            +{extra}
          </Badge>
        )}
      </div>
    </div>
  );
}

export default React.memo(TagList);
