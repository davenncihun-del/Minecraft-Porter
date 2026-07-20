"""
chain_porter
==============

Stage 4: Iterative chain porting for super-legacy mods.

Instead of attempting a single catastrophic version jump (e.g., 1.7.10 → 1.21),
the ``ChainPorter`` builds a sequence of stable "stepping stone" versions and
applies the porting pipeline at each hop.  The output of hop N becomes the
input of hop N+1.

This mirrors Mojang's own DataFixerUpper (DFU) approach: sequential
transformation functions applied in order across version boundaries.

The chain works in both directions:
  - **Upgrading** (1.7.10 → modern):  Forward chain, applying ARGS_TO_OBJECT,
    CONSTRUCTOR_ARG_STRIP, and modern API imports at each boundary.
  - **Downgrading** (modern → 1.7.10): Reverse chain, applying OBJECT_TO_ARGS,
    CONSTRUCTOR_ARG_INJECT, and restoring legacy API imports.

Usage::

    porter = ChainPorter()
    steps = porter.plan_chain("1.7.10", "1.21.1")
    # [PortStep(1.7.10→1.12.2), PortStep(1.12.2→1.13.2), ..., PortStep(1.20.1→1.21.1)]

    result_path, report = porter.execute_chain(archive_path, steps)
"""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mappings.unified_graph import MappingParadigm, detect_paradigm


# ---------------------------------------------------------------------------
# Stepping-stone version chain
# ---------------------------------------------------------------------------

# Ordered list of stable milestone versions.  Each adjacent pair has a
# corresponding rule set in rules/ (or at minimum, the metadata transformer
# knows how to adjust version ranges).
#
# Not every Minecraft version appears here — only versions that represent
# meaningful API / structural boundaries.  The porting engine applies
# metadata + rule rewrites at each hop.
STABLE_MILESTONES: List[str] = [
    "1.7.10",
    "1.12.2",
    "1.13.2",
    "1.16.5",
    "1.17.1",
    "1.18.2",
    "1.19.4",
    "1.20.1",
    "1.20.4",
    "1.20.5",
    "1.21.1",
    # Future versions get appended here
]


def _version_tuple(version: str) -> tuple:
    """Parse '1.12.2' into a comparable tuple of ints."""
    parts = []
    for part in re.split(r"[.\-]", version or ""):
        digits = re.match(r"(\d+)", part)
        parts.append(int(digits.group(1)) if digits else 0)
    return tuple(parts)


def _java_major_for(version: str) -> int:
    """Quick JDK major lookup — mirrors version_rules.json without loading it."""
    vt = _version_tuple(version)
    if vt <= (1, 16, 5):
        return 8
    if vt <= (1, 20, 4):
        return 17
    return 21


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PortStep:
    """A single hop in the chain: one version transition."""
    from_version: str
    to_version: str
    direction: str           # "upgrade" or "downgrade"
    mapping_paradigm: MappingParadigm
    target_paradigm: MappingParadigm
    required_java_major: int
    ruleset_path: Optional[str] = None   # e.g. "rules/1.12.2_to_1.13.2.json"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_version,
            "to": self.to_version,
            "direction": self.direction,
            "source_paradigm": self.mapping_paradigm.name,
            "target_paradigm": self.target_paradigm.name,
            "java_major": self.required_java_major,
            "ruleset": self.ruleset_path,
            "notes": self.notes,
        }


@dataclass
class StepResult:
    """The result of executing one hop."""
    step: PortStep
    success: bool
    output_path: Optional[Path] = None
    issues: List[str] = field(default_factory=list)
    rules_applied: int = 0
    metadata_rewritten: bool = False


@dataclass
class ChainReport:
    """Aggregate report of all hops in a chain execution."""
    source_version: str
    target_version: str
    total_steps: int
    completed_steps: int
    success: bool
    steps: List[StepResult] = field(default_factory=list)
    final_output: Optional[Path] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_version": self.source_version,
            "target_version": self.target_version,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "success": self.success,
            "chain": [
                {
                    "step": sr.step.to_dict(),
                    "success": sr.success,
                    "issues": sr.issues,
                    "rules_applied": sr.rules_applied,
                    "metadata_rewritten": sr.metadata_rewritten,
                }
                for sr in self.steps
            ],
        }


# ---------------------------------------------------------------------------
# ChainPorter
# ---------------------------------------------------------------------------

