from __future__ import annotations
import json
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from analyzer import ArchiveAnalysis
from bytecode import ClassReferenceExtractor
from mappings import MappingDiff
from runtime.java_manager import JavaManager, JavaResolutionError

@dataclass
class ValidationResult:
    compile_errors: List[str] = field(default_factory=list)
    mixin_issues: List[str] = field(default_factory=list)
    bytecode_issues: List[str] = field(default_factory=list)
    dependency_issues: List[str] = field(default_factory=list)
    report: Dict[str, object] = field(default_factory=dict)

class Validator:
    def __init__(
        self,
        mapping_diff: MappingDiff,
        classpath_jars: Optional[List[Path]] = None,
        target_version: Optional[str] = None,
        loader: Optional[str] = None,
        java_manager: Optional[JavaManager] = None,
    ):
        self.mapping_diff = mapping_diff
        self.classpath_jars = classpath_jars or []
        self.target_version = target_version
        self.loader = loader
        self.java_manager = java_manager or JavaManager()

    def validate(self, analysis: ArchiveAnalysis) -> ValidationResult:
        result = ValidationResult()
        if analysis.has_java:
            result.compile_errors.extend(self._validate_source(analysis))
        if analysis.has_class:
            result.bytecode_issues.extend(self._validate_bytecode(analysis))
        result.mixin_issues.extend(self._validate_mixins(analysis))
        result.report = {
            "compile_errors": result.compile_errors,
            "bytecode_issues": result.bytecode_issues,
            "mixin_issues": result.mixin_issues,
            "dependency_issues": result.dependency_issues,
            "dependency_report": analysis.dependency_report,
        }
        return result

    def _validate_source(self, analysis: ArchiveAnalysis) -> List[str]:
        source_files: List[Path] = []
        with zipfile.ZipFile(analysis.source_path, "r") as archive:
            for name in archive.namelist():
                if name.endswith(".java"):
                    temp = Path(tempfile.mkdtemp()) / name
                    temp.parent.mkdir(parents=True, exist_ok=True)
                    temp.write_bytes(archive.read(name))
                    source_files.append(temp)

        if not source_files:
            return []

        if not self.target_version:
            return ["No target Minecraft version was supplied; cannot select a matching JDK for compilation."]

        try:
            javac = self.java_manager.javac_for(self.target_version, self.loader)
        except JavaResolutionError as exc:
            return [str(exc)]

        output_dir = Path(tempfile.mkdtemp())
        if self.classpath_jars:
            classpath = ";".join(str(jar) for jar in self.classpath_jars)
            command = [javac, "-cp", classpath, "-d", str(output_dir)] + [str(p) for p in source_files]
        else:
            command = [javac, "-d", str(output_dir)] + [str(p) for p in source_files]
        process = subprocess.run(command, capture_output=True, text=True)
        if process.returncode != 0:
            return process.stderr.splitlines()
        return []

    def _validate_bytecode(self, analysis: ArchiveAnalysis) -> List[str]:
        extractor = ClassReferenceExtractor()
        issues: List[str] = []
        with zipfile.ZipFile(analysis.source_path, "r") as archive:
            for name in archive.namelist():
                if name.endswith(".class"):
                    data = archive.read(name)
                    for reference in extractor.extract_references(data):
                        owner = reference.owner.replace("/", ".")
                        if self.mapping_diff and (self.mapping_diff.find_class_change(owner) or self.mapping_diff.find_method_change(owner, reference.name, reference.descriptor)):
                            issues.append(f"Binary reference {owner}.{reference.name}{reference.descriptor} may be incompatible with target mappings.")
        return issues

    def _validate_mixins(self, analysis: ArchiveAnalysis) -> List[str]:
        issues: List[str] = []
        for mixin_config in analysis.mixin_configs:
            config = mixin_config.config
            if not isinstance(config, dict):
                issues.append(f"Mixin config {mixin_config.path} is invalid JSON.")
                continue
            targets = config.get("mixins", [])
            if not targets:
                continue
            refmap = mixin_config.refmap or {}
            if not self.mapping_diff:
                continue
            for mixin_name in targets:
                if isinstance(mixin_name, str) and "." in mixin_name:
                    class_change = self.mapping_diff.find_class_change(mixin_name)
                    if class_change and class_change.kind == "class_removed":
                        issues.append(f"Mixin target class {mixin_name} missing in target mapping.")
        return issues
