"""Metadata transformations shared by the archive update endpoints."""
from __future__ import annotations
import json
import re
from typing import Mapping, Optional

def get_loader_version(loader: str, target_version: Optional[str] = None) -> str:
    versions = {
        "Fabric": ">=0.16.0",
        "Forge": "[53,)",
        "NeoForge": "[21.1,)",
    }
    return versions.get(loader, "")

def _replace_toml_value(text: str, key: str, value: str) -> str:
    pattern = rf'(?m)^(\s*{re.escape(key)}\s*=\s*)"[^"]*"'
    return re.sub(pattern, lambda match: f'{match.group(1)}"{value}"', text)

def _update_toml_dependencies(text: str, dependency_updates: Mapping[str, str]) -> str:
    sections = re.split(r'(^\[\[?.*\]\]?\s*$)', text, flags=re.MULTILINE)
    updated_sections = []
    applied_overrides = set()
    current_header = ""
    
    for section in sections:
        if re.match(r'^\[\[?.*\]\]?\s*$', section):
            current_header = section
            updated_sections.append(section)
            continue
            
        if "dependencies." in current_header:
            mod_id_match = re.search(r'modId\s*=\s*"([^"]+)"', section)
            if mod_id_match:
                mod_id = mod_id_match.group(1)
                if mod_id in dependency_updates:
                    new_range = dependency_updates[mod_id]
                    if 'versionRange' in section:
                        section = re.sub(
                            r'(versionRange\s*=\s*")[^"]*(")', 
                            rf'\g<1>{new_range}\g<2>', 
                            section
                        )
                    else:
                        section += f'    versionRange="{new_range}"\n'
                    applied_overrides.add(mod_id)
        
        updated_sections.append(section)
        
    final_text = "".join(updated_sections)
    
    for mod_id, new_range in dependency_updates.items():
        if mod_id not in applied_overrides and mod_id not in {"minecraft", "forge", "neoforge", "javafml"}:
            new_block = (
                f"\n[[dependencies.{mod_id}]]\n"
                f'    modId="{mod_id}"\n'
                f'    mandatory=true\n'
                f'    versionRange="{new_range}"\n'
                f'    ordering="NONE"\n'
                f'    side="BOTH"\n'
            )
            final_text += new_block
            
    return final_text

def rewrite_metadata(
    text: str,
    metadata_path: str,
    loader: str,
    target_version: str,
    *,
    custom_neoforge_version: Optional[str] = None,
    custom_javafml_version: Optional[str] = None,
    custom_minecraft_version: Optional[str] = None,
    dependency_overrides: Optional[Mapping[str, str]] = None,
) -> str:
    if loader == "Fabric":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text

        depends = data.get("depends")
        if not isinstance(depends, dict):
            depends = {}
            
        depends["minecraft"] = custom_minecraft_version or target_version
        depends["fabricloader"] = get_loader_version("Fabric")
        
        for mod_id, version_range in (dependency_overrides or {}).items():
            if version_range is None:
                depends.pop(mod_id, None)
            else:
                depends[mod_id] = version_range
                
        data["depends"] = depends
        data["minecraft_version"] = custom_minecraft_version or target_version
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    if loader in {"Forge", "NeoForge"}:
        minecraft_range = custom_minecraft_version or f"[{target_version}]"
        loader_range = custom_neoforge_version or get_loader_version(loader, target_version)
        loader_id = "neoforge" if loader == "NeoForge" else "forge"
        
        toml_updates = {
            "minecraft": minecraft_range,
            loader_id: loader_range,
            "javafml": custom_javafml_version or loader_range,
        }
            
        for mod_id, version_range in (dependency_overrides or {}).items():
            if version_range is not None:
                toml_updates[mod_id] = version_range

        updated = _update_toml_dependencies(text, toml_updates)
        updated = _replace_toml_value(updated, "minecraft", minecraft_range)
        updated = _replace_toml_value(updated, "minecraft_version", minecraft_range)
        if loader_range:
            updated = _replace_toml_value(updated, "loaderVersion", loader_range)
            
        return updated

    if loader == "Vanilla":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        pack = data.get("pack")
        if not isinstance(pack, dict):
            pack = {}
        minecraft_version = custom_minecraft_version or target_version
        pack["supported_minecraft_version"] = minecraft_version
        pack["minecraft_version"] = minecraft_version
        data["pack"] = pack
        data["minecraft_version"] = minecraft_version
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    return text

class Transformer:
    def rewrite_metadata(self, *args, **kwargs) -> str:
        return rewrite_metadata(*args, **kwargs)