"""
mappings.srg_parser
====================

Parses SRG and MCP mapping formats used by Forge for Minecraft 1.12.2 and
earlier.  These mappings use obfuscated identifiers like ``func_12345_a``,
``field_67890_b``, and ``p_12345_1_`` that need to be translated into
human-readable names before source-level analysis or transformation.

Two input formats are supported:

1. **.srg files** (joined.srg / notch-to-srg):
   Lines prefixed with CL/FD/MD mapping notch-obfuscated names → SRG names.

2. **MCP .csv exports** (methods.csv, fields.csv, params.csv):
   Comma-separated ``srg_name,mcp_name,side,description`` that translate SRG
   identifiers into human-readable MCP names.

The resulting ``SrgMappingSet`` can be queried by SRG name or by MCP name, and
is consumed by ``UnifiedMappingGraph`` to bridge 1.12-era symbol names into
modern named mappings via intermediary chaining.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SrgClassMapping:
    """A single class mapping: notch → srg (obfuscated → intermediate)."""
    notch_name: str
    srg_name: str


@dataclass
class SrgMethodMapping:
    """A method mapping with owning class context."""
    owner_notch: str
    name_notch: str
    descriptor_notch: str
    owner_srg: str
    name_srg: str
    descriptor_srg: str
    mcp_name: Optional[str] = None


@dataclass
class SrgFieldMapping:
    """A field mapping with owning class context."""
    owner_notch: str
    name_notch: str
    owner_srg: str
    name_srg: str
    mcp_name: Optional[str] = None


@dataclass
class SrgMappingSet:
    """Complete SRG/MCP mapping set for a single Minecraft version.

    Keying strategy (avoids if/else dispatch):
      - ``classes``: ``Dict[notch_name, SrgClassMapping]``
      - ``classes_by_srg``: ``Dict[srg_name, SrgClassMapping]``
      - ``methods``: ``Dict[srg_name, SrgMethodMapping]``   (func_XXXXX → mapping)
      - ``fields``:  ``Dict[srg_name, SrgFieldMapping]``    (field_XXXXX → mapping)
      - ``methods_by_mcp``: ``Dict[mcp_name, List[SrgMethodMapping]]`` (reverse index)
      - ``fields_by_mcp``:  ``Dict[mcp_name, List[SrgFieldMapping]]``  (reverse index)
    """
    version: str = ""
    classes: Dict[str, SrgClassMapping] = field(default_factory=dict)
    classes_by_srg: Dict[str, SrgClassMapping] = field(default_factory=dict)
    methods: Dict[str, SrgMethodMapping] = field(default_factory=dict)
    fields: Dict[str, SrgFieldMapping] = field(default_factory=dict)
    methods_by_mcp: Dict[str, List[SrgMethodMapping]] = field(default_factory=dict)
    fields_by_mcp: Dict[str, List[SrgFieldMapping]] = field(default_factory=dict)

    # ----- factory methods -----

    @classmethod
    def from_srg(cls, path: Path, version: str = "") -> "SrgMappingSet":
        """Parse a joined.srg or similar SRG-format mapping file."""
        mapping = cls(version=version)
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if not parts:
                continue

            prefix = parts[0]
            if prefix == "CL:" and len(parts) >= 3:
                notch, srg = parts[1], parts[2]
                entry = SrgClassMapping(notch_name=notch, srg_name=srg)
                mapping.classes[notch] = entry
                mapping.classes_by_srg[srg] = entry

            elif prefix == "FD:" and len(parts) >= 3:
                notch_full, srg_full = parts[1], parts[2]
                owner_notch, name_notch = _split_member(notch_full)
                owner_srg, name_srg = _split_member(srg_full)
                entry = SrgFieldMapping(
                    owner_notch=owner_notch, name_notch=name_notch,
                    owner_srg=owner_srg, name_srg=name_srg,
                )
                mapping.fields[name_srg] = entry

            elif prefix == "MD:" and len(parts) >= 5:
                notch_full, desc_notch = parts[1], parts[2]
                srg_full, desc_srg = parts[3], parts[4]
                owner_notch, name_notch = _split_member(notch_full)
                owner_srg, name_srg = _split_member(srg_full)
                entry = SrgMethodMapping(
                    owner_notch=owner_notch, name_notch=name_notch,
                    descriptor_notch=desc_notch,
                    owner_srg=owner_srg, name_srg=name_srg,
                    descriptor_srg=desc_srg,
                )
                mapping.methods[name_srg] = entry

        return mapping

    @classmethod
    def from_csv(
        cls,
        methods_csv: Path,
        fields_csv: Path,
        params_csv: Optional[Path] = None,
        version: str = "",
    ) -> "SrgMappingSet":
        """Load MCP CSV exports and overlay human-readable names onto SRG ids.

        The CSV files have the format: ``searge,name,side,desc``
        """
        mapping = cls(version=version)

        # Methods
        if methods_csv.exists():
            for row in _read_csv(methods_csv):
                srg_name = row.get("searge") or row.get("srg") or ""
                mcp_name = row.get("name") or row.get("mcp") or ""
                if not srg_name or not mcp_name:
                    continue
                entry = SrgMethodMapping(
                    owner_notch="", name_notch="",
                    descriptor_notch="",
                    owner_srg="", name_srg=srg_name,
                    descriptor_srg="",
                    mcp_name=mcp_name,
                )
                mapping.methods[srg_name] = entry
                mapping.methods_by_mcp.setdefault(mcp_name, []).append(entry)

        # Fields
        if fields_csv.exists():
            for row in _read_csv(fields_csv):
                srg_name = row.get("searge") or row.get("srg") or ""
                mcp_name = row.get("name") or row.get("mcp") or ""
                if not srg_name or not mcp_name:
                    continue
                entry = SrgFieldMapping(
                    owner_notch="", name_notch="",
                    owner_srg="", name_srg=srg_name,
                    mcp_name=mcp_name,
                )
                mapping.fields[srg_name] = entry
                mapping.fields_by_mcp.setdefault(mcp_name, []).append(entry)

        return mapping

    def overlay_csv(
        self,
        methods_csv: Path,
        fields_csv: Path,
        params_csv: Optional[Path] = None,
    ) -> None:
        """Apply MCP names from CSV onto an existing SRG set (loaded from .srg)."""
        if methods_csv.exists():
            for row in _read_csv(methods_csv):
                srg_name = row.get("searge") or row.get("srg") or ""
                mcp_name = row.get("name") or row.get("mcp") or ""
                if srg_name in self.methods and mcp_name:
                    self.methods[srg_name].mcp_name = mcp_name
                    self.methods_by_mcp.setdefault(mcp_name, []).append(
                        self.methods[srg_name]
                    )

        if fields_csv.exists():
            for row in _read_csv(fields_csv):
                srg_name = row.get("searge") or row.get("srg") or ""
                mcp_name = row.get("name") or row.get("mcp") or ""
                if srg_name in self.fields and mcp_name:
                    self.fields[srg_name].mcp_name = mcp_name
                    self.fields_by_mcp.setdefault(mcp_name, []).append(
                        self.fields[srg_name]
                    )

    # ----- query methods -----

    def resolve_method(self, srg_or_mcp: str) -> Optional[str]:
        """Given a func_XXXXX or MCP name, return the MCP / SRG counterpart."""
        if srg_or_mcp in self.methods:
            return self.methods[srg_or_mcp].mcp_name or srg_or_mcp
        if srg_or_mcp in self.methods_by_mcp:
            return self.methods_by_mcp[srg_or_mcp][0].name_srg
        return None

    def resolve_field(self, srg_or_mcp: str) -> Optional[str]:
        """Given a field_XXXXX or MCP name, return the MCP / SRG counterpart."""
        if srg_or_mcp in self.fields:
            return self.fields[srg_or_mcp].mcp_name or srg_or_mcp
        if srg_or_mcp in self.fields_by_mcp:
            return self.fields_by_mcp[srg_or_mcp][0].name_srg
        return None

    def resolve_class(self, notch_or_srg: str) -> Optional[str]:
        """Given a notch or SRG class name, return the SRG / notch counterpart."""
        if notch_or_srg in self.classes:
            return self.classes[notch_or_srg].srg_name
        if notch_or_srg in self.classes_by_srg:
            return self.classes_by_srg[notch_or_srg].notch_name
        return None

    def is_srg_name(self, name: str) -> bool:
        """Check if a name matches the SRG obfuscation pattern."""
        return bool(_SRG_PATTERN.match(name))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_SRG_PATTERN = re.compile(r"^(func_\d+_[a-zA-Z_]+|field_\d+_[a-zA-Z_]+|p_\d+_\d+_)$")


def _split_member(full_path: str) -> Tuple[str, str]:
    """Split 'net/minecraft/world/World/someMethod' into ('net/minecraft/world/World', 'someMethod')."""
    idx = full_path.rfind("/")
    if idx == -1:
        return "", full_path
    return full_path[:idx], full_path[idx + 1:]


def _read_csv(path: Path) -> List[Dict[str, str]]:
    """Read a CSV file, auto-detecting the dialect and skipping comments."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    return list(reader)
