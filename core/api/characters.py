"""
API endpoints for Marvel Rivals character and skin data.
"""

from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.db.db import get_connection, get_all_characters, get_character_skins
from core.extraction.service import extract_and_ingest


router = APIRouter(prefix="/api", tags=["characters"])


class CharacterSkin(BaseModel):
    variant: str
    name: str


class Character(BaseModel):
    character_id: str
    name: str
    skins: List[CharacterSkin]


class RebuildResponse(BaseModel):
    success: bool
    message: str
    characters_count: int
    skins_count: int


@router.get("/characters", response_model=List[Character])
async def list_characters():
    """
    Get all characters with their skins.
    """
    conn = get_connection()
    try:
        characters = get_all_characters(conn)
        return characters
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch characters: {str(e)}")
    finally:
        conn.close()


@router.get("/characters/{character_id}/skins", response_model=List[CharacterSkin])
async def list_character_skins(character_id: str):
    """
    Get all skins for a specific character.
    """
    conn = get_connection()
    try:
        skins = get_character_skins(conn, character_id)
        if not skins:
            # Check if character exists
            cur = conn.cursor()
            char_exists = cur.execute(
                "SELECT COUNT(*) FROM characters WHERE character_id = ?",
                (character_id,)
            ).fetchone()[0] > 0
            
            if not char_exists:
                raise HTTPException(status_code=404, detail=f"Character {character_id} not found")
        
        return skins
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch skins: {str(e)}")
    finally:
        conn.close()


class TagLookupRequest(BaseModel):
    tags: List[str]


class TagInfo(BaseModel):
    type: str  # "character" or "skin"
    name: str | None = None
    character_id: str | None = None
    parent: str | None = None  # Primary parent (first match)
    parents: List[str] = []    # All possible parents for disambiguation


@router.post("/characters/lookup-tags", response_model=Dict[str, TagInfo])
async def lookup_tags(request: TagLookupRequest):
    """
    Lookup tags to determine which are characters and which are skins.
    Returns mapping of tag -> {type: 'character'|'skin', character_id?, parent?, parents[]}
    
    This is used by the frontend to properly build hierarchical character-skin filters.
    """
    conn = get_connection()
    try:
        result: Dict[str, TagInfo] = {}
        
        for tag in request.tags:
            tag_lower = tag.lower()
            
            # Check if it's a character name
            cur = conn.cursor()
            char = cur.execute(
                "SELECT character_id, name FROM characters WHERE LOWER(name) = ?",
                (tag_lower,)
            ).fetchone()
            
            if char:
                result[tag] = TagInfo(
                    type="character",
                    name=char[1],
                    character_id=char[0],
                    parent=None,
                    parents=[]
                )
                continue
            
            # Check if it's a skin name - fetch ALL matches to handle ambiguity
            # e.g. "The Life Fantastic" -> ["Mister Fantastic", "Invisible Woman"]
            skins = cur.execute(
                """SELECT s.character_id, c.name, s.name 
                   FROM skins s 
                   JOIN characters c ON s.character_id = c.character_id 
                   WHERE LOWER(s.name) = ?""",
                (tag_lower,)
            ).fetchall()
            
            if skins:
                # Aggregate all parents
                all_parents = sorted(list(set(row[1] for row in skins)))
                
                # Use first found as primary for backward compat
                primary_skin = skins[0]
                
                result[tag] = TagInfo(
                    type="skin",
                    name=primary_skin[2],
                    character_id=primary_skin[0],
                    parent=primary_skin[1],
                    parents=all_parents
                )
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to lookup tags: {str(e)}")
    finally:
        conn.close()


@router.post("/rebuild-character-data", response_model=RebuildResponse)
async def rebuild_character_data():
    """
    Rebuild character and skin data by re-extracting from PAK files.
    This will delete all existing character data and re-populate from game files.
    """
    try:
        # Run extraction and ingestion
        extract_and_ingest()
        
        # Get counts
        conn = get_connection()
        try:
            cur = conn.cursor()
            char_count = cur.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
            skin_count = cur.execute("SELECT COUNT(*) FROM skins").fetchone()[0]
            
            return RebuildResponse(
                success=True,
                message=f"Successfully rebuilt character data",
                characters_count=char_count,
                skins_count=skin_count
            )
        finally:
            conn.close()
            
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to rebuild character data: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Custom tag endpoints
# ---------------------------------------------------------------------------

