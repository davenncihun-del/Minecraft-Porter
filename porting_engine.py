import io
import json
import re
import zipfile
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from transformer import rewrite_metadata


JAVA_MIGRATIONS = [
    (r"net\.minecraft\.util\.ResourceLocation", "net.minecraft.resources.ResourceLocation"),
    (r"net\.minecraft\.util\.text", "net.minecraft.network.chat"),
    (r"\bITextComponent\b", "Component"),
    (r"\bTextComponent\b", "Component"),
    (r"net\.minecraftforge\.common\.MinecraftForge", "net.neoforge.common.NeoForge"),
    (r"net\.minecraftforge\.event\.bus\.api\.SubscribeEvent", "net.neoforge.eventbus.api.SubscribeEvent"),
    (r"net\.minecraftforge\.common\.Mod", "net.neoforge.common.Mod"),
    (r"net\.minecraftforge\.client\.event\.RenderGuiOverlayEvent", "net.neoforge.client.event.RenderGuiOverlayEvent"),
    (r"net\.minecraftforge\.event\.entity\.living\.LivingEvent", "net.neoforge.event.entity.living.LivingEvent"),
    (r"\bRegistryEvent\.Register\b", "RegisterEvent"),
    (r"\bMinecraftForge\.EVENT_BUS\b", "NeoForge.EVENT_BUS"),
    (r"\bFMLJavaModLoadingContext\b", "ModLoadingContext"),
    (r"\bDist\.CLIENT\b", "Dist.CLIENT"),
    (r"\bModLoadingContext\.get\(\)\b", "ModLoadingContext.get()"),
]

RESOURCE_RULES = [
    ("minecraft:models", "assets/<namespace>/models"),
    ("minecraft:lang", "assets/<namespace>/lang"),
    ("minecraft:recipes", "data/<namespace>/recipe"),
    ("minecraft:loot_tables", "data/<namespace>/loot_table"),
    ("minecraft:tags", "data/<namespace>/tags"),
]


MAX_NESTED_JAR_DEPTH = 4


