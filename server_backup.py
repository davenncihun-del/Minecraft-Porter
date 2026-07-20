import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import tomllib
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from analyzer import ArchiveAnalyzer
from loader_api import LoaderApiDiff
from mappings import MappingSet, MappingDiff
from planner import MigrationPlanner
from report import ReportBuilder
from transformer import Transformer
from validator import Validator

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_SIZE = 20 * 1024 * 1024
SUPPORTED_VERSIONS = [
    "26.2",
    "26.1.2",
    "26.1.1",
    "26.1",
    "1.21.11",
    "1.21.10",
    "1.21.9",
    "1.21.8",
    "1.21.7",
    "1.21.6",
    "1.21.5",
    "1.21.1",
    "1.20.6",
    "1.20.4",
    "1.20.1",
    "1.19.4",
    "1.19.2",
    "1.18.2",
    "1.17.1",
    "1.16.5",
    "1.16.4",
    "1.15.2",
    "1.14.4",
    "1.13.2",
    "1.12.2",
    "1.12.1",
    "1.12",
]

COMPATIBILITY_PROFILES = {
    "26.2": {
        "loader_notes": {
            "NeoForge": "Target NeoForge 26.2+ and verify all APIs against the latest registry and rendering changes.",
            "Fabric": "Use the latest Fabric API and loader that supports the 26.2 toolchain.",
            "Forge": "Forge support is best-effort; expect a manual review for registry and rendering changes.",
        },
        "dependencies": ["NeoForge/Forge API updates", "Fabric API update", "Latest mappings and rendering libraries"],
        "limitations": ["Some older mixin-based mods may need author-side adjustments."]
    },
    "1.21.11": {
        "loader_notes": {
            "NeoForge": "NeoForge 21.11+ recommended; review registry and data generation changes.",
            "Fabric": "Fabric loader 0.16+ and API updates recommended.",
            "Forge": "Forge 53+ support is experimental; verify mixins and rendering hooks."
        },
        "dependencies": ["Updated NeoForge/Fabric API", "Latest mappings", "Renderer and registry compatibility checks"],
        "limitations": ["This is a compatibility-focused port; deeper code rewrites may still be necessary."]
    },
    "1.21.10": {
        "loader_notes": {
            "NeoForge": "NeoForge 21.10+ recommended.",
            "Fabric": "Fabric loader 0.16+ recommended.",
            "Forge": "Forge 53+ recommended; manual review still advised."
        },
        "dependencies": ["Updated mappings", "Modern rendering hooks", "Dependency refresh"],
        "limitations": ["Known limitations are documented in the compatibility report."]
    },
    "1.21.9": {
        "loader_notes": {
            "NeoForge": "NeoForge 21.9+ recommended.",
            "Fabric": "Fabric loader 0.16+ recommended.",
            "Forge": "Forge 53+ recommended."
        },
        "dependencies": ["Updated mappings", "Renderer compat patch", "Registry compatibility update"],
        "limitations": ["Some mods may require a full code migration to newer APIs."]
    },
    "1.21.8": {
        "loader_notes": {
            "NeoForge": "NeoForge 21.8+ recommended.",
            "Fabric": "Fabric loader 0.16+ recommended.",
            "Forge": "Forge 53+ recommended."
        },
        "dependencies": ["Updated mappings", "Mixin compatibility review", "Renderer compatibility patch"],
        "limitations": ["No guarantee of full gameplay parity until runtime validation is completed."]
    },
    "1.21.7": {
        "loader_notes": {
            "NeoForge": "NeoForge 21.7+ recommended.",
            "Fabric": "Fabric loader 0.16+ recommended.",
            "Forge": "Forge 53+ recommended."
        },
        "dependencies": ["Updated mappings", "Dependency refresh", "Registry compatibility patch"],
        "limitations": ["Potential shader or rendering regressions should be tested in-game."]
    },
    "1.21.6": {
        "loader_notes": {
            "NeoForge": "NeoForge 21.6+ recommended.",
            "Fabric": "Fabric loader 0.16+ recommended.",
            "Forge": "Forge 53+ recommended."
        },
        "dependencies": ["Updated mappings", "Fabric API refresh", "Modern loader dependencies"],
        "limitations": ["Runtime validation is still needed for gameplay-critical systems."]
    },
    "1.21.5": {
        "loader_notes": {
            "NeoForge": "NeoForge 21.5+ recommended; review registry and rendering hooks.",
            "Fabric": "Fabric loader 0.16+ and API updates recommended.",
            "Forge": "Forge 53+ recommended; manual review still advised."
        },
        "dependencies": ["NeoForge/Fabric API update", "Updated mappings", "Renderer compatibility update"],
        "limitations": ["Feature parity may still require author-side code migration."]
    },
    "1.20.1": {
        "loader_notes": {
            "NeoForge": "NeoForge 1.20.1+ recommended.",
            "Fabric": "Fabric loader 0.16+ recommended.",
            "Forge": "Forge 47+ recommended."
        },
        "dependencies": ["Backport-safe dependencies", "Compatibility checks for mixins"],
        "limitations": ["Older versions may lack some newer rendering and registry features."]
    },
    "1.16.5": {
        "loader_notes": {
            "NeoForge": "NeoForge 1.16.5 recommended.",
            "Fabric": "Fabric loader 0.14+ recommended.",
            "Forge": "Forge 36.2+ recommended."
        },
        "dependencies": ["Legacy mappings", "Backport-safe renderer updates"],
        "limitations": ["Some newer APIs are not available on this target."]
    },
    "1.15.2": {
        "loader_notes": {
            "NeoForge": "NeoForge 1.15.2 recommended.",
            "Fabric": "Fabric loader 0.14+ recommended.",
            "Forge": "Forge 31+ recommended."
        },
        "dependencies": ["Legacy API compatibility patch"],
        "limitations": ["This is a conservative backport and may miss newer features."]
    },
    "1.14.4": {
        "loader_notes": {
            "NeoForge": "NeoForge 1.14.4 recommended.",
            "Fabric": "Fabric loader 0.14+ recommended.",
            "Forge": "Forge 28+ recommended."
        },
        "dependencies": ["Legacy renderer compatibility"],
        "limitations": ["Only the most critical compatibility fixes are included."]
    },
    "1.13.2": {
        "loader_notes": {
            "NeoForge": "NeoForge 1.13.2 recommended.",
            "Fabric": "Fabric support is limited on this line.",
            "Forge": "Forge 25+ recommended."
        },
        "dependencies": ["Legacy registry updates"],
        "limitations": ["This target is best used as a conservative backport only."]
    },
    "1.12.2": {
        "loader_notes": {
            "NeoForge": "NeoForge 1.12.2 recommended.",
            "Fabric": "Fabric is not commonly used for this version line.",
            "Forge": "Forge 14.23+ recommended."
        },
        "dependencies": ["Legacy registry and event compatibility"],
        "limitations": ["Many modern APIs are unavailable on 1.12.2."]
    },
}