class CustomTagItem(BaseModel):
    id: int
    tag: str
    added_at: Optional[str] = None


class AddCustomTagRequest(BaseModel):
    tag: str


@router.get("/mods/all-custom-tags", response_model=List[str])
async def get_all_custom_tags():
    """
    Return a distinct list of all custom tags used across all mods.
    Used to populate the tag suggestion dropdown in the frontend.
    """
    import json
    conn = get_connection()
    try:
        all_tags = set()
        
        # 1. Custom tags
        custom_rows = conn.execute("SELECT tag FROM mod_custom_tags").fetchall()
        for row in custom_rows:
            if row[0]:
                for part in row[0].split(","):
                    if part.strip():
                        all_tags.add(part.strip())
                
        # 2. Pak extracted tags
        pak_rows = conn.execute("SELECT tags_json FROM pak_tags_json").fetchall()
        for row in pak_rows:
            try:
                tags = json.loads(row[0])
                if isinstance(tags, list):
                    for t in tags:
                        if t and isinstance(t, str):
                            for part in t.split(","):
                                if part.strip():
                                    all_tags.add(part.strip())
            except:
                pass

        # 4. Official characters
        char_rows = conn.execute("SELECT name FROM characters").fetchall()
        for row in char_rows:
            if row[0]:
                all_tags.add(row[0].strip())

        # 5. Official skins
        skin_rows = conn.execute("SELECT name FROM skins").fetchall()
        for row in skin_rows:
            if row[0]:
                all_tags.add(row[0].strip())

        return sorted(list(all_tags), key=lambda x: x.lower())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch custom tags: {str(e)}")
    finally:
        conn.close()


@router.get("/mods/{mod_id}/custom-tags", response_model=List[CustomTagItem])
async def get_mod_custom_tags(mod_id: int):
    """
    Return all custom tags attached to a specific mod.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, tag, added_at FROM mod_custom_tags WHERE mod_id = ? ORDER BY added_at",
            (mod_id,)
        ).fetchall()
        return [{"id": row[0], "tag": row[1], "added_at": row[2]} for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch custom tags: {str(e)}")
    finally:
        conn.close()


@router.post("/mods/{mod_id}/custom-tags", response_model=CustomTagItem, status_code=201)
async def add_mod_custom_tag(mod_id: int, request: AddCustomTagRequest):
    """
    Add a custom tag to a mod. If the tag already exists for this mod, returns the existing one.
    """
    tag = request.tag.strip()
    if not tag:
        raise HTTPException(status_code=400, detail="Tag cannot be empty")
    if len(tag) > 100:
        raise HTTPException(status_code=400, detail="Tag must be 100 characters or fewer")

    conn = get_connection()
    try:
        # INSERT OR IGNORE lets us safely handle duplicates
        conn.execute(
            "INSERT OR IGNORE INTO mod_custom_tags (mod_id, tag) VALUES (?, ?)",
            (mod_id, tag)
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, tag, added_at FROM mod_custom_tags WHERE mod_id = ? AND tag = ? COLLATE NOCASE",
            (mod_id, tag)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to insert or retrieve tag")
        return {"id": row[0], "tag": row[1], "added_at": row[2]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add custom tag: {str(e)}")
    finally:
        conn.close()


@router.delete("/mods/{mod_id}/custom-tags/{tag_id}", response_model=dict)
async def remove_mod_custom_tag(mod_id: int, tag_id: int):
    """
    Remove a specific custom tag from a mod by its row ID.
    """
    conn = get_connection()
    try:
        result = conn.execute(
            "DELETE FROM mod_custom_tags WHERE id = ? AND mod_id = ?",
            (tag_id, mod_id)
        )
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Custom tag not found")
        return {"ok": True, "deleted_id": tag_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove custom tag: {str(e)}")
    finally:
        conn.close()
