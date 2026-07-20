from __future__ import annotations
import json
import zipfile
from typing import Any, TYPE_CHECKING, Optional

from analyzer import ArchiveAnalysis
from diff_model import Confidence, DiffKind, Evidence, FieldDiff, field_diff_to_plan_item

if TYPE_CHECKING:
    from planner import MigrationPlan


class SchemaDiffer:
    def __init__(self, schema_source: Optional[str] = None):
        self.schema_source = schema_source or "vanilla-schemas"

    def diff_archive(self, analysis: ArchiveAnalysis, plan: MigrationPlan) -> None:
        with zipfile.ZipFile(analysis.source_path, "r") as archive:
            for member in archive.namelist():
                if member.endswith(".json") and not member.endswith("fabric.mod.json"):
                    try:
                        payload = json.loads(archive.read(member).decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    self._diff_json(member, payload, analysis, plan)

    def _diff_json(self, source_file: str, payload: Any, analysis: ArchiveAnalysis, plan: MigrationPlan) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                self._diff_json_field(source_file, key, value, analysis, plan)
        elif isinstance(payload, list):
            for item in payload:
                self._diff_json(source_file, item, analysis, plan)

    def _diff_json_field(self, source_file: str, field_name: str, value: Any, analysis: ArchiveAnalysis, plan: MigrationPlan) -> None:
        if field_name in {"item", "id", "ingredient", "result"} and isinstance(value, str):
            if value.startswith("minecraft:"):
                diff = FieldDiff(
                    kind=DiffKind.REVIEW,
                    confidence=Confidence.REVIEW,
                    path=f"{source_file}/{field_name}",
                    old_value_type="minecraft_id",
                    new_value_type="minecraft_id",
                    renamed_to=value,
                    description="Minecraft resource identifier found in schema JSON; verify compatibility for target version.",
                    evidence=Evidence(
                        source_kind="schema",
                        source_path=self.schema_source,
                        source_version=analysis.current_version or "unknown",
                        target_path=source_file,
                        target_version=analysis.target_version or "unknown",
                        locator=field_name,
                    ),
                )
                plan.plan_diffs.append(field_diff_to_plan_item(
                    diff,
                    source_file=source_file,
                    issue="Potential schema compatibility issue for Minecraft resource identifier.",
                    reason="Schema diff detected a resource identifier that may require update for the target version.",
                    details={"category": "schema", "field_name": field_name},
                ))