app = FastAPI(title="Minecraft Compatibility Updater")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InspectRequest(BaseModel):
    file_id: str
    loader: Optional[str] = None
    minecraft_version: Optional[str] = None


class UpdateRequest(BaseModel):
    file_id: str
    loader: Optional[str] = None
    target_version: Optional[str] = None


class StoredFile:
    def __init__(self, file_id: str, path: Path, original_name: str, size: int):
        self.file_id = file_id
        self.path = path
        self.original_name = original_name
        self.size = size
        self.created_at = datetime.utcnow()


STORED_FILES: dict[str, StoredFile] = {}


def version_tuple(version: str):
    parts = []
    for part in version.split('.'):
        digits = re.match(r"(\d+)", part)
        parts.append(int(digits.group(1)) if digits else 0)
    return tuple(parts)


def cleanup_old_files():
    cutoff = datetime.utcnow() - timedelta(hours=1)
    for file_id, stored in list(STORED_FILES.items()):
        if stored.created_at < cutoff:
            try:
                stored.path.unlink(missing_ok=True)
            except Exception:
                pass
            STORED_FILES.pop(file_id, None)


def store_uploaded_file(file: UploadFile) -> StoredFile:
    cleanup_old_files()
    if file.size and file.size > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 20MB limit.")

    ext = Path(file.filename or "file.bin").suffix.lower()
    if ext not in {".jar", ".zip"}:
        raise HTTPException(status_code=400, detail="Only .jar and .zip files are supported.")

    file_id = uuid.uuid4().hex
    destination = UPLOAD_DIR / f"{file_id}{ext}"
    with destination.open("wb") as handle:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)

    stored = StoredFile(file_id=file_id, path=destination, original_name=file.filename or "uploaded.bin", size=destination.stat().st_size)
    STORED_FILES[file_id] = stored
    return stored


