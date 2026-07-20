from __future__ import annotations
import io
import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple

@dataclass
class ClassReference:
    owner: str
    name: str
    descriptor: str
    kind: str

class ClassReferenceExtractor:
    def extract_references(self, data: bytes) -> List[ClassReference]:
        stream = io.BytesIO(data)
        magic = stream.read(4)
        if magic != b"\xca\xfe\xba\xbe":
            return []

        stream.read(4)  # minor and major version
        constant_pool_count_bytes = stream.read(2)
        if len(constant_pool_count_bytes) < 2:
            return []
            
        constant_pool_count = struct.unpack(">H", constant_pool_count_bytes)[0]
        constant_pool: Dict[int, Tuple[int, Tuple]] = {}
        index = 1
        
        while index < constant_pool_count:
            tag_byte = stream.read(1)
            if not tag_byte:
                break
            tag = struct.unpack(">B", tag_byte)[0]
            
            if tag == 7:  # Class
                name_index = struct.unpack(">H", stream.read(2))[0]
                constant_pool[index] = (tag, (name_index,))
            elif tag in {9, 10, 11}:  # Fieldref / Methodref / InterfaceMethodref
                class_index = struct.unpack(">H", stream.read(2))[0]
                name_and_type_index = struct.unpack(">H", stream.read(2))[0]
                constant_pool[index] = (tag, (class_index, name_and_type_index))
            elif tag == 8:  # String
                string_index = struct.unpack(">H", stream.read(2))[0]
                constant_pool[index] = (tag, (string_index,))
            elif tag == 1:  # Utf8
                length = struct.unpack(">H", stream.read(2))[0]
                value = stream.read(length).decode("utf-8", errors="replace")
                constant_pool[index] = (tag, (value,))
            elif tag in {3, 4}:  # Integer, Float
                stream.read(4)
            elif tag in {5, 6}:  # Long, Double
                stream.read(8)
                index += 1  # 8-byte constants take up two slots in the pool
                constant_pool[index] = (0, ())
            elif tag == 12:  # NameAndType
                name_index = struct.unpack(">H", stream.read(2))[0]
                descriptor_index = struct.unpack(">H", stream.read(2))[0]
                constant_pool[index] = (tag, (name_index, descriptor_index))
            elif tag == 15:  # MethodHandle
                stream.read(3)
            elif tag == 16:  # MethodType
                stream.read(2)
            elif tag == 17:  # Dynamic (Java 11+)
                stream.read(4)
            elif tag == 18:  # InvokeDynamic
                stream.read(4)
            elif tag in {19, 20}:  # Module, Package (Java 9+)
                stream.read(2)
            else:
                # Fallback to prevent hard crashes on future Java versions
                raise ValueError(f"CRITICAL: Unsupported constant pool tag {tag} at index {index}")
            index += 1

        references: List[ClassReference] = []
        for entry in constant_pool.values():
            tag, data_entry = entry
            if tag in {9, 10, 11}:
                class_info = constant_pool.get(data_entry[0])
                name_and_type = constant_pool.get(data_entry[1])
                
                if not class_info or not name_and_type:
                    continue
                    
                _, (class_name_index,) = class_info
                _, (name_index, desc_index) = name_and_type
                
                owner = self._decode_utf8(constant_pool, class_name_index)
                name = self._decode_utf8(constant_pool, name_index)
                descriptor = self._decode_utf8(constant_pool, desc_index)
                kind = "method" if tag in {10, 11} else "field"
                
                references.append(ClassReference(
                    owner=owner.replace("/", "."), 
                    name=name, 
                    descriptor=descriptor, 
                    kind=kind
                ))
                
        return references

    def _decode_utf8(self, pool: Dict[int, Tuple[int, Tuple]], index: int) -> str:
        entry = pool.get(index)
        return entry[1][0] if entry and entry[0] == 1 else ""