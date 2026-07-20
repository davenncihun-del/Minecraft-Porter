import zipfile
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple

try:
    import tomllib
except ImportError:
    import tomli as tomllib # Fallback for older Python versions

# We ignore these because every mod has them; they don't make a mod "Stage 3"
CORE_DEPENDENCIES = {"minecraft", "forge", "neoforge", "javafml", "fabricloader", "fabric", "fabric-api"}

class ModInspector:
    def __init__(self, archive_path: Path, target_version: str):
        self.archive_path = archive_path
        self.target_version = target_version

    def _detect_loader(self, namelist: List[str]) -> Tuple[str, str]:
        """Finds the metadata file and identifies the mod loader."""
        lower_names = [n.lower() for n in namelist]
        if "fabric.mod.json" in lower_names:
            return "Fabric", namelist[lower_names.index("fabric.mod.json")]
        if "meta-inf/neoforge.mods.toml" in lower_names:
            return "NeoForge", namelist[lower_names.index("meta-inf/neoforge.mods.toml")]
        if "meta-inf/mods.toml" in lower_names:
            return "Forge", namelist[lower_names.index("meta-inf/mods.toml")]
        if "pack.mcmeta" in lower_names:
            return "Vanilla", namelist[lower_names.index("pack.mcmeta")]
        return "Unknown", ""

    def _extract_dependencies(self, loader: str, metadata_text: str) -> List[str]:
        """Extracts third-party dependencies to determine if Stage 3 is needed."""
        deps = []
        if loader == "Fabric":
            try:
                data = json.loads(metadata_text)
                depends = data.get("depends", {})
                if isinstance(depends, dict):
                    deps = list(depends.keys())
            except:
                pass
        elif loader in {"Forge", "NeoForge"}:
            try:
                parsed = tomllib.loads(metadata_text)
                dependencies = parsed.get("dependencies", {})
                if isinstance(dependencies, dict):
                    for table_id, entries in dependencies.items():
                        for entry in (entries if isinstance(entries, list) else [entries]):
                            if isinstance(entry, dict) and entry.get("modId"):
                                deps.append(entry.get("modId"))
            except:
                pass
        
        # Filter out the core Minecraft/Loader dependencies
        return [d for d in deps if d.lower() not in CORE_DEPENDENCIES]

    def _get_current_version(self, loader: str, text: str) -> str:
        """Quickly grabs the current targeted Minecraft version."""
        if loader == "Fabric":
            try:
                data = json.loads(text)
                return str(data.get("depends", {}).get("minecraft", "Unknown"))
            except:
                return "Unknown"
        elif loader in {"Forge", "NeoForge"}:
            match = re.search(r'minecraft\s*=\s*"([^"]+)"', text)
            if not match:
                match = re.search(r'versionRange\s*=\s*"([^"]+)"', text) # Fallback
            return match.group(1).strip('[]()') if match else "Unknown"
        return "Unknown"

    def inspect(self) -> Dict[str, Any]:
        """Analyzes the archive and assigns a Complexity Stage and Action Plan."""
        has_java = False
        has_class = False
        third_party_deps = []
        loader = "Unknown"
        metadata_path = ""
        current_version = "Unknown"

        with zipfile.ZipFile(self.archive_path, 'r') as archive:
            namelist = archive.namelist()
            loader, metadata_path = self._detect_loader(namelist)
            
            # Check for code files
            has_java = any(name.endswith(".java") for name in namelist)
            has_class = any(name.endswith(".class") for name in namelist)
            
            # Parse metadata
            if metadata_path:
                text = archive.read(metadata_path).decode("utf-8", errors="replace")
                current_version = self._get_current_version(loader, text)
                third_party_deps = self._extract_dependencies(loader, text)

        # --- EVALUATE THE STAGE AND REASONS ---
        stage = 1
        reasons = []

        # Stage 1 Checks (Baseline)
        if current_version != self.target_version:
            reasons.append(f"Old Metadata: Currently targets {current_version}, needs {self.target_version}.")
        else:
            reasons.append("Metadata Verification: Ensuring structural integrity for target version.")

        # Stage 2 Checks (Code Changes)
        if has_java or has_class:
            stage = 2
            reasons.append("API Outdated: Contains Java source or compiled bytecode requiring modern Minecraft API migrations.")

        # Stage 3 Checks (Complex Dependencies)
        if len(third_party_deps) > 0:
            stage = 3
            reasons.append(f"Complex Dependencies Detected: Requires batch resolution for {len(third_party_deps)} external libraries (e.g., {third_party_deps[0]}).")

        return {
            "loader": loader,
            "current_version": current_version,
            "target_version": self.target_version,
            "complexity_stage": stage,
            "update_reasons": reasons,
            "discovered_dependencies": third_party_deps,
            "has_source_code": has_java or has_class,
            "metadata_path": metadata_path
        }