def load_mapping_set(version: str) -> Optional[MappingSet]:
    mapping_path = BASE_DIR / "mappings" / f"{version}-intermediary.tiny"
    if mapping_path.exists():
        try:
            return MappingSet.from_tiny(mapping_path)
        except Exception:
            return None
    return None


def load_target_jar(version: str) -> Optional[Path]:
    jar_path = BASE_DIR / "jars" / f"minecraft-{version}.jar"
    return jar_path if jar_path.exists() else None


def read_text_from_zip(path: Path, entry_name: str) -> Optional[str]:
    with zipfile.ZipFile(path, "r") as archive:
        if entry_name not in archive.namelist():
            return None
        return archive.read(entry_name).decode("utf-8", errors="replace")


def detect_loader_and_metadata(path: Path):
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()

    def find_entry(candidate_names):
        lower_names = [name.lower() for name in names]
        for candidate in candidate_names:
            for idx, name in enumerate(lower_names):
                if name.endswith(candidate.lower()):
                    return names[idx]
        return None

    fabric_entry = find_entry(["fabric.mod.json"])
    if fabric_entry:
        return "Fabric", fabric_entry

    forge_entry = find_entry(["meta-inf/mods.toml"])
    if forge_entry:
        return "Forge", forge_entry

    neoforge_entry = find_entry(["meta-inf/neoforge.mods.toml"])
    if neoforge_entry:
        return "NeoForge", neoforge_entry

    vanilla_entry = find_entry(["pack.mcmeta"])
    if vanilla_entry:
        return "Vanilla", vanilla_entry

    return "Unknown", None


def extract_current_version(loader: str, metadata_path: str, text: str):
    if loader == "Fabric" or loader == "Vanilla":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
        if loader == "Fabric":
            depends = data.get("depends", {}) if isinstance(data.get("depends"), dict) else {}
            if isinstance(depends, dict):
                minecraft = depends.get("minecraft")
                if isinstance(minecraft, str) and minecraft:
                    return minecraft
            if isinstance(data.get("minecraft_version"), str):
                return data["minecraft_version"]
        if loader == "Vanilla":
            pack = data.get("pack", {}) if isinstance(data.get("pack"), dict) else {}
            for key in ["supported_minecraft_version", "minecraft_version"]:
                value = pack.get(key)
                if isinstance(value, str) and value:
                    return value
            for key in ["supported_minecraft_version", "minecraft_version"]:
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    if loader in {"Forge", "NeoForge"}:
        try:
            parsed = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            for key in ["minecraft", "minecraft_version"]:
                value = parsed.get(key)
                if isinstance(value, str) and value:
                    return value
            # Some Forge/NeoForge toml files nest the version under a mod entry or dependency table
            for section in [parsed.get("mods"), parsed.get("dependencies")]:
                if isinstance(section, dict):
                    for subvalue in section.values():
                        if isinstance(subvalue, dict):
                            for key in ["minecraft", "minecraft_version"]:
                                value = subvalue.get(key)
                                if isinstance(value, str) and value:
                                    return value
        for pattern in [r'minecraft\s*=\s*"([^"]+)"', r'minecraft_version\s*=\s*"([^"]+)"']:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    return None


def determine_problems(loader: str, current_version: Optional[str], metadata_path: Optional[str]):
    problems = []
    fixes = []
    if not current_version:
        problems.append("Missing Minecraft version metadata.")
        fixes.append("Add a Minecraft version entry to the metadata file.")
    elif current_version not in SUPPORTED_VERSIONS:
        problems.append("The metadata points to an older or unsupported Minecraft version.")
        fixes.append("Update the metadata to a supported version from the compatibility list.")
    if loader == "Fabric" and metadata_path == "fabric.mod.json":
        problems.append("Dependency metadata may be incomplete.")
        fixes.append("Add dependency declarations for Minecraft and required libraries.")
    if loader in {"Forge", "NeoForge"} and metadata_path:
        problems.append("The mod may still need code-level changes for newer APIs.")
        fixes.append("Review mod code and dependencies after metadata is updated.")
    if not problems:
        problems.append("No critical compatibility issues were detected.")
        fixes.append("The file is ready for a compatibility-safe metadata update.")
    return problems, fixes


