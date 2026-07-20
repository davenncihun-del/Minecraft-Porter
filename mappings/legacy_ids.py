"""
mappings.legacy_ids
====================

Handles the pre-flattening numerical Block/Item ID + Metadata system used by
Minecraft 1.7.10 and 1.12.2, and maps it to/from the post-flattening
namespaced identifier system (1.13+).

Before 1.13 ("The Flattening"), blocks and items were identified by a numeric
ID and a metadata short.  For example:
  - Wool (ID 35) with metadata 14 = Red Wool
  - Stone (ID 1) with metadata 3  = Diorite

After 1.13, each variant became its own namespaced identifier:
  - ``minecraft:red_wool``
  - ``minecraft:diorite``

This module loads a static mapping table from ``legacy_block_ids.json`` and
exposes bidirectional lookup:
  - Forward  (upgrading):  ``(35, 14)`` → ``"minecraft:red_wool"``
  - Backward (downporting): ``"minecraft:red_wool"`` → ``(35, 14)``

Data sourced from PrismarineJS/minecraft-data legacy tables.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LEGACY_IDS_PATH = Path(__file__).resolve().parent / "legacy_block_ids.json"


@dataclass(frozen=True)
class LegacyBlockEntry:
    """A single block/item identity across the flattening boundary."""
    numeric_id: int
    metadata: int
    modern_name: str          # e.g. "minecraft:red_wool"
    legacy_name: str          # e.g. "wool" (the 1.12 registry name without namespace)
    display_name: str = ""    # e.g. "Red Wool"


class LegacyIdRegistry:
    """Bidirectional registry of pre-flattening IDs ↔ post-flattening names.

    Loaded once from ``legacy_block_ids.json``.  All lookups are O(1) dict
    queries — no iteration or linear search.
    """

    def __init__(self, entries: List[LegacyBlockEntry]):
        # Forward: (numeric_id, metadata) → modern_name
        self._forward: Dict[Tuple[int, int], LegacyBlockEntry] = {}
        # Reverse: modern_name → (numeric_id, metadata)
        self._reverse: Dict[str, LegacyBlockEntry] = {}
        # By legacy name: legacy_name → list of entries (one per metadata variant)
        self._by_legacy: Dict[str, List[LegacyBlockEntry]] = {}

        for entry in entries:
            self._forward[(entry.numeric_id, entry.metadata)] = entry
            self._reverse[entry.modern_name] = entry
            self._by_legacy.setdefault(entry.legacy_name, []).append(entry)

    @classmethod
    def load(cls, path: Path = LEGACY_IDS_PATH) -> "LegacyIdRegistry":
        """Load the registry from the bundled JSON file."""
        if not path.exists():
            return cls([])
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = []
        for item in data:
            entries.append(LegacyBlockEntry(
                numeric_id=int(item["id"]),
                metadata=int(item.get("meta", item.get("metadata", 0))),
                modern_name=item["modern"],
                legacy_name=item.get("legacy", item.get("name", "")),
                display_name=item.get("display", ""),
            ))
        return cls(entries)

    # ----- Forward: upgrading (1.7/1.12 → 1.13+) -----

    def to_modern(self, numeric_id: int, metadata: int = 0) -> Optional[str]:
        """Convert a legacy numeric ID + metadata to a modern namespaced name."""
        entry = self._forward.get((numeric_id, metadata))
        if entry:
            return entry.modern_name
        # Fallback: try metadata=0 (base variant)
        entry = self._forward.get((numeric_id, 0))
        return entry.modern_name if entry else None

    def to_modern_entry(self, numeric_id: int, metadata: int = 0) -> Optional[LegacyBlockEntry]:
        """Get the full entry for a legacy ID + metadata."""
        return self._forward.get((numeric_id, metadata))

    # ----- Reverse: downporting (1.13+ → 1.7/1.12) -----

    def to_legacy(self, modern_name: str) -> Optional[Tuple[int, int]]:
        """Convert a modern namespaced name to a legacy (numeric_id, metadata) pair."""
        entry = self._reverse.get(modern_name)
        if entry:
            return (entry.numeric_id, entry.metadata)
        # Try without namespace prefix
        if ":" in modern_name:
            bare = modern_name.split(":", 1)[1]
            entry = self._reverse.get(f"minecraft:{bare}")
            if entry:
                return (entry.numeric_id, entry.metadata)
        return None

    def to_legacy_entry(self, modern_name: str) -> Optional[LegacyBlockEntry]:
        """Get the full entry for a modern namespaced name."""
        return self._reverse.get(modern_name)

    # ----- By legacy name -----

    def variants_of(self, legacy_name: str) -> List[LegacyBlockEntry]:
        """Get all metadata variants of a legacy block name (e.g. 'wool' → 16 colors)."""
        return self._by_legacy.get(legacy_name, [])

    # ----- Bulk operations -----

    def all_modern_names(self) -> List[str]:
        """Return all modern namespaced names in the registry."""
        return list(self._reverse.keys())

    def all_legacy_ids(self) -> List[Tuple[int, int]]:
        """Return all (numeric_id, metadata) pairs in the registry."""
        return list(self._forward.keys())

    def __len__(self) -> int:
        return len(self._forward)
