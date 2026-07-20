"""
gradle_patcher
================

Automated Gradle buildscript upgrades/downgrades for legacy mod projects.

When porting mods across major Minecraft version boundaries, the build
environment itself often needs patching:

  - **1.7.10 mods**: Use ForgeGradle 1.2, Gradle 2.0, and reference
    ``files.minecraftforge.net`` (dead since 2022).  Needs Anatawa12's
    ForgeGradle 1.2 fork (``maven.anatawa12.com``).

  - **1.12.2 mods**: Use ForgeGradle 2.x, Gradle 4.10.3, and sometimes
    reference defunct JCenter.

  - **1.16+ mods**: Use ForgeGradle 3+/5+, Gradle 6+.

This patcher is structure-aware: it counts brace depth to identify Groovy
DSL blocks (``repositories { ... }``, ``dependencies { ... }``) and performs
targeted edits within those blocks, rather than using brittle line-by-line
regex replacements.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Known repository replacements
# ---------------------------------------------------------------------------

DEAD_REPOS: Dict[str, str] = {
    # ForgeGradle 1.2 era
    "files.minecraftforge.net/maven": "maven.minecraftforge.net",
    "files.minecraftforge.net": "maven.minecraftforge.net",
    # JCenter (sunset Feb 2021)
    "jcenter.bintray.com": "repo.maven.apache.org/maven2",
    "jcenter()": "mavenCentral()",
}

# Anatawa12's ForgeGradle 1.2 fork — the only working ForgeGradle for 1.7.10
ANATAWA12_BUILDSCRIPT_REPO = (
    '        maven {\n'
    '            name = "anatawa12"\n'
    '            url = "https://maven.anatawa12.com/repo"\n'
    '        }\n'
)

ANATAWA12_FG_PLUGIN = 'classpath "com.anatawa12.forge:ForgeGradle:1.2-1.1.+"'

# Gradle wrapper versions per era
GRADLE_WRAPPER_VERSIONS: Dict[str, str] = {
    "1.7.10": "2.0",
    "1.12.2": "4.10.3",
    "1.16.5": "6.8.3",
    "1.17.1": "7.1.1",
    "1.18.2": "7.4.2",
    "1.19.4": "7.6.1",
    "1.20.1": "8.1.1",
    "1.20.4": "8.4",
    "1.20.5": "8.7",
    "1.21.1": "8.8",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PatchResult:
    """Result of patching a build file."""
    path: str
    patched: bool
    changes: List[str] = field(default_factory=list)
    original: str = ""
    result: str = ""


# ---------------------------------------------------------------------------
# Groovy DSL block parser (structure-aware)
# ---------------------------------------------------------------------------

def _find_block(text: str, block_name: str) -> Optional[Tuple[int, int]]:
    """Find a Groovy DSL block by name (e.g. 'repositories') and return
    its (start, end) character indices including braces.

    Handles nested braces correctly via depth counting.
    """
    pattern = re.compile(rf'\b{re.escape(block_name)}\s*\{{')
    match = pattern.search(text)
    if not match:
        return None

    start = match.start()
    open_idx = match.end() - 1  # position of the opening brace
    depth = 0
    i = open_idx

    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
        i += 1

    return None  # Unbalanced braces


def _inject_into_block(
    text: str,
    block_name: str,
    injection: str,
    before_close: bool = True,
) -> Tuple[str, bool]:
    """Inject text into an existing Groovy DSL block.

    If ``before_close`` is True, inject just before the closing brace.
    """
    bounds = _find_block(text, block_name)
    if not bounds:
        return text, False

    start, end = bounds
    if before_close:
        insert_pos = end - 1  # just before the '}'
        result = text[:insert_pos] + injection + text[insert_pos:]
    else:
        # After the opening brace
        block_text = text[start:end]
        brace_idx = block_text.index("{")
        insert_pos = start + brace_idx + 1
        result = text[:insert_pos] + "\n" + injection + text[insert_pos:]

    return result, True


# ---------------------------------------------------------------------------
# Patcher implementations
# ---------------------------------------------------------------------------

def patch_build_gradle(
    content: str,
    target_version: str,
) -> PatchResult:
    """Patch a build.gradle file for the target Minecraft version.

    Applies:
      - Dead repository URL replacements
      - Anatawa12 ForgeGradle injection for 1.7.10
      - ForgeGradle classpath version updates
      - jcenter() → mavenCentral() replacement
    """
    result = PatchResult(path="build.gradle", patched=False, original=content)
    patched = content
    changes: List[str] = []

    # Replace dead repository URLs
    for dead, replacement in DEAD_REPOS.items():
        if dead in patched:
            if dead == "jcenter()":
                patched = patched.replace(dead, replacement)
            else:
                patched = patched.replace(dead, replacement)
            changes.append(f"Replaced dead repo: {dead} → {replacement}")

    # For 1.7.10 targets: inject Anatawa12 ForgeGradle fork
    from mappings.unified_graph import _version_tuple
    vt = _version_tuple(target_version)

    if vt <= (1, 7, 10):
        # Inject anatawa12 repository into buildscript.repositories
        if "anatawa12" not in patched:
            # Try to find buildscript { repositories { ... } }
            bs_bounds = _find_block(patched, "buildscript")
            if bs_bounds:
                bs_text = patched[bs_bounds[0]:bs_bounds[1]]
                repo_bounds = _find_block(bs_text, "repositories")
                if repo_bounds:
                    # Inject anatawa12 repo into buildscript.repositories
                    abs_insert = bs_bounds[0] + repo_bounds[1] - 1
                    patched = patched[:abs_insert] + ANATAWA12_BUILDSCRIPT_REPO + patched[abs_insert:]
                    changes.append("Injected Anatawa12 ForgeGradle 1.2 fork repository")

        # Replace ForgeGradle classpath if it exists
        fg_pattern = re.compile(r'classpath\s+["\']net\.minecraftforge\.gradle:ForgeGradle:[^"\']*["\']')
        if fg_pattern.search(patched):
            patched = fg_pattern.sub(ANATAWA12_FG_PLUGIN, patched)
            changes.append("Updated ForgeGradle classpath to Anatawa12 fork")

    elif vt <= (1, 12, 2):
        # Ensure ForgeGradle 2.x-compatible repository
        if "files.minecraftforge.net" in patched:
            patched = patched.replace("files.minecraftforge.net", "maven.minecraftforge.net")
            changes.append("Updated Forge maven URL for 1.12.2 compatibility")

    result.result = patched
    result.changes = changes
    result.patched = len(changes) > 0
    return result


def patch_gradle_wrapper(
    content: str,
    target_version: str,
) -> PatchResult:
    """Patch gradle-wrapper.properties to use the correct Gradle version."""
    result = PatchResult(
        path="gradle/wrapper/gradle-wrapper.properties",
        patched=False,
        original=content,
    )

    # Determine target Gradle version
    target_gradle = None
    from mappings.unified_graph import _version_tuple
    vt = _version_tuple(target_version)
    for mc_version, gradle_version in sorted(
        GRADLE_WRAPPER_VERSIONS.items(),
        key=lambda x: _version_tuple(x[0]),
        reverse=True,
    ):
        if vt >= _version_tuple(mc_version):
            target_gradle = gradle_version
            break

    if not target_gradle:
        target_gradle = "2.0"  # Fallback for ancient versions

    # Replace the distribution URL
    old_pattern = re.compile(
        r'distributionUrl\s*=\s*.*gradle-[\d.]+-(?:bin|all)\.zip'
    )
    new_url = (
        f"distributionUrl=https\\://services.gradle.org/distributions/"
        f"gradle-{target_gradle}-bin.zip"
    )

    if old_pattern.search(content):
        patched = old_pattern.sub(new_url, content)
        result.result = patched
        result.patched = patched != content
        if result.patched:
            result.changes.append(f"Updated Gradle wrapper to {target_gradle}")
    else:
        result.result = content

    return result


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------

class GradlePatcher:
    """Patches Gradle build files for a target Minecraft version."""

    def patch_workspace(
        self,
        workspace_dir: Path,
        target_version: str,
    ) -> List[PatchResult]:
        """Patch all relevant Gradle files in a workspace directory."""
        results: List[PatchResult] = []

        # build.gradle
        build_gradle = workspace_dir / "build.gradle"
        if build_gradle.exists():
            content = build_gradle.read_text(encoding="utf-8", errors="replace")
            result = patch_build_gradle(content, target_version)
            if result.patched:
                build_gradle.write_text(result.result, encoding="utf-8")
            results.append(result)

        # gradle-wrapper.properties
        wrapper_props = workspace_dir / "gradle" / "wrapper" / "gradle-wrapper.properties"
        if wrapper_props.exists():
            content = wrapper_props.read_text(encoding="utf-8", errors="replace")
            result = patch_gradle_wrapper(content, target_version)
            if result.patched:
                wrapper_props.write_text(result.result, encoding="utf-8")
            results.append(result)

        return results
