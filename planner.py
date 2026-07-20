from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import javalang

from analyzer import ArchiveAnalysis, JavaSourceFile
from diff_model import Confidence, DiffKind, Evidence, FieldDiff, PlanDiff, field_diff_to_plan_item
from mixin_diff import MixinDiffer
from mappings import MappingDiff
from schema_diff import SchemaDiffer
from signature_rules import RuleSet, SignatureRewriteError
from type_tracker import LocalTypeTracker

@dataclass
class JavaReplacement:
    source_file: str
    kind: str
    old: str
    new: str
    location: Optional[str]
    reason: str
    provider: Optional[str] = None
    # Populated only for AST-resolved call-site rewrites (kind == "method_signature"),
    # where the exact source span is already known and re-searching the tree by name
    # would be ambiguous when a method is called more than once in a file.
    line: Optional[int] = None
    column: Optional[int] = None

@dataclass
class MigrationPlan:
    source_path: Path
    loader: str
    target_version: str
    java_replacements: List[JavaReplacement] = field(default_factory=list)
    plan_diffs: List[PlanDiff] = field(default_factory=list)
    citations: Dict[str, Any] = field(default_factory=dict)
    summary: List[str] = field(default_factory=list)

    @property
    def mixin_validation(self) -> List[PlanDiff]:
        return [diff for diff in self.plan_diffs if diff.details.get("category") == "mixin"]

    @property
    def schema_changes(self) -> List[PlanDiff]:
        return [diff for diff in self.plan_diffs if diff.details.get("category") == "schema"]

class MigrationPlanner:
    def __init__(
        self,
        mapping_diff: Optional[MappingDiff] = None,
        schema_source: Optional[str] = None,
        signature_ruleset: Optional[RuleSet] = None,
    ):
        self.mapping_diff = mapping_diff
        self.schema_source = schema_source
        # If a caller doesn't supply a ruleset explicitly, plan() falls back to
        # RuleSet.for_versions(), which looks up MinecraftPorter/rules/<from>_to_<to>.json.
        self.signature_ruleset = signature_ruleset

    def plan(self, analysis: ArchiveAnalysis) -> MigrationPlan:
        plan = MigrationPlan(
            source_path=analysis.source_path,
            loader=analysis.loader,
            target_version=analysis.target_version or "unknown",
        )

        if self.mapping_diff:
            plan.citations["mapping_diff"] = {
                "source": self.mapping_diff.source_file,
                "source_version": self.mapping_diff.source_version,
                "target_version": self.mapping_diff.target_version,
            }

        ruleset = self.signature_ruleset or RuleSet.for_versions(analysis.current_version, analysis.target_version)
        if ruleset:
            plan.citations["signature_ruleset"] = {
                "from_version": ruleset.from_version,
                "to_version": ruleset.to_version,
                "rule_count": len(ruleset.rules),
            }

        for java_file in analysis.java_files:
            self._plan_java_file(java_file, plan)
            if ruleset:
                self._plan_signature_rewrites(java_file, ruleset, plan)

        MixinDiffer(mapping_diff=self.mapping_diff).diff_analysis(analysis, plan)

        if self.schema_source:
            plan.citations["schema_source"] = self.schema_source
            SchemaDiffer(self.schema_source).diff_archive(analysis, plan)

        plan.summary.append(f"Planned {len(plan.java_replacements)} AST-driven Java source changes.")
        plan.summary.append(f"Detected {len(plan.mixin_validation)} mixin validation items.")
        plan.summary.append(f"Detected {len(plan.schema_changes)} datapack/schema warnings.")
        return plan

    def _plan_signature_rewrites(self, java_file: JavaSourceFile, ruleset: RuleSet, plan: MigrationPlan) -> None:
        """
        Type-aware call-site rewrites: flat-coordinate <-> object-instantiated
        API shapes (e.g. World#getBlockState(x,y,z) <-> World#getBlockState(BlockPos)),
        driven by the JSON ruleset in MinecraftPorter/rules/.

        Unlike the rename-only replacements in _plan_java_file, this pass resolves
        the receiver's declared type via LocalTypeTracker before a rule is allowed
        to fire, so a same-named method on an unrelated class is never rewritten.
        A call site with an unresolvable receiver type is skipped and reported,
        never guessed.
        """
        if java_file.tree is None:
            return

        tracker = LocalTypeTracker(java_file.tree)

        for _, invocation in java_file.tree.filter(javalang.tree.MethodInvocation):
            if not invocation.qualifier:
                continue

            receiver_type = tracker.resolve(invocation, invocation.qualifier)
            rule = ruleset.match(invocation, receiver_type)
            if rule is None:
                continue

            try:
                rewrite = ruleset.render(invocation, rule, java_file.text)
            except SignatureRewriteError as exc:
                plan.summary.append(
                    f"Skipped signature rule '{rule.id}' in {java_file.path} "
                    f"at {invocation.qualifier}.{invocation.member}(): {exc}"
                )
                continue

            plan.java_replacements.append(JavaReplacement(
                source_file=java_file.path,
                kind="method_signature",
                old=rewrite.old,
                new=rewrite.new,
                location=f"{invocation.qualifier}.{invocation.member}({rewrite.old}) "
                         f"@ line {invocation.position.line if invocation.position else '?'}",
                reason=rule.reason,
                provider=rule.id,
                line=rewrite.line,
                column=rewrite.column,
            ))

    def _plan_java_file(self, java_file: JavaSourceFile, plan: MigrationPlan) -> None:
        if self.mapping_diff is None:
            plan.summary.append(f"No mapping data available for {java_file.path}; using AST analysis only.")
            return

        for import_path in java_file.imports:
            change = self.mapping_diff.find_class_change(import_path)
            if change and change.kind == "class_renamed":
                plan.java_replacements.append(JavaReplacement(
                    source_file=java_file.path,
                    kind="import",
                    old=import_path,
                    new=change.target,
                    location=f"import {import_path}",
                    reason=change.reason,
                    provider=change.source_file,
                ))

        for type_name in java_file.types:
            if "." in type_name:
                change = self.mapping_diff.find_class_change(type_name)
                if change and change.kind == "class_renamed":
                    plan.java_replacements.append(JavaReplacement(
                        source_file=java_file.path,
                        kind="type_reference",
                        old=type_name,
                        new=change.target,
                        location=f"type {type_name}",
                        reason=change.reason,
                        provider=change.source_file,
                    ))

        for qualifier, method_name in java_file.method_calls:
            if qualifier and self.mapping_diff:
                owner = qualifier
                descriptor = ""  # descriptor resolution is approximate in source-only analysis
                change = self.mapping_diff.find_method_change(owner, method_name, descriptor)
                if change:
                    plan.java_replacements.append(JavaReplacement(
                        source_file=java_file.path,
                        kind="method_invocation",
                        old=method_name,
                        new=change.target.split(".")[-1],
                        location=f"{qualifier}.{method_name}()",
                        reason=change.reason,
                        provider=change.source_file,
                    ))

        for qualifier, field_name in java_file.field_accesses:
            if qualifier and self.mapping_diff:
                change = self.mapping_diff.find_field_change(qualifier, field_name)
                if change:
                    plan.java_replacements.append(JavaReplacement(
                        source_file=java_file.path,
                        kind="field_access",
                        old=field_name,
                        new=change.target.split(".")[-1],
                        location=f"{qualifier}.{field_name}",
                        reason=change.reason,
                        provider=change.source_file,
                    ))