def resolve_target_version(current_version: Optional[str], requested: Optional[str]):
    if requested and requested != "auto":
        return requested
    if not current_version:
        return SUPPORTED_VERSIONS[0]

    for version in SUPPORTED_VERSIONS:
        if version_tuple(version) <= version_tuple(current_version):
            return version
    return SUPPORTED_VERSIONS[-1]


def build_compatibility_report(loader: str, current_version: Optional[str], target_version: str, metadata_path: Optional[str], problems: list[str], fixes: list[str]):
    profile = COMPATIBILITY_PROFILES.get(target_version, {
        "loader_notes": {loader: "Targeted compatibility update generated by the porter."},
        "dependencies": ["Refresh loader-specific dependencies"],
        "limitations": ["Manual validation recommended."]
    })
    return {
        "target_version": target_version,
        "current_version": current_version or "unknown",
        "loader": loader,
        "metadata_path": metadata_path,
        "required_dependencies": profile.get("dependencies", []),
        "loader_notes": profile.get("loader_notes", {}),
        "known_limitations": profile.get("limitations", []),
        "problems": problems,
        "suggested_fixes": fixes,
        "launch_readiness": "metadata updated; runtime validation still required",
    }


def rewrite_metadata(text: str, loader: str, target_version: str):
    """
    Updates Minecraft mod metadata for supported loaders.
    Handles:
    - Fabric fabric.mod.json
    - Forge mods.toml
    - NeoForge neoforge.mods.toml
    - Vanilla pack.mcmeta
    """

    loader_versions = {
        "Fabric": {
            "loader": "0.16.0"
        },
        "Forge": {
            "loader": "[53,)"
        },
        "NeoForge": {
            "loader": "[21.11,)"
        }
    }

    if loader == "Fabric":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text

        depends = data.get("depends", {})

        if not isinstance(depends, dict):
            depends = {}

        depends["minecraft"] = target_version
        depends["fabricloader"] = f">={loader_versions['Fabric']['loader']}"

        data["depends"] = depends
        data["minecraft_version"] = target_version

        return json.dumps(data, indent=2)


    if loader in {"Forge", "NeoForge"}:
        updated = text

        # Minecraft dependency
        updated = re.sub(
            r'(modId\s*=\s*"minecraft"[\s\S]*?versionRange\s*=\s*")([^"]+)(")',
            rf'\g<1>[{target_version}]\g<3>',
            updated
        )

        # Loader version
        updated = re.sub(
            r'(loaderVersion\s*=\s*")([^"]+)(")',
            rf'\g<1>{loader_versions[loader]["loader"]}\g<3>',
            updated
        )


        if loader == "NeoForge":

            updated = re.sub(
                r'(modId\s*=\s*"neoforge"[\s\S]*?versionRange\s*=\s*")([^"]+)(")',
                rf'\g<1>{loader_versions["NeoForge"]["loader"]}\g<3>',
                updated
            )


        if loader == "Forge":

            updated = re.sub(
                r'(modId\s*=\s*"forge"[\s\S]*?versionRange\s*=\s*")([^"]+)(")',
                rf'\g<1>{loader_versions["Forge"]["loader"]}\g<3>',
                updated
            )


        return updated



    if loader == "Vanilla":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text

        pack = data.get("pack", {})

        if not isinstance(pack, dict):
            pack = {}

        pack["supported_minecraft_version"] = target_version
        pack["minecraft_version"] = target_version

        data["pack"] = pack
        data["minecraft_version"] = target_version

        return json.dumps(data, indent=2)


    return text


def apply_update_to_archive(source_path: Path, destination_path: Path, metadata_path: str, loader: str, target_version: str, current_version: Optional[str], problems: list[str], fixes: list[str]):
    with zipfile.ZipFile(source_path, "r") as source_archive:
        with zipfile.ZipFile(destination_path, "w") as dest_archive:
            for info in source_archive.infolist():
                data = source_archive.read(info.filename)
                if info.filename == metadata_path:
                    data = rewrite_metadata(data.decode("utf-8", errors="replace"), loader, target_version).encode("utf-8")
                dest_archive.writestr(info, data)

            report = build_compatibility_report(loader, current_version, target_version, metadata_path, problems, fixes)
            dest_archive.writestr("compatibility-report.json", json.dumps(report, indent=2))
            dest_archive.writestr(
                "PORTING_NOTES.txt",
                "Compatibility-focused port generated by the updater.\n"
                "This archive includes updated metadata plus a compatibility report.\n"
                "Runtime validation and code-level migration may still be required for full gameplay support.\n"
            )


