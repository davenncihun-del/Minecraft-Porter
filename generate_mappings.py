#!/usr/bin/env python3
import argparse
import io
import json
import os
import re
import struct
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

INTERNAL_PACKAGES = ("net/minecraft/", "com/mojang/", "net/neoforge/", "net/neoforged/")

class ClassFileParser:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)
        self.constant_pool: Dict[int, Tuple[int, Tuple]] = {}
        self.class_name: str = ""
        self.fields: List[Tuple[str, str]] = []
        self.methods: List[Tuple[str, str]] = []
        try:
            self.parse()
        except Exception as e:
            # Catch parsing errors to avoid crashing on malformed files
            pass

    def parse(self):
        magic = self.stream.read(4)
        if magic != b"\xca\xfe\xba\xbe":
            return
        
        self.stream.read(4)  # minor and major version
        constant_pool_count = struct.unpack(">H", self.stream.read(2))[0]
        
        # Parse constant pool
        index = 1
        while index < constant_pool_count:
            tag_data = self.stream.read(1)
            if not tag_data:
                break
            tag = struct.unpack(">B", tag_data)[0]
            if tag == 7:  # Class
                name_index = struct.unpack(">H", self.stream.read(2))[0]
                self.constant_pool[index] = (tag, (name_index,))
            elif tag in {9, 10, 11}:  # Fieldref/Methodref/InterfaceMethodref
                class_index = struct.unpack(">H", self.stream.read(2))[0]
                name_and_type_index = struct.unpack(">H", self.stream.read(2))[0]
                self.constant_pool[index] = (tag, (class_index, name_and_type_index))
            elif tag == 8:  # String
                string_index = struct.unpack(">H", self.stream.read(2))[0]
                self.constant_pool[index] = (tag, (string_index,))
            elif tag == 1:  # Utf8
                length = struct.unpack(">H", self.stream.read(2))[0]
                value = self.stream.read(length).decode("utf-8", errors="replace")
                self.constant_pool[index] = (tag, (value,))
            elif tag in {3, 4}:  # Integer, Float
                self.stream.read(4)
            elif tag in {5, 6}:  # Long, Double
                self.stream.read(8)
                self.constant_pool[index] = (0, ())
                index += 1  # Long and Double occupy 2 CP entries
            elif tag == 12:  # NameAndType
                name_index = struct.unpack(">H", self.stream.read(2))[0]
                descriptor_index = struct.unpack(">H", self.stream.read(2))[0]
                self.constant_pool[index] = (tag, (name_index, descriptor_index))
            elif tag == 15:  # MethodHandle
                self.stream.read(3)
            elif tag == 16:  # MethodType
                self.stream.read(2)
            elif tag == 17:  # Dynamic
                self.stream.read(4)
            elif tag == 18:  # InvokeDynamic
                self.stream.read(4)
            elif tag == 19:  # Module
                self.stream.read(2)
            elif tag == 20:  # Package
                self.stream.read(2)
            else:
                break
            index += 1

        # read access flags
        self.stream.read(2)
        
        # read this class
        this_class_idx = struct.unpack(">H", self.stream.read(2))[0]
        self.class_name = self.get_class_name(this_class_idx)
        
        # read super class
        self.stream.read(2)
        
        # read interfaces
        interfaces_count = struct.unpack(">H", self.stream.read(2))[0]
        self.stream.read(interfaces_count * 2)
        
        # read fields
        fields_count = struct.unpack(">H", self.stream.read(2))[0]
        for _ in range(fields_count):
            self.stream.read(2)  # access flags
            name_idx = struct.unpack(">H", self.stream.read(2))[0]
            desc_idx = struct.unpack(">H", self.stream.read(2))[0]
            attributes_count = struct.unpack(">H", self.stream.read(2))[0]
            for _ in range(attributes_count):
                self.stream.read(2)  # attr name index
                attr_len = struct.unpack(">I", self.stream.read(4))[0]
                self.stream.read(attr_len)
            
            f_name = self.get_utf8(name_idx)
            f_desc = self.get_utf8(desc_idx)
            if f_name:
                self.fields.append((f_name, f_desc))
            
        # read methods
        methods_count = struct.unpack(">H", self.stream.read(2))[0]
        for _ in range(methods_count):
            self.stream.read(2)  # access flags
            name_idx = struct.unpack(">H", self.stream.read(2))[0]
            desc_idx = struct.unpack(">H", self.stream.read(2))[0]
            attributes_count = struct.unpack(">H", self.stream.read(2))[0]
            for _ in range(attributes_count):
                self.stream.read(2)  # attr name index
                attr_len = struct.unpack(">I", self.stream.read(4))[0]
                self.stream.read(attr_len)
                
            m_name = self.get_utf8(name_idx)
            m_desc = self.get_utf8(desc_idx)
            if m_name:
                self.methods.append((m_name, m_desc))

    def get_utf8(self, index: int) -> str:
        entry = self.constant_pool.get(index)
        if entry and entry[0] == 1:
            return entry[1][0]
        return ""

    def get_class_name(self, index: int) -> str:
        entry = self.constant_pool.get(index)
        if entry and entry[0] == 7:
            name_index = entry[1][0]
            return self.get_utf8(name_index)
        return ""


