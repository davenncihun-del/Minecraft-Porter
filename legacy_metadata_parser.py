"""
legacy_metadata_parser
========================

Lenient parser for 1.7.10-era ``mcmod.info`` files.

These files are *nominally* JSON but frequently contain syntax violations that
crash standard ``json.loads()``:

  - Trailing commas after the last element in arrays/objects
  - Unquoted keys
  - Single-quoted strings instead of double-quoted
  - C-style ``//`` line comments and ``/* ... */`` block comments
  - UTF-8 BOM markers
  - Windows-style ``\\r\\n`` line endings embedded in strings

The parser uses a **state-machine preprocessor** that normalizes the text into
valid JSON before passing it to ``json.loads()``.  If even that fails, a
fallback regex extractor pulls the key fields (``modid``, ``name``,
``version``, ``mcversion``) directly from the raw text.

This replaces the need for Gson's ``JsonReader.setLenient(true)`` without
adding a Java dependency.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def parse_mcmod_info(raw_text: str) -> List[Dict[str, Any]]:
    """Parse a potentially malformed mcmod.info file.

    Returns a list of mod entries (mcmod.info wraps entries in a JSON array).
    Each entry is a dict with keys like ``modid``, ``name``, ``version``,
    ``mcversion``, ``description``, ``authorList``, ``dependencies``.

    Never raises — returns an empty list on total failure.
    """
    # Phase 1: Preprocessing state machine
    cleaned = _preprocess(raw_text)

    # Phase 2: Try standard JSON parse
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
        if isinstance(data, dict):
            # Some mcmod.info files are bare objects instead of arrays
            mod_list = data.get("modList") or data.get("modlist")
            if isinstance(mod_list, list):
                return [entry for entry in mod_list if isinstance(entry, dict)]
            return [data]
    except json.JSONDecodeError:
        pass

    # Phase 3: Fallback regex extraction
    return _regex_extract(raw_text)


def extract_mcmod_version(raw_text: str) -> Optional[str]:
    """Extract the Minecraft version from a mcmod.info file."""
    entries = parse_mcmod_info(raw_text)
    for entry in entries:
        mc = entry.get("mcversion") or entry.get("mc_version")
        if isinstance(mc, str) and mc.strip():
            return mc.strip()
    return None


def extract_mcmod_id(raw_text: str) -> Optional[str]:
    """Extract the mod ID from a mcmod.info file."""
    entries = parse_mcmod_info(raw_text)
    for entry in entries:
        mod_id = entry.get("modid") or entry.get("mod_id")
        if isinstance(mod_id, str) and mod_id.strip():
            return mod_id.strip()
    return None


# ---------------------------------------------------------------------------
# Phase 1: State-machine preprocessor
# ---------------------------------------------------------------------------

def _preprocess(raw: str) -> str:
    """Normalize malformed JSON-ish text into valid JSON."""
    text = raw

    # Strip BOM
    if text.startswith("\ufeff"):
        text = text[1:]

    # Strip C-style block comments: /* ... */
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    # Strip C-style line comments: // ...
    # Must not strip inside strings — use a state machine
    text = _strip_line_comments(text)

    # Convert single-quoted strings to double-quoted
    text = _single_to_double_quotes(text)

    # Add quotes around unquoted keys
    text = _quote_unquoted_keys(text)

    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return text


def _strip_line_comments(text: str) -> str:
    """Remove // comments while respecting string boundaries."""
    result: List[str] = []
    i = 0
    in_string = False
    string_char = '"'

    while i < len(text):
        c = text[i]

        if in_string:
            result.append(c)
            if c == "\\" and i + 1 < len(text):
                i += 1
                result.append(text[i])
            elif c == string_char:
                in_string = False
        elif c in ('"', "'"):
            in_string = True
            string_char = c
            result.append(c)
        elif c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            # Skip to end of line
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            result.append(c)

        i += 1

    return "".join(result)


def _single_to_double_quotes(text: str) -> str:
    """Convert 'single-quoted' strings to "double-quoted".

    Only operates outside of already-double-quoted strings.
    """
    result: List[str] = []
    i = 0
    in_double = False

    while i < len(text):
        c = text[i]

        if in_double:
            result.append(c)
            if c == "\\" and i + 1 < len(text):
                i += 1
                result.append(text[i])
            elif c == '"':
                in_double = False
        elif c == '"':
            in_double = True
            result.append(c)
        elif c == "'":
            # Convert to double quote
            result.append('"')
        else:
            result.append(c)

        i += 1

    return "".join(result)


def _quote_unquoted_keys(text: str) -> str:
    """Add double quotes around unquoted object keys.

    Matches patterns like ``modid:`` or ``  mcversion :`` that appear as
    bare identifiers before a colon, outside of strings.
    """
    # Pattern: start of key context (after { or ,) → bare word → colon
    return re.sub(
        r'(?<=[\{,\n])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
        r' "\1":',
        text,
    )


# ---------------------------------------------------------------------------
# Phase 3: Regex fallback extractor
# ---------------------------------------------------------------------------

_FIELD_PATTERNS = {
    "modid": re.compile(r'["\']?modid["\']?\s*[:=]\s*["\']([^"\']+)["\']', re.IGNORECASE),
    "name": re.compile(r'["\']?name["\']?\s*[:=]\s*["\']([^"\']+)["\']', re.IGNORECASE),
    "version": re.compile(r'["\']?version["\']?\s*[:=]\s*["\']([^"\']+)["\']', re.IGNORECASE),
    "mcversion": re.compile(r'["\']?mcversion["\']?\s*[:=]\s*["\']([^"\']+)["\']', re.IGNORECASE),
    "description": re.compile(r'["\']?description["\']?\s*[:=]\s*["\']([^"\']+)["\']', re.IGNORECASE),
}


def _regex_extract(raw: str) -> List[Dict[str, Any]]:
    """Last-resort extraction: pull known fields via regex patterns."""
    entry: Dict[str, Any] = {}
    for key, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(raw)
        if match:
            entry[key] = match.group(1)

    # Try to extract authorList
    author_match = re.search(
        r'["\']?authorList["\']?\s*[:=]\s*\[([^\]]*)\]',
        raw,
        re.IGNORECASE,
    )
    if author_match:
        raw_authors = author_match.group(1)
        authors = re.findall(r'["\']([^"\']+)["\']', raw_authors)
        entry["authorList"] = authors

    # Try to extract dependencies
    dep_match = re.search(
        r'["\']?dependencies["\']?\s*[:=]\s*\[([^\]]*)\]',
        raw,
        re.IGNORECASE,
    )
    if dep_match:
        raw_deps = dep_match.group(1)
        deps = re.findall(r'["\']([^"\']+)["\']', raw_deps)
        entry["dependencies"] = deps

    return [entry] if entry else []
