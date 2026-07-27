"""Asset/PAK tagging service.

Kept free of eager submodule imports so that importing this package does not
drag in optional native dependencies.
"""
from .service import (  # noqa: F401
    invalidate_entity_map_cache,
    rebuild_all_pak_tags,
    rebuild_pak_tags_for,
    tag_all_assets,
    tag_assets_for_paks,
    tag_paks,
)

__all__ = [
    "tag_assets_for_paks",
    "rebuild_pak_tags_for",
    "tag_paks",
    "tag_all_assets",
    "rebuild_all_pak_tags",
    "invalidate_entity_map_cache",
]