def parse_tiny_mappings(content: str) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str], Dict[Tuple[str, str], str]]:
    """
    Parses standard Tiny mapping format contents (v1 and v2) and returns:
    1. class_mappings: Dict[class_intermediary, class_named]
    2. method_mappings: Dict[(class_named, method_intermediary), method_named]
    3. field_mappings: Dict[(class_named, field_intermediary), field_named]
    """
    class_mappings = {}
    method_mappings = {}
    field_mappings = {}
    
    lines = content.splitlines()
    if not lines:
        return class_mappings, method_mappings, field_mappings
        
    header = lines[0].split("\t")
    if not header[0].startswith("tiny"):
        return class_mappings, method_mappings, field_mappings
        
    # Find the namespaces line
    ns_line = None
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if "intermediary" in parts and "named" in parts:
            ns_line = parts
            break
            
    if not ns_line:
        # Default fallback
        inter_idx = 1
        named_idx = 2
    else:
        # Clean metadata tokens to isolate namespaces
        clean_ns = [p for p in ns_line if p not in ("tiny", "0", "1", "2", "3", "4", "5", "6", "v1", "v2")]
        if "intermediary" in clean_ns and "named" in clean_ns:
            inter_idx = clean_ns.index("intermediary")
            named_idx = clean_ns.index("named")
        else:
            # Fallback
            inter_idx = 1
            named_idx = 2
        
    current_class_named = None
    
    for line in lines:
        if not line.strip() or line.startswith("#") or line.startswith("tiny"):
            continue
            
        parts = line.split("\t")
        if parts[0] == "" and len(parts) > 1:
            # Indented member (Tiny v2)
            member_type = parts[1]
            if member_type == "f" and current_class_named:
                if len(parts) > max(inter_idx + 3, named_idx + 3):
                    f_inter = parts[inter_idx + 3]
                    f_named = parts[named_idx + 3]
                    field_mappings[(current_class_named, f_inter)] = f_named
            elif member_type == "m" and current_class_named:
                if len(parts) > max(inter_idx + 3, named_idx + 3):
                    m_inter = parts[inter_idx + 3]
                    m_named = parts[named_idx + 3]
                    method_mappings[(current_class_named, m_inter)] = m_named
        else:
            member_type = parts[0]
            if member_type == "c":
                if len(parts) > max(inter_idx + 1, named_idx + 1):
                    c_inter = parts[inter_idx + 1]
                    c_named = parts[named_idx + 1]
                    class_mappings[c_inter] = c_named
                    current_class_named = c_named
            elif member_type == "f":
                # Tiny v1
                if len(parts) > max(inter_idx + 3, named_idx + 3):
                    c_inter = parts[1]
                    c_named = class_mappings.get(c_inter, c_inter)
                    f_inter = parts[inter_idx + 3]
                    f_named = parts[named_idx + 3]
                    field_mappings[(c_named, f_inter)] = f_named
            elif member_type == "m":
                # Tiny v1
                if len(parts) > max(inter_idx + 3, named_idx + 3):
                    c_inter = parts[1]
                    c_named = class_mappings.get(c_inter, c_inter)
                    m_inter = parts[inter_idx + 3]
                    m_named = parts[named_idx + 3]
                    method_mappings[(c_named, m_inter)] = m_named
                    
    return class_mappings, method_mappings, field_mappings


