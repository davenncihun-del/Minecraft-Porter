from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Confidence(Enum):
    MECHANICAL = "MECHANICAL"
    REVIEW = "REVIEW"
    MANUAL = "MANUAL"


class DiffKind(Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    RENAMED = "RENAMED"
    TYPE_CHANGED = "TYPE_CHANGED"
    SIGNATURE_CHANGED = "SIGNATURE_CHANGED"
    REVIEW = "REVIEW"
    UNCHANGED = "UNCHANGED"


@dataclass
class Evidence:
    source_kind: str
    source_path: str
    source_version: str
    target_path: str
    target_version: str
    locator: str


@dataclass
class FieldDiff:
    kind: DiffKind
    confidence: Confidence
    path: str
    old_value_type: str
    new_value_type: str
    renamed_to: Optional[str] = None
    description: str = ""
    evidence: Optional[Evidence] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "kind": self.kind.value,
            "confidence": self.confidence.value,
            "path": self.path,
            "old_value_type": self.old_value_type,
            "new_value_type": self.new_value_type,
            "renamed_to": self.renamed_to,
            "description": self.description,
            "evidence": asdict(self.evidence) if self.evidence else None,
        }
        return payload


@dataclass
class PlanDiff:
    source_file: str
    issue: str
    reason: str
    diff: FieldDiff
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_file": self.source_file,
            "issue": self.issue,
            "reason": self.reason,
            "diff": self.diff.to_dict(),
            "details": self.details,
        }


def field_diff_to_plan_item(diff: FieldDiff, source_file: str, issue: str, reason: str, details: Optional[Dict[str, Any]] = None) -> PlanDiff:
    return PlanDiff(
        source_file=source_file,
        issue=issue,
        reason=reason,
        diff=diff,
        details=details or {},
    )
