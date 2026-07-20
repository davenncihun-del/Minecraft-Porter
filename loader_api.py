from __future__ import annotations
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

@dataclass
class ApiSignature:
    owner: str
    member: str
    descriptor: str
    kind: str

@dataclass
class ApiDiffEntry:
    kind: str
    source: str
    target: str
    owner: str
    member: str
    descriptor: str
    reason: str

class LoaderApiDiff:
    def __init__(self, javap_executable: str = "javap"):
        # Callers should pass an absolute path resolved via
        # runtime.java_manager.JavaManager.javap_for(target_version, loader)
        # rather than relying on this "javap" PATH default, since different
        # Minecraft versions require different JDKs (8 vs 17 vs 21) to
        # produce accurate signatures for their compiled classes.
        self.javap_executable = javap_executable

    def jar_signatures(self, jar_path: Path) -> List[ApiSignature]:
        classes = self._list_classes(jar_path)
        signatures: List[ApiSignature] = []
        for clazz in classes:
            output = self._run_javap(jar_path, clazz)
            signatures.extend(self._parse_javap_output(clazz, output))
        return signatures

    def diff(self, source_jar: Path, target_jar: Path) -> List[ApiDiffEntry]:
        source_signatures = self.jar_signatures(source_jar)
        target_signatures = self.jar_signatures(target_jar)
        target_set = {(sig.owner, sig.member, sig.descriptor, sig.kind) for sig in target_signatures}
        diff: List[ApiDiffEntry] = []
        for sig in source_signatures:
            if (sig.owner, sig.member, sig.descriptor, sig.kind) not in target_set:
                diff.append(ApiDiffEntry(
                    kind="signature_removed",
                    source=f"{sig.owner}.{sig.member}{sig.descriptor}",
                    target="<missing>",
                    owner=sig.owner,
                    member=sig.member,
                    descriptor=sig.descriptor,
                    reason=f"{sig.kind.capitalize()} signature {sig.member}{sig.descriptor} in {sig.owner} is not present in target loader jar.",
                ))
        return diff

    def _list_classes(self, jar_path: Path) -> List[str]:
        classes: List[str] = []
        with zipfile.ZipFile(jar_path, "r") as archive:
            for name in archive.namelist():
                if name.endswith(".class") and "$" not in name:
                    classes.append(name[:-6].replace("/", "."))
        return classes

    def _run_javap(self, jar_path: Path, class_name: str) -> str:
        process = subprocess.run(
            [self.javap_executable, "-classpath", str(jar_path), "-public", class_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return process.stdout

    def _parse_javap_output(self, owner: str, output: str) -> List[ApiSignature]:
        signatures: List[ApiSignature] = []
        current_owner = owner
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("public") or stripped.startswith("protected"):
                if "(" in stripped and ")" in stripped:
                    member = stripped.split("(", 1)[0].split()[-1]
                    descriptor = stripped[stripped.index("(") : stripped.index(")") + 1]
                    kind = "method"
                    signatures.append(ApiSignature(owner=current_owner, member=member, descriptor=descriptor, kind=kind))
                elif ";" in stripped:
                    parts = stripped.split()
                    if parts:
                        member = parts[-1].rstrip(";")
                        signatures.append(ApiSignature(owner=current_owner, member=member, descriptor="", kind="field"))
        return signatures
