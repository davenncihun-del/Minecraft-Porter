from __future__ import annotations
import zipfile
from typing import TYPE_CHECKING, Optional

from analyzer import ArchiveAnalysis, MixinConfig
from diff_model import Confidence, DiffKind, Evidence, FieldDiff, field_diff_to_plan_item
from mappings import MappingDiff

if TYPE_CHECKING:
    from planner import MigrationPlan


class MixinDiffer:
    def __init__(self, mapping_diff: Optional[MappingDiff] = None):
        self.mapping_diff = mapping_diff

    def diff_analysis(self, analysis: ArchiveAnalysis, plan: MigrationPlan) -> None:
        for mixin_config in analysis.mixin_configs:
            self._diff_mixin_config(mixin_config, analysis, plan)

    def _diff_mixin_config(self, mixin_config: MixinConfig, analysis: ArchiveAnalysis, plan: MigrationPlan) -> None:
        config = mixin_config.config
        if not isinstance(config, dict):
            diff = FieldDiff(
                kind=DiffKind.REMOVED,
                confidence=Confidence.REVIEW,
                path=mixin_config.path,
                old_value_type="mixin_config",
                new_value_type="invalid",
                description="Mixin configuration is malformed and cannot be analyzed.",
                evidence=Evidence(
                    source_kind="mixin",
                    source_path=mixin_config.path,
                    source_version=analysis.current_version or "unknown",
                    target_path=analysis.loader,
                    target_version=analysis.target_version or "unknown",
                    locator="mixins",
                ),
            )
            plan.plan_diffs.append(field_diff_to_plan_item(
                diff,
                source_file=mixin_config.path,
                issue="Invalid mixin config format.",
                reason="Unable to parse mixin configuration JSON.",
                details={"category": "mixin", "locator": "mixins"},
            ))
            return

        targets = config.get("mixins", [])
        if not isinstance(targets, list):
            diff = FieldDiff(
                kind=DiffKind.TYPE_CHANGED,
                confidence=Confidence.REVIEW,
                path=mixin_config.path,
                old_value_type=type(targets).__name__,
                new_value_type="list",
                description="Mixin targets field has an unexpected type.",
                evidence=Evidence(
                    source_kind="mixin",
                    source_path=mixin_config.path,
                    source_version=analysis.current_version or "unknown",
                    target_path=analysis.loader,
                    target_version=analysis.target_version or "unknown",
                    locator="mixins",
                ),
            )
            plan.plan_diffs.append(field_diff_to_plan_item(
                diff,
                source_file=mixin_config.path,
                issue="Mixin targets field has unexpected type.",
                reason="Mixin config format is invalid for target loader expectations.",
                details={"category": "mixin", "locator": "mixins"},
            ))
            return

        if not targets:
            plan.summary.append(f"Mixin config {mixin_config.path} contains no mixins.")
            return

        for mixin_name in targets:
            if isinstance(mixin_name, str) and "." in mixin_name and self.mapping_diff:
                class_change = self.mapping_diff.find_class_change(mixin_name)
                if class_change and class_change.kind == "class_removed":
                    diff = FieldDiff(
                        kind=DiffKind.REMOVED,
                        confidence=Confidence.REVIEW,
                        path=f"{mixin_config.path}/mixins/{mixin_name}",
                        old_value_type="class_reference",
                        new_value_type="missing",
                        description="Mixin target class no longer exists in target mappings.",
                        evidence=Evidence(
                            source_kind="mixin",
                            source_path=mixin_config.path,
                            source_version=analysis.current_version or "unknown",
                            target_path=analysis.loader,
                            target_version=analysis.target_version or "unknown",
                            locator=mixin_name,
                        ),
                    )
                    plan.plan_diffs.append(field_diff_to_plan_item(
                        diff,
                        source_file=mixin_config.path,
                        issue=f"Mixin target class {mixin_name} missing in target mapping.",
                        reason="Mixin target refers to a class removed or renamed in the target environment.",
                        details={"category": "mixin", "mixin_target": mixin_name},
                    ))
