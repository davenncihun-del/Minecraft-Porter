from __future__ import annotations
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import javalang

from bytecode import ClassReferenceExtractor
from betterdependency_bridge import run_betterdependency_cli

MIXIN_ANNOTATIONS = {"Inject", "Redirect", "ModifyArg", "ModifyVariable", "ModifyConstant", "ModifyReturnValue"}

@dataclass
class JavaSourceFile:
    path: str
    text: str
    tree: Optional[javalang.tree.CompilationUnit]
    parse_errors: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    types: List[str] = field(default_factory=list)
    method_calls: List[Tuple[Optional[str], str]] = field(default_factory=list)
    field_accesses: List[Tuple[Optional[str], str]] = field(default_factory=list)
    annotations: List[Dict[str, Any]] = field(default_factory=list)
    mixin_targets: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class MixinConfig:
    path: str
    config: Dict[str, Any]
    refmap: Optional[Dict[str, Any]]

@dataclass
class ArchiveAnalysis:
    source_path: Path
    loader: str
    metadata_path: Optional[str]
    current_version: Optional[str]
    target_version: Optional[str]
    java_files: List[JavaSourceFile] = field(default_factory=list)
    class_files: List[str] = field(default_factory=list)
    mixin_configs: List[MixinConfig] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    dependency_metadata: Dict[str, Any] = field(default_factory=dict)
    dependency_report: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    has_java: bool = False
    has_class: bool = False