@app.get("/", response_class=HTMLResponse)
async def index():
    return (BASE_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/styles.css")
async def styles():
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")


@app.get("/app.js")
async def app_js():
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    stored = store_uploaded_file(file)

    return {
        "file_id": stored.file_id,
        "filename": stored.original_name,
        "size_bytes": stored.size,
        "message": "File uploaded successfully."
    }


@app.post("/inspect")
async def inspect_file(payload: InspectRequest):
    stored = STORED_FILES.get(payload.file_id)

    if not stored:
        raise HTTPException(
            status_code=404,
            detail="File not found."
        )

    loader, metadata_path = detect_loader_and_metadata(stored.path)

    if payload.loader and payload.loader != "auto":
        loader = payload.loader

    if metadata_path is None:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file does not contain supported metadata."
        )

    with zipfile.ZipFile(stored.path, "r") as archive:
        text = archive.read(metadata_path).decode(
            "utf-8",
            errors="replace"
        )

    current_version = extract_current_version(
        loader,
        metadata_path,
        text
    )

    problems, fixes = determine_problems(
        loader,
        current_version,
        metadata_path
    )

    target_version = resolve_target_version(
        current_version,
        payload.minecraft_version
    )

    return {
        "loader": loader,
        "current_version": current_version or "Unknown",
        "target_version": target_version,
        "problems": problems,
        "suggested_fixes": fixes,
        "metadata_path": metadata_path
    }

@app.post("/update")
async def update_file(payload: UpdateRequest):
    stored = STORED_FILES.get(payload.file_id)
    if not stored:
        raise HTTPException(status_code=404, detail="File not found.")

    loader, metadata_path = detect_loader_and_metadata(stored.path)

    if payload.loader and payload.loader != "auto":
        loader = payload.loader

    if metadata_path is None:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file does not contain supported metadata."
        )

    with zipfile.ZipFile(stored.path, "r") as archive:
        if metadata_path not in archive.namelist():
            raise HTTPException(
                status_code=400,
                detail="The required metadata file could not be found inside the archive."
            )

        original_text = archive.read(metadata_path).decode(
            "utf-8",
            errors="replace"
        )

    current_version = extract_current_version(
        loader,
        metadata_path,
        original_text
    )

    problems, fixes = determine_problems(
        loader,
        current_version,
        metadata_path
    )

    target_version = resolve_target_version(
        current_version,
        payload.target_version
    )

    analyzer = ArchiveAnalyzer()
    analysis = analyzer.analyze_archive(
        stored.path,
        target_version=target_version
    )

    source_mappings = load_mapping_set(
        analysis.current_version or "unknown"
    )

    target_mappings = load_mapping_set(
        target_version
    )

    mapping_diff = (
        MappingDiff.from_sets(
            source_mappings,
            target_mappings
        )
        if source_mappings and target_mappings
        else None
    )

    planner = MigrationPlanner(
        mapping_diff=mapping_diff,
        schema_source="vanilla-schemas"
    )

    plan = planner.plan(analysis)

    try:
        transformer = Transformer()

        file_ext = stored.path.suffix.lower()

        safe_version = target_version.replace(".", "_")

        original_stem = Path(stored.original_name).stem
        original_ext = Path(stored.original_name).suffix

        updated_archive_path = stored.path.with_name(
            f"{original_stem}-updated-{target_version}{original_ext}"
        )

        transformer.apply(
            analysis,
            plan,
            updated_archive_path
        )

        classpath_jars = []

        target_jar = load_target_jar(target_version)

        if target_jar:
            classpath_jars.append(target_jar)

        validator = Validator(
            mapping_diff=mapping_diff,
            classpath_jars=classpath_jars
        )

        validation = validator.validate(analysis)

        report_builder = ReportBuilder()

        report_bundle = report_builder.build(
            plan,
            validation,
            updated_archive_path.parent
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Update failure: {exc}"
        )

    return FileResponse(
        path=updated_archive_path,
        media_type=(
            "application/java-archive"
            if file_ext == ".jar"
            else "application/zip"
        ),
        filename=updated_archive_path.name,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
