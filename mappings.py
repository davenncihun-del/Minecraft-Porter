from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

@dataclass
class MemberMapping:
    intermediary: str
    named: str
    descriptor: Optional[str] = None

@dataclass
class ClassMapping:
    intermediary: str
    named: str
    methods: Dict[Tuple[str, str], MemberMapping] = field(default_factory=dict)
    fields: Dict[str, MemberMapping] = field(default_factory=dict)

class MappingSet:
    def __init__(self, version: str, source_ns: str = "intermediary", target_ns: str = "named"):
        self.version = version
        self.source_ns = source_ns
        self.target_ns = target_ns
        self.classes: Dict[str, ClassMapping] = {}
        self.source_ns_index = 0
        self.target_ns_index = 1

    @classmethod
    def from_tiny(cls, path: Path, source_ns: str = "intermediary", target_ns: str = "named") -> "MappingSet":
        with path.open("r", encoding="utf-8") as handle:
            lines = [line.rstrip("\n") for line in handle if line.strip()]

        if not lines or not lines[0].startswith("tiny"):
            raise ValueError(f"Unsupported mapping format in {path}")

        namespaces = []
        for line in lines[1:]:
            if not line.startswith("#"):
                namespaces = line.split("\t")
                break

        if source_ns not in namespaces or target_ns not in namespaces:
            raise ValueError(f"Mapping file {path} does not contain namespaces {source_ns} and {target_ns}")

        source_index = namespaces.index(source_ns)
        target_index = namespaces.index(target_ns)
        mapping = cls(path.stem, source_ns=source_ns, target_ns=target_ns)
        mapping.source_ns_index = source_index
        mapping.target_ns_index = target_index

        current_class: Optional[ClassMapping] = None
        for line in lines[2:]:
            parts = line.split("\t")
            if not parts:
                continue
            if parts[0] == "c" and len(parts) > max(source_index, target_index):
                source_name = parts[1]
                target_name = parts[2]
                current_class = ClassMapping(intermediary=source_name, named=target_name)
                mapping.classes[source_name] = current_class
            elif parts[0] == "f" and current_class is not None and len(parts) > max(source_index, target_index):
                source_name = parts[1]
                target_name = parts[2]
                current_class.fields[source_name] = MemberMapping(intermediary=source_name, named=target_name)
            elif parts[0] == "m" and current_class is not None and len(parts) > max(source_index, target_index):
                source_name = parts[1]
                descriptor = parts[2] if len(parts) > 2 else ""
                target_name = parts[3] if len(parts) > 3 else source_name
                current_class.methods[(source_name, descriptor)] = MemberMapping(intermediary=source_name, named=target_name, descriptor=descriptor)
        return mapping

    def get_class(self, intermediary: str) -> Optional[ClassMapping]:
        return self.classes.get(intermediary)

    def find_class_by_named(self, named: str) -> Optional[ClassMapping]:
        for clazz in self.classes.values():
            if clazz.named == named:
                return clazz
        return None

@dataclass
class SymbolChange:
    kind: str
    source: str
    target: str
    reason: str
    source_file: Optional[str] = None

@dataclass
class MappingDiff:
    source_version: str
    target_version: str
    class_changes: Dict[str, SymbolChange] = field(default_factory=dict)
    method_changes: Dict[Tuple[str, str, str], SymbolChange] = field(default_factory=dict)
    field_changes: Dict[Tuple[str, str], SymbolChange] = field(default_factory=dict)
    source_file: Optional[str] = None

    @classmethod
    def from_sets(cls, source: MappingSet, target: MappingSet) -> "MappingDiff":
        diff = cls(source_version=source.version, target_version=target.version, source_file=None)
        diff.source_file = f"{source.version}->{target.version}"

        for intermediary, source_class in source.classes.items():
            target_class = target.get_class(intermediary)
            if target_class is None:
                diff.class_changes[source_class.named] = SymbolChange(
                    kind="class_removed",
                    source=source_class.named,
                    target="<missing>",
                    reason="Class exists in source mapping but does not appear in target mapping.",
                    source_file=diff.source_file,
                )
                continue

            if source_class.named != target_class.named:
                diff.class_changes[source_class.named] = SymbolChange(
                    kind="class_renamed",
                    source=source_class.named,
                    target=target_class.named,
                    reason=f"Class renamed from {source_class.named} to {target_class.named} based on mapping diff.",
                    source_file=diff.source_file,
                )

            for field_name, source_field in source_class.fields.items():
                target_field = target_class.fields.get(field_name)
                if target_field and source_field.named != target_field.named:
                    diff.field_changes[(source_class.named, source_field.named)] = SymbolChange(
                        kind="field_renamed",
                        source=f"{source_class.named}.{source_field.named}",
                        target=f"{target_class.named}.{target_field.named}",
                        reason=f"Field renamed in class {source_class.named} based on mapping diff.",
                        source_file=diff.source_file,
                    )
                elif target_field is None:
                    diff.field_changes[(source_class.named, source_field.named)] = SymbolChange(
                        kind="field_removed",
                        source=f"{source_class.named}.{source_field.named}",
                        target="<missing>",
                        reason=f"Field {source_field.named} no longer exists in target mapping.",
                        source_file=diff.source_file,
                    )

            for (method_name, method_desc), source_method in source_class.methods.items():
                target_method = target_class.methods.get((method_name, method_desc))
                if target_method and source_method.named != target_method.named:
                    diff.method_changes[(source_class.named, source_method.named, method_desc)] = SymbolChange(
                        kind="method_renamed",
                        source=f"{source_class.named}.{source_method.named}{method_desc}",
                        target=f"{target_class.named}.{target_method.named}{method_desc}",
                        reason=f"Method renamed in class {source_class.named} based on mapping diff.",
                        source_file=diff.source_file,
                    )
                elif target_method is None:
                    diff.method_changes[(source_class.named, source_method.named, method_desc)] = SymbolChange(
                        kind="method_removed",
                        source=f"{source_class.named}.{source_method.named}{method_desc}",
                        target="<missing>",
                        reason=f"Method {source_method.named}{method_desc} no longer exists in target mapping.",
                        source_file=diff.source_file,
                    )
        return diff

    def find_class_change(self, qualified_name: str) -> Optional[SymbolChange]:
        return self.class_changes.get(qualified_name)

    def find_method_change(self, owner: str, method_name: str, descriptor: str) -> Optional[SymbolChange]:
        return self.method_changes.get((owner, method_name, descriptor))

    def find_field_change(self, owner: str, field_name: str) -> Optional[SymbolChange]:
        return self.field_changes.get((owner, field_name))
