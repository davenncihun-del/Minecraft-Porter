"""
runtime.java_manager
=====================

Resolves which installed JDK to use for a given Minecraft version + loader,
instead of relying on a single system `java` on PATH or a global JAVA_HOME.

Two data files back this module (both live next to this file):

- version_rules.json  -- which Java major version a Minecraft version/loader
                          combination requires (e.g. "1.12.2" -> 8).
- detected_jdks.json   -- where each installed JDK major version actually
                          lives on this machine. Can be hand-edited/pre-seeded
                          (as-is, right now, with your three Adoptium installs)
                          or populated automatically via discovery.

Nothing in here touches JAVA_HOME, PATH, or requires the user to switch an
active JDK. Callers ask for "the java 26.1.2 needs" and get back an absolute
path to that specific JDK's executable.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

RUNTIME_DIR = Path(__file__).resolve().parent
VERSION_RULES_PATH = RUNTIME_DIR / "version_rules.json"
DETECTED_JDKS_PATH = RUNTIME_DIR / "detected_jdks.json"

IS_WINDOWS = platform.system() == "Windows"
_EXE = ".exe" if IS_WINDOWS else ""

# Fallback discovery roots, only consulted when detected_jdks.json has no
# entry for a required major version yet. This never overrides a configured
# path and never picks "whatever java is available" -- it only looks for an
# install matching the *exact* major version a rule asked for, and if found,
# writes it back into detected_jdks.json so the scan only ever happens once.
DEFAULT_SEARCH_ROOTS = [
    r"C:\Program Files\Eclipse Adoptium",
    r"C:\Program Files\Java",
    r"C:\Program Files\Zulu",
    r"C:\Program Files\Amazon Corretto",
    r"C:\Program Files (x86)\Java",
    r"C:\Program Files (x86)\Eclipse Adoptium",
    "/usr/lib/jvm",
    "/opt/java",
    "/Library/Java/JavaVirtualMachines",
    str(Path.home() / ".sdkman" / "candidates" / "java"),
]


class JavaResolutionError(RuntimeError):
    """Raised when no JDK matching a required major version can be found."""


@dataclass
class JdkInstall:
    major: int
    home: str
    java: str
    javac: Optional[str] = None
    javap: Optional[str] = None
    source: str = "configured"  # "configured" (hand-edited/pre-seeded) or "detected"
    detected_at: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _version_tuple(version: str) -> tuple:
    parts = []
    for part in re.split(r"[.\-]", version or ""):
        digits = re.match(r"(\d+)", part)
        parts.append(int(digits.group(1)) if digits else 0)
    return tuple(parts)


def probe_major_version(java_executable: Path) -> Optional[int]:
    """Run `java -version` against a specific executable and parse its major version."""
    try:
        process = subprocess.run(
            [str(java_executable), "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (process.stdout or "") + (process.stderr or "")
    match = re.search(r'version "(\d+)(?:\.(\d+))?', output)
    if not match:
        return None
    major = int(match.group(1))
    if major == 1 and match.group(2):
        # Legacy "1.8.0_xxx" style versioning used pre-Java 9.
        return int(match.group(2))
    return major


class JavaVersionRules:
    """Loads version_rules.json and resolves the required Java major version."""

    def __init__(self, path: Path = VERSION_RULES_PATH):
        self.path = path
        self.rules: List[Dict[str, object]] = self._load()

    def _load(self) -> List[Dict[str, object]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("rules", [])

    def required_major_for(self, minecraft_version: str, loader: Optional[str] = None) -> int:
        target = _version_tuple(minecraft_version)
        candidates = []
        for rule in self.rules:
            lo, hi = rule.get("min_version"), rule.get("max_version")
            if lo and target < _version_tuple(lo):
                continue
            if hi and target > _version_tuple(hi):
                continue
            loaders = rule.get("loaders")
            if loaders and loader and loader not in loaders:
                continue
            candidates.append(rule)

        if not candidates:
            # Conservative fallback: assume the newest documented requirement
            # rather than silently guessing something older.
            return max((int(r["java_major"]) for r in self.rules), default=21)

        # Prefer a loader-specific rule over a generic one when both match.
        candidates.sort(key=lambda r: 0 if r.get("loaders") else 1)
        return int(candidates[0]["java_major"])


class JdkRegistry:
    """Loads/saves detected_jdks.json and performs one-time discovery."""

    def __init__(self, path: Path = DETECTED_JDKS_PATH):
        self.path = path
        self._installs: Dict[str, JdkInstall] = self._load()

    def _load(self) -> Dict[str, JdkInstall]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        installs: Dict[str, JdkInstall] = {}
        for major, entry in raw.items():
            if isinstance(entry, str):
                # Back-compat with a bare `{"8": "C:\\...\\java.exe"}` mapping.
                java_path = Path(entry)
                installs[str(major)] = JdkInstall(
                    major=int(major),
                    home=str(java_path.parent.parent),
                    java=str(java_path),
                    javac=str(java_path.with_name(f"javac{_EXE}")),
                    javap=str(java_path.with_name(f"javap{_EXE}")),
                )
            elif isinstance(entry, dict):
                installs[str(major)] = JdkInstall(**entry)
        return installs

    def save(self) -> None:
        data = {major: install.to_dict() for major, install in self._installs.items()}
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, major: int) -> Optional[JdkInstall]:
        return self._installs.get(str(major))

    def register(self, install: JdkInstall, persist: bool = True) -> None:
        self._installs[str(install.major)] = install
        if persist:
            self.save()

    def all(self) -> Dict[str, JdkInstall]:
        return dict(self._installs)

    def discover(self, major: int, persist: bool = True) -> Optional[JdkInstall]:
        """
        Best-effort scan of common install locations for one specific major
        version. Only ever called when detected_jdks.json has no entry for
        that major yet -- a configured path always wins, and this never
        substitutes "whatever java happens to be on PATH".
        """
        existing = self.get(major)
        if existing:
            return existing

        for root in DEFAULT_SEARCH_ROOTS:
            root_path = Path(root)
            if not root_path.is_dir():
                continue
            for candidate in root_path.glob("*"):
                java_path = candidate / "bin" / f"java{_EXE}"
                if not java_path.exists():
                    continue
                detected_major = probe_major_version(java_path)
                if detected_major == major:
                    install = JdkInstall(
                        major=major,
                        home=str(candidate),
                        java=str(java_path),
                        javac=str(candidate / "bin" / f"javac{_EXE}"),
                        javap=str(candidate / "bin" / f"javap{_EXE}"),
                        source="detected",
                        detected_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self.register(install, persist=persist)
                    return install
        return None


class JavaManager:
    """Public entry point: resolve the right java/javac/javap for a port job."""

    def __init__(self, rules: Optional[JavaVersionRules] = None, registry: Optional[JdkRegistry] = None):
        self.rules = rules or JavaVersionRules()
        self.registry = registry or JdkRegistry()

    def required_major_for(self, minecraft_version: str, loader: Optional[str] = None) -> int:
        return self.rules.required_major_for(minecraft_version, loader)

    def resolve(self, minecraft_version: str, loader: Optional[str] = None) -> JdkInstall:
        major = self.required_major_for(minecraft_version, loader)
        install = self.registry.get(major) or self.registry.discover(major)
        if not install or not Path(install.java).exists():
            raise JavaResolutionError(
                f"Minecraft {minecraft_version} ({loader or 'unknown loader'}) requires Java {major}, "
                f"but no matching JDK is registered in {DETECTED_JDKS_PATH.name} or found in common "
                f"install locations. Add its path under key \"{major}\" in {self.registry.path}."
            )
        return install

    def java_for(self, minecraft_version: str, loader: Optional[str] = None) -> str:
        return self.resolve(minecraft_version, loader).java

    def javac_for(self, minecraft_version: str, loader: Optional[str] = None) -> str:
        install = self.resolve(minecraft_version, loader)
        return install.javac or str(Path(install.java).with_name(f"javac{_EXE}"))

    def javap_for(self, minecraft_version: str, loader: Optional[str] = None) -> str:
        install = self.resolve(minecraft_version, loader)
        return install.javap or str(Path(install.java).with_name(f"javap{_EXE}"))
