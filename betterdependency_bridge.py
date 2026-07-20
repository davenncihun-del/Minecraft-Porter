"""
Bridge between MinecraftPorter (Python) and BetterDependency (Java CLI).

Invokes the BetterDependency CLI to resolve third-party mod dependencies
against Modrinth, then parses the structured JSON output so the porting
engine can use the resolved versions as ``dependency_overrides``.
"""
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

# Path to the compiled BetterDependency CLI fat-jar
CLI_JAR_PATH = (
    Path(__file__).resolve().parent.parent
    / "BetterDependency"
    / "target"
    / "betterdependency-cli-1.0.0-SNAPSHOT.jar"
)


def run_betterdependency_cli(
    mod_path: str,
    target_version: str,
    loader: str,
    *,
    dry_run: bool = False,
    timeout_seconds: int = 120,
) -> Dict[str, Any]:
    """
    Executes the BetterDependency CLI to resolve dependencies via Modrinth.

    Returns a dict with at minimum::

        {
            "status":  "success" | "error" | "not_available",
            "dependency_overrides": { "modId": "versionRange", ... },
            "resolutions": [ ... ],
            ...
        }

    If the CLI jar has not been built yet, returns status ``"not_available"``
    so the porter can proceed without resolution.
    """
    if not CLI_JAR_PATH.exists():
        print(
            f"[BetterDependency] CLI jar not found at {CLI_JAR_PATH}. "
            "Run 'mvn package' in the BetterDependency project to build it."
        )
        return {
            "status": "not_available",
            "message": (
                "BetterDependency CLI jar not found. "
                "Build with 'mvn package' to enable dependency resolution."
            ),
            "dependency_overrides": {},
            "resolutions": [],
            "resolved_count": 0,
            "failed_count": 0,
        }

    # Map loader names to the format BetterDependency expects
    platform_map = {
        "Fabric": "FABRIC",
        "Forge": "FORGE",
        "NeoForge": "NEOFORGE",
        "fabric": "FABRIC",
        "forge": "FORGE",
        "neoforge": "NEOFORGE",
    }
    platform = platform_map.get(loader, loader.upper())

    cmd = [
        "java", "-jar", str(CLI_JAR_PATH),
        str(mod_path),
        "-v", target_version,
        "-p", platform,
    ]
    if dry_run:
        cmd.append("--dry-run")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )

        # Log stderr (diagnostic info goes there)
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                print(f"[BetterDependency] {line}")

        # Parse the JSON from stdout
        stdout = result.stdout.strip()
        if not stdout:
            return _error_result(
                "CLI produced no output. "
                f"Exit code: {result.returncode}"
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as e:
            return _error_result(
                f"Failed to parse CLI output as JSON: {e}\n"
                f"Raw output: {stdout[:500]}"
            )

        # Validate the response has the expected structure
        if "status" not in data:
            data["status"] = "error" if result.returncode != 0 else "success"
        if "dependency_overrides" not in data:
            data["dependency_overrides"] = {}
        if "resolutions" not in data:
            data["resolutions"] = []

        return data

    except subprocess.TimeoutExpired:
        return _error_result(
            f"BetterDependency CLI timed out after {timeout_seconds}s. "
            "The Modrinth API may be slow or unreachable."
        )
    except FileNotFoundError:
        return _error_result(
            "Java runtime not found. Ensure 'java' is on PATH."
        )
    except Exception as e:
        return _error_result(f"Unexpected error running CLI: {e}")


def extract_dependency_overrides(
    cli_result: Dict[str, Any]
) -> Dict[str, str]:
    """
    Extracts the dependency_overrides map from a CLI result.

    Returns an empty dict if the CLI failed or was not available,
    so the porting engine can proceed with default behavior.
    """
    if cli_result.get("status") not in ("success",):
        return {}
    return cli_result.get("dependency_overrides", {})


def get_unresolved_dependencies(
    cli_result: Dict[str, Any]
) -> list:
    """
    Returns a list of dependencies that could not be resolved,
    for inclusion in the compatibility report.
    """
    unresolved = []
    for res in cli_result.get("resolutions", []):
        if res.get("status") != "RESOLVED":
            unresolved.append({
                "mod_id": res.get("mod_id", "unknown"),
                "status": res.get("status", "UNKNOWN"),
                "error": res.get("error", "No details available"),
            })
    return unresolved


def _error_result(message: str) -> Dict[str, Any]:
    """Constructs a standardized error result."""
    print(f"[BetterDependency] ERROR: {message}")
    return {
        "status": "error",
        "message": message,
        "dependency_overrides": {},
        "resolutions": [],
        "resolved_count": 0,
        "failed_count": 0,
    }