def download_intermediary_mappings(version: str) -> Optional[str]:
    url = f"https://maven.fabricmc.net/net/fabricmc/intermediary/{version}/intermediary-{version}.jar"
    try:
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": "MinecraftPorter/1.0 (Refactoring Catalog Generator)"}
        )
        with urllib.request.urlopen(req) as response:
            jar_data = response.read()
        
        with zipfile.ZipFile(io.BytesIO(jar_data)) as jar:
            if "mappings/mappings.tiny" in jar.namelist():
                return jar.read("mappings/mappings.tiny").decode("utf-8", errors="replace")
    except Exception as e:
        print(f"Failed to download intermediary mappings for version {version}: {e}")
    return None


def get_intermediary_mappings(version: str) -> Optional[str]:
    mappings_dir = Path("mappings")
    mappings_dir.mkdir(exist_ok=True)
    
    # Try different naming conventions
    local_path = mappings_dir / f"{version}-intermediary.tiny"
    if local_path.exists():
        return local_path.read_text(encoding="utf-8")
        
    print(f"Downloading intermediary mappings for version {version}...")
    mappings_content = download_intermediary_mappings(version)
    if mappings_content:
        local_path.write_text(mappings_content, encoding="utf-8")
        return mappings_content
        
    # Try minor version fallback (e.g., 1.21.11 -> 1.21)
    version_parts = version.split(".")
    if len(version_parts) > 2:
        fallback_version = ".".join(version_parts[:2])
        print(f"Retrying with fallback version: {fallback_version}")
        fallback_path = mappings_dir / f"{fallback_version}-intermediary.tiny"
        if fallback_path.exists():
            return fallback_path.read_text(encoding="utf-8")
        mappings_content = download_intermediary_mappings(fallback_version)
        if mappings_content:
            fallback_path.write_text(mappings_content, encoding="utf-8")
            return mappings_content
            
    return None


def detect_version_from_jar(jar_path: Path) -> Optional[str]:
    # 1. Filename pattern check (e.g. minecraft-1.21.1.jar, NeoForge-26.1.jar)
    filename = jar_path.name
    match = re.search(r'(\d+\.\d+(?:\.\d+)?)', filename)
    if match:
        return match.group(1)
        
    with zipfile.ZipFile(jar_path, "r") as archive:
        names = archive.namelist()
        
        # 2. Check version.json
        if "version.json" in names:
            try:
                v_data = json.loads(archive.read("version.json").decode("utf-8", errors="replace"))
                if isinstance(v_data, dict) and "id" in v_data:
                    return v_data["id"]
            except Exception:
                pass
                
        # 3. Check NeoForge/Forge/Fabric metadata
        for meta_path in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
            if meta_path in names:
                try:
                    text = archive.read(meta_path).decode("utf-8", errors="replace")
                    for pattern in [r'minecraft\s*=\s*"([^"]+)"', r'versionRange\s*=\s*"([^\"]+)"', r'version\s*=\s*"([^"]+)"']:
                        m = re.search(pattern, text)
                        if m:
                            v = m.group(1).strip('[]() ')
                            if v and not v.startswith('${'):
                                return v
                except Exception:
                    pass
                    
        if "fabric.mod.json" in names:
            try:
                data = json.loads(archive.read("fabric.mod.json").decode("utf-8", errors="replace"))
                depends = data.get("depends", {})
                if isinstance(depends, dict) and "minecraft" in depends:
                    return depends["minecraft"]
                if "minecraft_version" in data:
                    return data["minecraft_version"]
                if "version" in data:
                    return data["version"]
            except Exception:
                pass
                
    return None


