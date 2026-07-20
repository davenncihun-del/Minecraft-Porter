from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from planner import MigrationPlan
from validator import ValidationResult

@dataclass
class ReportBundle:
    report: Dict[str, Any]
    report_path: Path

class ReportBuilder:
    def build(self, plan: MigrationPlan, validation: ValidationResult, destination_dir: Path) -> ReportBundle:
        report = {
            "source_path": str(plan.source_path),
            "loader": plan.loader,
            "target_version": plan.target_version,
            "summary": plan.summary,
            "java_replacements": [r.__dict__ for r in plan.java_replacements],
            "mixin_validation": [i.to_dict() for i in plan.mixin_validation],
            "schema_changes": [s.to_dict() for s in plan.schema_changes],
            "plan_diffs": [diff.to_dict() for diff in plan.plan_diffs],
            "dependencies": validation.report.get("dependency_issues", []),
            "dependency_report": validation.report.get("dependency_report", []),
            "validation": validation.report,
            "citations": plan.citations,
        }
        destination_dir.mkdir(parents=True, exist_ok=True)
        report_path = destination_dir / "compatibility-report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return ReportBundle(report=report, report_path=report_path)