class ArchiveAnalyzer:
    def analyze_archive(self, archive_path: Path, target_version: Optional[str] = None) -> ArchiveAnalysis:
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = archive.namelist()
            loader, metadata_path = self.detect_loader(names)
            metadata_text = None
            metadata = {}
            current_version = None
            if metadata_path and metadata_path in names:
                metadata_text = archive.read(metadata_path).decode("utf-8", errors="replace")
                metadata = self._parse_metadata(loader, metadata_path, metadata_text)
                current_version = self._extract_current_version(loader, metadata_path, metadata_text, metadata)

            analysis = ArchiveAnalysis(
                source_path=archive_path,
                loader=loader,
                metadata_path=metadata_path,
                current_version=current_version,
                target_version=target_version,
                metadata=metadata,
            )

            for name in names:
                if name.endswith(".java"):
                    text = archive.read(name).decode("utf-8", errors="replace")
                    java_file = self._analyze_java_source(name, text)
                    analysis.java_files.append(java_file)
                elif name.endswith(".class"):
                    analysis.class_files.append(name)
                elif name.endswith(".mixins.json") or name.endswith("mixins.json"):
                    config_text = archive.read(name).decode("utf-8", errors="replace")
                    try:
                        config = json.loads(config_text)
                    except json.JSONDecodeError:
                        config = {"error": "invalid json"}
                    refmap = None
                    refmap_name = config.get("refmap")
                    if isinstance(refmap_name, str) and refmap_name in names:
                        try:
                            refmap = json.loads(archive.read(refmap_name).decode("utf-8", errors="replace"))
                        except json.JSONDecodeError:
                            refmap = {"error": "invalid refmap"}
                    analysis.mixin_configs.append(MixinConfig(path=name, config=config, refmap=refmap))

            analysis.has_java = len(analysis.java_files) > 0
            analysis.has_class = len(analysis.class_files) > 0
            if not analysis.has_java and analysis.has_class:
                analysis.issues.append("Compiled Java classes were found without source. Bytecode-level validation will be used.")
            cli_result = run_betterdependency_cli(str(archive_path), target_version or "unknown", analysis.loader)
            analysis.dependency_report = cli_result.get("resolutions", [])
            return analysis

    def detect_loader(self, names: List[str]) -> Tuple[str, Optional[str]]:
        if "fabric.mod.json" in names:
            return "Fabric", "fabric.mod.json"
        if "META-INF/mods.toml" in names:
            return "Forge", "META-INF/mods.toml"
        if "META-INF/neoforge.mods.toml" in names:
            return "NeoForge", "META-INF/neoforge.mods.toml"
        if "pack.mcmeta" in names:
            return "Vanilla", "pack.mcmeta"
        return "Unknown", None

    def _parse_metadata(self, loader: str, metadata_path: str, text: str) -> Dict[str, Any]:
        try:
            if loader == "Fabric" or loader == "Vanilla":
                return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

        if loader in {"Forge", "NeoForge"}:
            try:
                import tomllib
                return tomllib.loads(text)
            except Exception:
                return {"raw": text}

        return {"raw": text}

    def _extract_current_version(self, loader: str, metadata_path: str, text: str, parsed: Dict[str, Any]) -> Optional[str]:
        if loader == "Fabric":
            if isinstance(parsed.get("depends"), dict):
                minecraft = parsed["depends"].get("minecraft")
                if isinstance(minecraft, str):
                    return minecraft
            if isinstance(parsed.get("minecraft_version"), str):
                return parsed["minecraft_version"]

        if loader in {"Forge", "NeoForge"}:
            if isinstance(parsed.get("minecraft"), str):
                return parsed["minecraft"]
            if isinstance(parsed.get("minecraft_version"), str):
                return parsed["minecraft_version"]
            match = re.search(r'minecraft\s*=\s*"([^"]+)"', text)
            if match:
                return match.group(1)

        if loader == "Vanilla":
            pack = parsed.get("pack") if isinstance(parsed.get("pack"), dict) else {}
            for key in ["supported_minecraft_version", "minecraft_version"]:
                value = pack.get(key)
                if isinstance(value, str):
                    return value
        return None

    def _analyze_java_source(self, path: str, text: str) -> JavaSourceFile:
        try:
            tree = javalang.parse.parse(text)
        except Exception as exc:
            return JavaSourceFile(path=path, text=text, tree=None, parse_errors=[str(exc)])

        source_file = JavaSourceFile(path=path, text=text, tree=tree)
        self._extract_java_symbols(source_file)
        return source_file

    def _extract_java_symbols(self, source_file: JavaSourceFile) -> None:
        tree = source_file.tree
        if tree is None:
            return

        for _, node in tree.filter(javalang.tree.Import):
            source_file.imports.append(node.path)

        for _, node in tree.filter(javalang.tree.ReferenceType):
            type_name = self._flatten_type(node)
            if type_name:
                source_file.types.append(type_name)

        for _, node in tree.filter(javalang.tree.MethodInvocation):
            source_file.method_calls.append((node.qualifier, node.member))

        for _, node in tree.filter(javalang.tree.MemberReference):
            if node.qualifier:
                source_file.field_accesses.append((node.qualifier, node.member))

        for _, node in tree.filter(javalang.tree.Annotation):
            annotation_name = node.name if hasattr(node, "name") else None
            if annotation_name:
                annotation = {"name": annotation_name, "elements": self._annotation_elements(node)}
                source_file.annotations.append(annotation)
                if annotation_name in MIXIN_ANNOTATIONS:
                    source_file.mixin_targets.append(annotation)

    def _flatten_type(self, node: javalang.tree.ReferenceType) -> str:
        if hasattr(node, "name"):
            return ".".join(node.name) if isinstance(node.name, list) else node.name
        return ""

    def _annotation_elements(self, node: javalang.tree.Annotation) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        if getattr(node, "element", None) is not None:
            element = node.element
            if isinstance(element, list):
                for pair in element:
                    values[pair.name] = self._annotation_value(pair.value)
            elif hasattr(element, "name"):
                values[element.name] = self._annotation_value(element.value)
        return values

    def _annotation_value(self, value: Any) -> Any:
        if hasattr(value, "value"):
            return value.value
        if isinstance(value, list):
            return [self._annotation_value(item) for item in value]
        return value