def diff_jars(source_jar: Path, target_jar: Path, source_version: Optional[str], target_version: Optional[str]) -> Dict[str, Any]:
    # Auto-detect versions if not provided
    if not source_version:
        source_version = detect_version_from_jar(source_jar)
    if not target_version:
        target_version = detect_version_from_jar(target_jar)
        
    if not source_version or not target_version:
        raise ValueError(
            f"Unable to detect version for jars. Please specify --source-version and --target-version. "
            f"(Detected: Source={source_version}, Target={target_version})"
        )
        
    print(f"Diffing source jar (Minecraft {source_version}) and target jar (Minecraft {target_version})...")
    
    # Download and parse mapping sets
    src_mappings_raw = get_intermediary_mappings(source_version)
    tgt_mappings_raw = get_intermediary_mappings(target_version)
    
    if not src_mappings_raw:
        print(f"Warning: Could not load source mappings for version {source_version}.")
    if not tgt_mappings_raw:
        print(f"Warning: Could not load target mappings for version {target_version}.")
        
    src_class_map, src_method_map, src_field_map = parse_tiny_mappings(src_mappings_raw or "")
    tgt_class_map, tgt_method_map, tgt_field_map = parse_tiny_mappings(tgt_mappings_raw or "")
    
    # Reverse lookups: named -> intermediary
    src_class_to_inter = {named: inter for inter, named in src_class_map.items()}
    src_method_to_inter = {(class_named, method_named): method_inter for (class_named, method_inter), method_named in src_method_map.items()}
    src_field_to_inter = {(class_named, field_named): field_inter for (class_named, field_inter), field_named in src_field_map.items()}
    
    # Parse source jar
    source_classes: Dict[str, ClassFileParser] = {}
    with zipfile.ZipFile(source_jar, "r") as archive:
        for name in archive.namelist():
            if name.endswith(".class") and not name.startswith("META-INF/"):
                parser = ClassFileParser(archive.read(name))
                if parser.class_name:
                    # check internal packages
                    if any(parser.class_name.startswith(pkg) for pkg in INTERNAL_PACKAGES):
                        source_classes[parser.class_name] = parser
                        
    # Parse target jar
    target_classes: Dict[str, ClassFileParser] = {}
    with zipfile.ZipFile(target_jar, "r") as archive:
        for name in archive.namelist():
            if name.endswith(".class") and not name.startswith("META-INF/"):
                parser = ClassFileParser(archive.read(name))
                if parser.class_name:
                    if any(parser.class_name.startswith(pkg) for pkg in INTERNAL_PACKAGES):
                        target_classes[parser.class_name] = parser

    refactoring_catalog = {
        "classes": {},
        "methods": {},
        "fields": {}
    }
    
    # Analyze classes
    for src_class_name, src_parser in source_classes.items():
        src_class_dot = src_class_name.replace("/", ".")
        
        # Check if class exists in target
        if src_class_name in target_classes:
            tgt_parser = target_classes[src_class_name]
            
            # Match methods
            tgt_methods_set = set(tgt_parser.methods)
            for m_name, m_desc in src_parser.methods:
                if (m_name, m_desc) not in tgt_methods_set:
                    # Method is missing! Try to find if it was renamed
                    m_inter = src_method_to_inter.get((src_class_name, m_name))
                    if m_inter:
                        # Find what this intermediary name is mapped to in target
                        tgt_m_name = tgt_method_map.get((src_class_name, m_inter))
                        if tgt_m_name and tgt_m_name != m_name:
                            action = {
                                "action": "replace_method",
                                "replace_with": tgt_m_name
                            }
                            refactoring_catalog["methods"][f"{src_class_dot}.{m_name}()"] = action
                            refactoring_catalog["methods"][f"{src_class_dot}.{m_name}{m_desc}"] = action
                        else:
                            # It was deleted
                            refactoring_catalog["methods"][f"{src_class_dot}.{m_name}()"] = {"action": "remove_method"}
                            refactoring_catalog["methods"][f"{src_class_dot}.{m_name}{m_desc}"] = {"action": "remove_method"}
                    else:
                        refactoring_catalog["methods"][f"{src_class_dot}.{m_name}()"] = {"action": "remove_method"}
                        refactoring_catalog["methods"][f"{src_class_dot}.{m_name}{m_desc}"] = {"action": "remove_method"}
            
            # Match fields
            tgt_fields_set = set(tgt_parser.fields)
            for f_name, f_desc in src_parser.fields:
                if (f_name, f_desc) not in tgt_fields_set:
                    # Field is missing! Try to find if it was renamed
                    f_inter = src_field_to_inter.get((src_class_name, f_name))
                    if f_inter:
                        tgt_f_name = tgt_field_map.get((src_class_name, f_inter))
                        if tgt_f_name and tgt_f_name != f_name:
                            refactoring_catalog["fields"][f"{src_class_dot}.{f_name}"] = {
                                "action": "replace_field",
                                "replace_with": tgt_f_name
                            }
                        else:
                            refactoring_catalog["fields"][f"{src_class_dot}.{f_name}"] = {"action": "remove_field"}
                    else:
                        refactoring_catalog["fields"][f"{src_class_dot}.{f_name}"] = {"action": "remove_field"}
                        
        else:
            # Class is missing! Try to find if it was renamed/relocated
            c_inter = src_class_to_inter.get(src_class_name)
            if c_inter:
                tgt_class_name = tgt_class_map.get(c_inter)
                if tgt_class_name and tgt_class_name != src_class_name:
                    tgt_class_dot = tgt_class_name.replace("/", ".")
                    refactoring_catalog["classes"][src_class_dot] = {
                        "action": "replace_class",
                        "replace_with": tgt_class_dot
                    }
                    
                    # Since the class itself was renamed/relocated, we must also map all its methods and fields to the new class!
                    if tgt_class_name in target_classes:
                        tgt_parser = target_classes[tgt_class_name]
                        # Map fields
                        tgt_fields_set = set(tgt_parser.fields)
                        for f_name, f_desc in src_parser.fields:
                            f_inter = src_field_to_inter.get((src_class_name, f_name))
                            tgt_f_name = tgt_field_map.get((tgt_class_name, f_inter)) if f_inter else None
                            if not tgt_f_name:
                                tgt_f_name = f_name
                            
                            if (tgt_f_name, f_desc) in tgt_fields_set:
                                # Define rules under BOTH the original class name and the renamed class name
                                refactoring_catalog["fields"][f"{src_class_dot}.{f_name}"] = {
                                    "action": "replace_field",
                                    "replace_with": f"{tgt_class_dot}.{tgt_f_name}"
                                }
                                refactoring_catalog["fields"][f"{tgt_class_dot}.{f_name}"] = {
                                    "action": "replace_field",
                                    "replace_with": tgt_f_name
                                }
                        
                        # Map methods
                        tgt_methods_set = set(tgt_parser.methods)
                        for m_name, m_desc in src_parser.methods:
                            m_inter = src_method_to_inter.get((src_class_name, m_name))
                            tgt_m_name = tgt_method_map.get((tgt_class_name, m_inter)) if m_inter else None
                            if not tgt_m_name:
                                tgt_m_name = m_name
                            
                            if (tgt_m_name, m_desc) in tgt_methods_set:
                                action = {
                                    "action": "replace_method",
                                    "replace_with": f"{tgt_class_dot}.{tgt_m_name}"
                                }
                                action_simple = {
                                    "action": "replace_method",
                                    "replace_with": tgt_m_name
                                }
                                # Keyed under original class
                                refactoring_catalog["methods"][f"{src_class_dot}.{m_name}()"] = action
                                refactoring_catalog["methods"][f"{src_class_dot}.{m_name}{m_desc}"] = action
                                # Keyed under renamed class
                                refactoring_catalog["methods"][f"{tgt_class_dot}.{m_name}()"] = action_simple
                                refactoring_catalog["methods"][f"{tgt_class_dot}.{m_name}{m_desc}"] = action_simple
                else:
                    refactoring_catalog["classes"][src_class_dot] = {"action": "remove_class"}
            else:
                refactoring_catalog["classes"][src_class_dot] = {"action": "remove_class"}
                
    return refactoring_catalog


def main():
    parser = argparse.ArgumentParser(description="Generate a deterministic mappings.json code refactoring catalog between two Minecraft versions.")
    parser.add_argument("--source", required=True, help="Path to the source Minecraft or NeoForge .jar file.")
    parser.add_argument("--target", required=True, help="Path to the target Minecraft or NeoForge .jar file.")
    parser.add_argument("--source-version", help="Minecraft version of the source jar (auto-detected if not specified).")
    parser.add_argument("--target-version", help="Minecraft version of the target jar (auto-detected if not specified).")
    parser.add_argument("--output", default="mappings.json", help="Path to write the output mappings.json file.")
    
    args = parser.parse_args()
    
    src_path = Path(args.source)
    tgt_path = Path(args.target)
    out_path = Path(args.output)
    
    if not src_path.exists():
        print(f"Error: Source jar does not exist at {src_path}")
        return
    if not tgt_path.exists():
        print(f"Error: Target jar does not exist at {tgt_path}")
        return
        
    try:
        catalog = diff_jars(src_path, tgt_path, args.source_version, args.target_version)
        
        # Write to JSON
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)
            
        print(f"Deterministic mappings successfully generated at: {out_path.resolve()}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