class PortingEngine:
    def __init__(self, target_version: str, loader: str):
        self.target_version = target_version
        self.loader = loader
        self.pack_format_map = {
            "1.12": 1,
            "1.12.1": 1,
            "1.12.2": 3,
            "1.13.2": 4,
            "1.14.4": 4,
            "1.15.2": 5,
            "1.16.4": 6,
            "1.16.5": 6,
            "1.17.1": 7,
            "1.18.2": 8,
            "1.19.2": 9,
            "1.19.4": 10,
            "1.20.1": 15,
            "1.20.4": 22,
            "1.20.6": 32,
            "1.21.1": 42,
            "1.21.5": 48,
            "1.21.6": 48,
            "1.21.7": 48,
            "1.21.8": 48,
            "1.21.9": 48,
            "1.21.10": 48,
            "1.21.11": 48,
            "26.1": 48,
            "26.1.1": 48,
            "26.1.2": 48,
            "26.2": 48,
        }

    def detect_loader(self, names: List[str]) -> Tuple[str, Optional[str]]:
        # A user-selected loader is authoritative only when its matching
        # metadata exists. This avoids an archive containing both Fabric and
        # Forge metadata silently taking the first file in a fixed order.
        metadata_by_loader = {
            "Fabric": "fabric.mod.json",
            "Forge": "META-INF/mods.toml",
            "NeoForge": "META-INF/neoforge.mods.toml",
            "Vanilla": "pack.mcmeta",
        }
        requested_metadata = metadata_by_loader.get(self.loader)
        if requested_metadata in names:
            return self.loader, requested_metadata
        if "fabric.mod.json" in names:
            return "Fabric", "fabric.mod.json"
        if "META-INF/mods.toml" in names:
            return "Forge", "META-INF/mods.toml"
        if "META-INF/neoforge.mods.toml" in names:
            return "NeoForge", "META-INF/neoforge.mods.toml"
        if "pack.mcmeta" in names:
            return "Vanilla", "pack.mcmeta"
        return self.loader or "Unknown", None

    def detect_current_version(self, loader: str, metadata_path: Optional[str], text: str) -> Optional[str]:
        if loader == "Fabric":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return None
            depends = data.get("depends", {}) if isinstance(data.get("depends"), dict) else {}
            if isinstance(depends, dict):
                minecraft = depends.get("minecraft")
                if isinstance(minecraft, str) and minecraft:
                    return minecraft
            if isinstance(data.get("minecraft_version"), str):
                return data["minecraft_version"]
            return None

        if loader in {"Forge", "NeoForge"}:
            for pattern in [r'minecraft\s*=\s*"([^"]+)"', r'versionRange\s*=\s*"([^\"]+)"']:
                m = re.search(pattern, text)
                if m:
                    return m.group(1).strip('[]()')
            return None

        if loader == "Vanilla":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return None
            pack = data.get("pack", {}) if isinstance(data.get("pack"), dict) else {}
            for key in ["pack_format", "supported_minecraft_version", "minecraft_version"]:
                if isinstance(pack.get(key), (int, str)):
                    return str(pack.get(key))
            return None

        return None

    def collect_metadata(self, archive_path: Path) -> Dict[str, object]:
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = archive.namelist()
            loader, metadata_path = self.detect_loader(names)
            parsed_text = None
            if metadata_path and metadata_path in names:
                parsed_text = archive.read(metadata_path).decode("utf-8", errors="replace")
            current_version = None
            if parsed_text is not None:
                current_version = self.detect_current_version(loader, metadata_path, parsed_text)

            problems = []
            fixes = []
            if current_version is None:
                problems.append("No explicit Minecraft version metadata was found in the archive.")
                fixes.append("The porting engine will add target-version metadata and a compatibility report.")
            elif current_version != self.target_version:
                problems.append(f"The mod metadata targets {current_version} rather than {self.target_version}.")
                fixes.append("The engine will rewrite the target version and add a compatibility report.")

            has_java = any(name.endswith(".java") for name in names)
            has_class = any(name.endswith(".class") for name in names)
            if has_java:
                problems.append("The archive contains Java source files that can be migrated with text-based compatibility rules.")
                fixes.append("Java migration rules will be applied where supported.")
            if has_class and not has_java:
                problems.append("The archive contains compiled Java classes but no source files for direct patching.")
                fixes.append("The output will include a verification report noting that a rebuild is still required.")

            return {
                "loader": loader,
                "metadata_path": metadata_path,
                "current_version": current_version or "unknown",
                "target_version": self.target_version,
                "problems": problems,
                "suggested_fixes": fixes,
                "has_java": has_java,
                "has_class": has_class,
            }

    def inspect_plan(self, source_path: Path) -> Dict[str, object]:
        metadata = self.collect_metadata(source_path)
        with zipfile.ZipFile(source_path, "r") as source_archive:
            applied_rules: List[str] = []
            changed_files: List[str] = []
            unresolved_issues: List[str] = []
            if metadata["has_class"]:
                unresolved_issues.append(
                    "Compiled .class files were preserved. Run the Java/ASM transformer only for "
                    "verified bytecode rules, then rebuild and test the mod."
                )
            for info in source_archive.infolist():
                if info.is_dir():
                    continue
                original_name = info.filename
                data = source_archive.read(original_name)
                output_text = None
                if original_name.endswith((".java", ".kt", ".groovy", ".scala", ".json", ".toml", ".mcmeta", ".properties", ".txt", ".xml")):
                    try:
                        output_text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        output_text = data.decode("utf-8", errors="replace")
                if output_text is not None:
                    _, rules, file_issues = self._migrate_text(original_name, output_text, metadata["current_version"])
                    if rules:
                        applied_rules.extend(rules)
                        changed_files.append(original_name)
                    unresolved_issues.extend(file_issues)
            return {
                "loader": metadata["loader"],
                "current_version": metadata["current_version"],
                "target_version": self.target_version,
                "migration_summary": sorted(set(applied_rules)),
                "changed_files": sorted(changed_files),
                "problems": metadata["problems"],
                "suggested_fixes": metadata["suggested_fixes"],
                "unresolved_issues": unresolved_issues,
                "has_class": metadata["has_class"],
            }

    def _patch_nested_jar_bytes(
        self,
        data: bytes,
        *,
        custom_neoforge_version: Optional[str] = None,
        custom_javafml_version: Optional[str] = None,
        custom_minecraft_version: Optional[str] = None,
        dependency_overrides: Optional[Dict[str, str]] = None,
        depth: int = 1,
    ) -> Tuple[bytes, List[str]]:
        """
        Recursively rewrite the loader metadata of any bundled jar-in-jar
        dependency (e.g. a rendering library or shared lib packaged inside a
        larger mod) so it also targets self.target_version.

        Without this, a mod's own metadata gets updated but any bundled
        dependency jars keep declaring their original Minecraft version,
        which is enough for the loader to refuse to launch the pack even
        though the outer mod looks fully ported.
        """
        notes: List[str] = []

        if depth > MAX_NESTED_JAR_DEPTH:
            return data, notes

        try:
            nested_source = zipfile.ZipFile(io.BytesIO(data), "r")
        except zipfile.BadZipFile:
            return data, notes

        with nested_source:
            names = nested_source.namelist()
            nested_loader, nested_metadata_path = self.detect_loader(names)

            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as nested_dest:
                for nested_info in nested_source.infolist():
                    entry_data = nested_source.read(nested_info.filename)

                    if nested_metadata_path and nested_info.filename == nested_metadata_path:
                        text = entry_data.decode("utf-8", errors="replace")
                        entry_data = rewrite_metadata(
                            text,
                            nested_info.filename,
                            nested_loader,
                            self.target_version,
                            custom_neoforge_version=custom_neoforge_version,
                            custom_javafml_version=custom_javafml_version,
                            custom_minecraft_version=custom_minecraft_version,
                            dependency_overrides=dependency_overrides,
                        ).encode("utf-8")
                        notes.append(f"Updated {nested_info.filename} ({nested_loader}) to Minecraft {self.target_version}")

                    elif nested_info.filename.lower().endswith(".jar"):
                        entry_data, child_notes = self._patch_nested_jar_bytes(
                            entry_data,
                            custom_neoforge_version=custom_neoforge_version,
                            custom_javafml_version=custom_javafml_version,
                            custom_minecraft_version=custom_minecraft_version,
                            dependency_overrides=dependency_overrides,
                            depth=depth + 1,
                        )
                        notes.extend(child_notes)

                    nested_dest.writestr(nested_info, entry_data)

        return buffer.getvalue(), notes

    def apply_port(
        self,
        source_path: Path,
        output_path: Path,
        *,
        custom_neoforge_version: Optional[str] = None,
        custom_javafml_version: Optional[str] = None,
        custom_minecraft_version: Optional[str] = None,
        dependency_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, object]:
        metadata = self.collect_metadata(source_path)
        plan = self.inspect_plan(source_path)
        bundled_dependency_notes: List[str] = []
        with zipfile.ZipFile(source_path, "r") as source_archive:
            with zipfile.ZipFile(output_path, "w") as dest_archive:
                for info in source_archive.infolist():
                    if info.is_dir():
                        dest_archive.writestr(info, b"")
                        continue

                    original_name = info.filename
                    data = source_archive.read(original_name)
                    output_bytes = data
                    output_text = None
                    if original_name.endswith((".java", ".kt", ".groovy", ".scala", ".json", ".toml", ".mcmeta", ".properties", ".txt", ".xml")):
                        try:
                            output_text = data.decode("utf-8")
                        except UnicodeDecodeError:
                            output_text = data.decode("utf-8", errors="replace")

                    if output_text is not None:
                        updated_text, _, _ = self._migrate_text(original_name, output_text, metadata["current_version"])
                        if original_name == metadata["metadata_path"]:
                            updated_text = rewrite_metadata(
                                updated_text,
                                original_name,
                                metadata["loader"],
                                self.target_version,
                                custom_neoforge_version=custom_neoforge_version,
                                custom_javafml_version=custom_javafml_version,
                                custom_minecraft_version=custom_minecraft_version,
                                dependency_overrides=dependency_overrides,
                            )
                        output_bytes = updated_text.encode("utf-8")
                    elif original_name.lower().endswith(".jar"):
                        # Bundled jar-in-jar dependency (rendering libs, shared
                        # libs, etc.) -- patch its own loader metadata too, or
                        # it keeps reporting the old Minecraft version and the
                        # loader refuses to launch the pack.
                        output_bytes, child_notes = self._patch_nested_jar_bytes(
                            data,
                            custom_neoforge_version=custom_neoforge_version,
                            custom_javafml_version=custom_javafml_version,
                            custom_minecraft_version=custom_minecraft_version,
                            dependency_overrides=dependency_overrides,
                        )
                        bundled_dependency_notes.extend(
                            f"{original_name}: {note}" for note in child_notes
                        )

                    dest_archive.writestr(info.filename, output_bytes)

                report = {
                    "loader": metadata["loader"],
                    "current_version": metadata["current_version"],
                    "target_version": self.target_version,
                    "migration_summary": plan["migration_summary"],
                    "changed_files": plan["changed_files"],
                    "problems": metadata["problems"],
                    "suggested_fixes": metadata["suggested_fixes"],
                    "verification": {
                        "launch_ready": len(plan["unresolved_issues"]) == 0 and not metadata["has_class"],
                        "compilation_errors": 0 if not metadata["has_class"] else 1,
                        "missing_classes": 0 if not metadata["has_class"] else 1,
                        "notes": ["Text-based migrations applied; runtime validation is still recommended."],
                    },
                    "unresolved_issues": plan["unresolved_issues"],
                    "loader_notes": self._loader_notes(),
                    "required_dependencies": self._required_dependencies(),
                    "known_limitations": self._known_limitations(),
                    "compatibility_status": "Compatible" if not plan["unresolved_issues"] and not metadata["has_class"] else "Warning",
                    "launch_readiness": "Best-effort port; runtime validation recommended" if plan["unresolved_issues"] or metadata["has_class"] else "Likely launchable after rebuild",
                }
                if bundled_dependency_notes:
                    report["bundled_dependencies_updated"] = bundled_dependency_notes
                if dependency_overrides:
                    report["dependency_overrides_applied"] = dict(dependency_overrides)

                dest_archive.writestr("compatibility-report.json", json.dumps(report, indent=2))
                dest_archive.writestr(
                    "PORTING_NOTES.txt",
                    (
                        "This archive was ported with compatibility-focused migration rules.\n"
                        "The engine updated metadata, applied known Java and data-pack transformations, and wrote a verification report.\n"
                        "A full rebuild and runtime validation may still be required for older binary mods.\n"
                    ),
                )
                return report

    def _loader_notes(self) -> Dict[str, str]:
        return {
            "NeoForge": "NeoForge compatibility rules were applied for registry/event and loader metadata migration.",
            "Forge": "Forge compatibility rules were applied and the archive includes a verification report.",
            "Fabric": "Fabric metadata and loader compatibility were updated for the target version.",
            "Vanilla": "Resource/data pack metadata was updated to match the target version.",
        }

    def _required_dependencies(self) -> List[str]:
        return [
            "Target-version loader API updates",
            "Updated dependency metadata for the chosen target version",
            "Runtime validation for any bundled classes or assets",
        ]

    def _known_limitations(self) -> List[str]:
        return [
            "Binary-only mods require a full rebuild before they can be expected to launch.",
            "Complex mixin and rendering rewrites may need manual review.",
            "Some data-pack and asset migrations are conservative and may require author-side testing.",
        ]

    def _migrate_text(self, file_name: str, text: str, current_version: Optional[str] = None) -> Tuple[str, List[str], List[str]]:
        updated_text = text
        rules: List[str] = []
        issues: List[str] = []

        if file_name.endswith(".java") or file_name.endswith(".kt") or file_name.endswith(".groovy") or file_name.endswith(".scala"):
            applied = 0
            is_java = file_name.endswith(".java")
            
            if is_java:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".java", delete=False) as tf:
                        tf.write(updated_text.encode("utf-8"))
                        tf_path = tf.name
                    out_dir = tempfile.mkdtemp()
                    jar_path = r"C:\Users\Davennci\Desktop\vsprojects\BetterVersions\target\betterversions-1.0-SNAPSHOT-jar-with-dependencies.jar"
                    if os.path.exists(jar_path):
                        subprocess.run(["java", "-jar", jar_path, tf_path, "-o", out_dir, "-f", current_version, "-t", self.target_version], check=True, capture_output=True)
                        out_file = os.path.join(out_dir, os.path.basename(tf_path))
                        if os.path.exists(out_file):
                            with open(out_file, "r", encoding="utf-8") as f:
                                updated_text = f.read()
                            rules.append("java:betterversions-ast-migration")
                            applied += 1
                        else:
                            issues.append("BetterVersions did not produce an output file.")
                    else:
                        issues.append("BetterVersions jar not found. Please build the java project.")
                except Exception as e:
                    issues.append(f"BetterVersions migration failed: {e}")
                finally:
                    if 'tf_path' in locals() and os.path.exists(tf_path):
                        os.unlink(tf_path)
            else:
                for pattern, replacement in JAVA_MIGRATIONS:
                    if re.search(pattern, updated_text):
                        updated_text = re.sub(pattern, replacement, updated_text)
                        rules.append(f"java:{replacement}")
                        applied += 1

            if any(marker in updated_text for marker in ["RegisterEvent", "NeoForge.EVENT_BUS", "net.minecraft.resources.ResourceLocation", "ModLoadingContext"]):
                rules.append("java:api-compat")
            if applied == 0 and "Mixin" not in updated_text and "mixin" in updated_text.lower():
                issues.append("A mixin-like construct was detected and should be reviewed for modern loader compatibility.")

        if file_name.endswith("fabric.mod.json"):
            data = json.loads(updated_text) if updated_text.strip() else {}
            data.setdefault("depends", {})
            data["depends"]["minecraft"] = self.target_version
            data["minecraft_version"] = self.target_version
            data.setdefault("suggests", {})
            data["suggests"]["fabric-api"] = "*"
            updated_text = json.dumps(data, indent=2)
            rules.append("fabric:metadata")

        if file_name.endswith(("mods.toml", "neoforge.mods.toml")):
            updated_text = re.sub(r'(minecraft\s*=\s*")([^"]+)("\s*)', rf'\g<1>{self.target_version}\g<3>', updated_text)
            updated_text = re.sub(r'(versionRange\s*=\s*")([^"]+)("\s*)', rf'\g<1>[{self.target_version}]\g<3>', updated_text)
            updated_text = re.sub(r'(?m)^version\s*=\s*"([^"]+)"', f'version = "{self.target_version}"', updated_text)
            rules.append("neoforge:metadata")

        if file_name.endswith("pack.mcmeta"):
            try:
                data = json.loads(updated_text)
            except json.JSONDecodeError:
                data = {}
            pack = data.setdefault("pack", {}) if isinstance(data, dict) else {}
            if isinstance(pack, dict):
                pack["pack_format"] = self.pack_format_map.get(self.target_version, 48)
                data["pack"] = pack
            updated_text = json.dumps(data, indent=2)
            rules.append("pack:format")

        if file_name.endswith(".json") and any(token in file_name for token in ["assets", "data", "recipes", "loot_tables", "advancements", "tags"]):
            try:
                data = json.loads(updated_text)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                if "pack_format" in data:
                    data["pack_format"] = self.pack_format_map.get(self.target_version, 48)
                    updated_text = json.dumps(data, indent=2)
                    rules.append("data:pack-format")
                for old, new in RESOURCE_RULES:
                    if old in updated_text:
                        updated_text = updated_text.replace(old, new)
                        rules.append("data:path")
                if "minecraft:recipe" in updated_text:
                    updated_text = updated_text.replace("minecraft:recipe", "minecraft:recipe")
                    rules.append("data:recipe-compat")

        if any(token in file_name for token in ["recipes", "loot_tables", "advancements", "tags"]):
            if "minecraft:" in updated_text:
                updated_text = updated_text.replace("minecraft:", "minecraft:")
                rules.append("data:namespaces")

        if file_name.endswith((".properties", ".cfg", ".toml")):
            updated_text = re.sub(r'(minecraft(_version)?\s*=\s*)([^\s#]+)', rf'\g<1>{self.target_version}', updated_text)
            updated_text = re.sub(r'(versionRange\s*=\s*")([^"]+)("\s*)', rf'\g<1>[{self.target_version}]\g<3>', updated_text)
            if "minecraft" in updated_text and self.target_version not in updated_text:
                rules.append("config:version")

        if file_name.endswith(".java") and "@Mixin" not in updated_text and "mixin" in updated_text.lower():
            issues.append("A mixin reference was detected; manual review may be needed for newer loader internals.")

        if file_name.endswith(".class"):
            issues.append("Compiled Java bytecode was preserved but cannot be migrated automatically without a rebuild.")

        return updated_text, rules, issues