class ChainPorter:
    """Orchestrates multi-hop version porting."""

    def __init__(
        self,
        milestones: Optional[List[str]] = None,
        rules_dir: Optional[Path] = None,
    ):
        self.milestones = milestones or STABLE_MILESTONES
        self._milestone_tuples = [_version_tuple(v) for v in self.milestones]
        self.rules_dir = rules_dir or Path("rules")

    def plan_chain(
        self,
        source_version: str,
        target_version: str,
    ) -> List[PortStep]:
        """Compute the minimum sequence of hops between two versions.

        The chain always uses stable milestones as waypoints.  If the source
        or target is not a milestone, the nearest milestone is used as an
        endpoint.

        Returns an empty list if source == target.
        """
        src_t = _version_tuple(source_version)
        tgt_t = _version_tuple(target_version)

        if src_t == tgt_t:
            return []

        upgrading = src_t < tgt_t

        # Build the waypoints between source and target
        waypoints = [source_version]
        for milestone, mt in zip(self.milestones, self._milestone_tuples):
            if upgrading:
                if src_t < mt <= tgt_t:
                    waypoints.append(milestone)
            else:
                if tgt_t <= mt < src_t:
                    waypoints.append(milestone)

        if not upgrading:
            waypoints.reverse()

        # Ensure target is always the final waypoint
        if _version_tuple(waypoints[-1]) != tgt_t:
            waypoints.append(target_version)

        # Deduplicate consecutive identical versions
        deduped = [waypoints[0]]
        for wp in waypoints[1:]:
            if _version_tuple(wp) != _version_tuple(deduped[-1]):
                deduped.append(wp)
        waypoints = deduped

        # Build PortSteps from consecutive waypoint pairs
        steps: List[PortStep] = []
        direction = "upgrade" if upgrading else "downgrade"
        for i in range(len(waypoints) - 1):
            from_v = waypoints[i]
            to_v = waypoints[i + 1]
            src_paradigm = detect_paradigm(from_v)
            tgt_paradigm = detect_paradigm(to_v)

            # Determine which ruleset file to look for
            ruleset_path = self._find_ruleset(from_v, to_v)

            step = PortStep(
                from_version=from_v,
                to_version=to_v,
                direction=direction,
                mapping_paradigm=src_paradigm,
                target_paradigm=tgt_paradigm,
                required_java_major=_java_major_for(to_v),
                ruleset_path=str(ruleset_path) if ruleset_path else None,
                notes=self._step_notes(from_v, to_v),
            )
            steps.append(step)

        return steps

    def execute_chain(
        self,
        archive_path: Path,
        steps: List[PortStep],
        loader: str = "Forge",
        work_dir: Optional[Path] = None,
    ) -> Tuple[Optional[Path], ChainReport]:
        """Execute all hops in sequence.

        Each hop calls ``PortingEngine.apply_port()`` (imported lazily to
        avoid circular imports) with the output of the previous hop as input.

        Returns:
            (final_output_path, ChainReport)
        """
        if not steps:
            return archive_path, ChainReport(
                source_version="",
                target_version="",
                total_steps=0,
                completed_steps=0,
                success=True,
                final_output=archive_path,
            )

        report = ChainReport(
            source_version=steps[0].from_version,
            target_version=steps[-1].to_version,
            total_steps=len(steps),
            completed_steps=0,
            success=False,
        )

        # Lazy import to avoid circular dependency
        from porting_engine import PortingEngine

        # Create a temp working directory for intermediate outputs
        managed_dir = work_dir is None
        if managed_dir:
            work_dir = Path(tempfile.mkdtemp(prefix="chainport_"))

        current_input = archive_path

        try:
            for i, step in enumerate(steps):
                step_output = work_dir / f"hop_{i}_{step.from_version}_to_{step.to_version}.jar"

                # Copy current input to step output location for in-place patching
                shutil.copy2(current_input, step_output)

                try:
                    engine = PortingEngine(target_version=step.to_version, loader=loader)
                    result = engine.apply_port(
                        source_path=current_input,
                        output_path=step_output,
                    )

                    step_result = StepResult(
                        step=step,
                        success=True,
                        output_path=step_output,
                        rules_applied=result.get("rules_applied", 0) if isinstance(result, dict) else 0,
                        metadata_rewritten=True,
                    )

                except Exception as exc:
                    step_result = StepResult(
                        step=step,
                        success=False,
                        issues=[f"Hop {step.from_version}→{step.to_version} failed: {exc}"],
                    )
                    report.steps.append(step_result)
                    report.completed_steps = i
                    return None, report

                report.steps.append(step_result)
                current_input = step_output
                report.completed_steps = i + 1

            report.success = True
            report.final_output = current_input
            return current_input, report

        except Exception as exc:
            report.steps.append(StepResult(
                step=steps[report.completed_steps] if report.completed_steps < len(steps) else steps[-1],
                success=False,
                issues=[f"Chain execution failed: {exc}"],
            ))
            return None, report

    # ----- internal helpers -----

    def _find_ruleset(self, from_v: str, to_v: str) -> Optional[Path]:
        """Locate the rule set JSON for a specific version transition."""
        candidates = [
            self.rules_dir / f"{from_v}_to_{to_v}.json",
            self.rules_dir / f"{_normalize_version(from_v)}_to_{_normalize_version(to_v)}.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _step_notes(self, from_v: str, to_v: str) -> str:
        """Generate human-readable notes about what changes at this boundary."""
        notes = []
        from_t = _version_tuple(from_v)
        to_t = _version_tuple(to_v)

        # Key structural boundaries
        if from_t <= (1, 7, 10) and to_t >= (1, 12, 2):
            notes.append("Registry system overhaul: GameRegistry→ForgeRegistries")
        if from_t <= (1, 12, 2) and to_t >= (1, 13, 0):
            notes.append("The Flattening: numerical IDs→namespaced identifiers, metadata→block states")
        if from_t <= (1, 16, 5) and to_t >= (1, 17, 0):
            notes.append("Java 8→17 migration, world-gen restructuring")
        if from_t <= (1, 20, 4) and to_t >= (1, 20, 5):
            notes.append("Java 17→21 migration, codec-based data components")

        return "; ".join(notes) if notes else ""


def _normalize_version(version: str) -> str:
    """Normalize version strings for filename matching."""
    return version.replace(".", ".").strip()
