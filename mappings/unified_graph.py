"""
mappings.unified_graph
========================

The single entry point for all cross-version symbol resolution.  Instead of
scattering if/else chains across the codebase to handle three fundamentally
different mapping paradigms, the ``UnifiedMappingGraph`` dispatches through a
``Dict[MappingParadigm, Callable]`` lookup table:

  - **MODERN_NAMED**: Tiny intermediary → named (Fabric/Yarn/Mojmap). Uses
    the existing ``MappingSet`` / ``MappingDiff`` infrastructure.
  - **SRG_MCP**: SRG obfuscation strings (``func_``, ``field_``) → MCP →
    intermediary → named.  Uses ``SrgMappingSet``.
  - **NUMERIC_ID_META**: Pre-flattening numerical Block/Item IDs + metadata
    shorts → post-flattening namespaced identifiers.  Uses ``LegacyIdRegistry``.

Resolution is chained: a 1.7.10 symbol can be resolved through the numeric ID
layer into a 1.12 SRG name, then through the SRG layer into a modern named
symbol.  The graph handles this automatically when the paradigms of the source
and target versions differ.
"""
from __future__ import annotations

import re
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Dict, Optional

from mappings.legacy_ids import LegacyIdRegistry
from mappings.srg_parser import SrgMappingSet


class MappingParadigm(Enum):
    """Which mapping system a Minecraft version uses."""
    MODERN_NAMED = auto()    # 1.16+ : Tiny intermediary → named
    SRG_MCP = auto()         # 1.12.2 and 1.13–1.15 : func_/field_ SRG + MCP CSV
    NUMERIC_ID_META = auto() # 1.7.10 and earlier : numerical Block/Item IDs + metadata


# Version boundaries (inclusive upper bounds)
_VERSION_BOUNDARIES = [
    # (max_version_tuple, paradigm)
    ((1, 7, 10), MappingParadigm.NUMERIC_ID_META),
    ((1, 12, 2), MappingParadigm.SRG_MCP),
]
_DEFAULT_PARADIGM = MappingParadigm.MODERN_NAMED


def _version_tuple(version: str) -> tuple:
    """Parse '1.12.2' or '26.1' into a comparable tuple."""
    parts = []
    for part in re.split(r"[.\-]", version or ""):
        digits = re.match(r"(\d+)", part)
        parts.append(int(digits.group(1)) if digits else 0)
    return tuple(parts)


def detect_paradigm(version: str) -> MappingParadigm:
    """Determine which mapping paradigm a Minecraft version uses.

    No if/else chain: iterates a sorted boundary table.
    """
    vt = _version_tuple(version)
    for max_version, paradigm in _VERSION_BOUNDARIES:
        if vt <= max_version:
            return paradigm
    return _DEFAULT_PARADIGM


class UnifiedMappingGraph:
    """Dispatches symbol resolution across paradigm boundaries.

    The dispatch table maps each paradigm to its resolver function.  When
    source and target paradigms differ, the graph chains through intermediate
    paradigms automatically (e.g., NUMERIC_ID_META → SRG_MCP → MODERN_NAMED).

    Usage::

        graph = UnifiedMappingGraph(
            srg_set=SrgMappingSet.from_srg(Path("joined.srg")),
            legacy_ids=LegacyIdRegistry.load(),
        )
        result = graph.resolve("func_12345_a", "1.12.2", "1.21.1")
        # result = "someModernMethodName"

        result = graph.resolve_block_id(35, 14, "1.7.10", "1.21.1")
        # result = "minecraft:red_wool"
    """

    def __init__(
        self,
        srg_set: Optional[SrgMappingSet] = None,
        legacy_ids: Optional[LegacyIdRegistry] = None,
        # Modern mapping sets are loaded on-demand from the existing
        # mappings/ directory by the planner; this graph doesn't own them.
    ):
        self.srg_set = srg_set
        self.legacy_ids = legacy_ids or LegacyIdRegistry.load()

        # Dispatch table: paradigm → symbol resolver
        self._resolvers: Dict[MappingParadigm, Callable[[str], Optional[str]]] = {
            MappingParadigm.MODERN_NAMED: self._resolve_modern,
            MappingParadigm.SRG_MCP: self._resolve_srg,
            MappingParadigm.NUMERIC_ID_META: self._resolve_numeric,
        }

    def resolve(
        self,
        symbol: str,
        source_version: str,
        target_version: str,
    ) -> Optional[str]:
        """Resolve a symbol from source version's naming to target version's naming.

        Handles cross-paradigm chaining automatically.
        """
        source_paradigm = detect_paradigm(source_version)
        target_paradigm = detect_paradigm(target_version)

        if source_paradigm == target_paradigm:
            # Same paradigm — direct lookup
            resolver = self._resolvers.get(source_paradigm)
            return resolver(symbol) if resolver else None

        # Cross-paradigm: chain through the paradigm order
        current = symbol
        chain = self._build_chain(source_paradigm, target_paradigm)
        for paradigm in chain:
            resolver = self._resolvers.get(paradigm)
            if resolver:
                result = resolver(current)
                if result:
                    current = result
        return current if current != symbol else None

    def resolve_block_id(
        self,
        numeric_id: int,
        metadata: int,
        source_version: str,
        target_version: str,
    ) -> Optional[str]:
        """Resolve a pre-flattening block ID + metadata to a modern name."""
        return self.legacy_ids.to_modern(numeric_id, metadata)

    def resolve_block_name_to_id(
        self,
        modern_name: str,
        source_version: str,
        target_version: str,
    ) -> Optional[tuple]:
        """Resolve a modern block name back to a legacy (id, metadata) pair."""
        return self.legacy_ids.to_legacy(modern_name)

    # ----- paradigm-specific resolvers -----

    def _resolve_modern(self, symbol: str) -> Optional[str]:
        """Modern named symbols are already in the target namespace."""
        return symbol

    def _resolve_srg(self, symbol: str) -> Optional[str]:
        """Resolve a func_/field_ SRG name to its MCP (human-readable) name."""
        if self.srg_set is None:
            return None
        result = self.srg_set.resolve_method(symbol)
        if result:
            return result
        result = self.srg_set.resolve_field(symbol)
        if result:
            return result
        result = self.srg_set.resolve_class(symbol)
        return result

    def _resolve_numeric(self, symbol: str) -> Optional[str]:
        """Resolve a 'ID:META' string to a modern namespaced name."""
        if ":" not in symbol:
            return None
        parts = symbol.split(":")
        try:
            numeric_id = int(parts[0])
            metadata = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return None
        return self.legacy_ids.to_modern(numeric_id, metadata)

    # ----- chain builder -----

    def _build_chain(
        self,
        source: MappingParadigm,
        target: MappingParadigm,
    ) -> list:
        """Build the ordered list of paradigms to chain through.

        The paradigm order is: NUMERIC_ID_META → SRG_MCP → MODERN_NAMED
        """
        order = [
            MappingParadigm.NUMERIC_ID_META,
            MappingParadigm.SRG_MCP,
            MappingParadigm.MODERN_NAMED,
        ]
        try:
            src_idx = order.index(source)
            tgt_idx = order.index(target)
        except ValueError:
            return []

        if src_idx < tgt_idx:
            # Upgrading: walk forward
            return order[src_idx + 1: tgt_idx + 1]
        else:
            # Downgrading: walk backward
            return list(reversed(order[tgt_idx: src_idx]))
