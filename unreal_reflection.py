from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

from memory_trainer import (
    AobPattern,
    MemoryReadError,
    PatternAmbiguousError,
    PatternNotFoundError,
    ProcessMemory,
    TrainerError,
)


UOBJECT_CLASS_PRIVATE = 0x10
UOBJECT_NAME_PRIVATE = 0x18
UOBJECT_OUTER_PRIVATE = 0x20
USTRUCT_SUPER_STRUCT = 0x40
USTRUCT_CHILD_PROPERTIES = 0x50
FFIELD_NEXT = 0x20
FFIELD_NAME_PRIVATE = 0x28
FPROPERTY_OFFSET_INTERNAL = 0x4C

FUOBJECT_ARRAY_OBJ_OBJECTS = 0x10
TUOBJECT_ARRAY_OBJECTS = 0x00
TUOBJECT_ARRAY_MAX_ELEMENTS = 0x10
TUOBJECT_ARRAY_NUM_ELEMENTS = 0x14
TUOBJECT_ARRAY_MAX_CHUNKS = 0x18
TUOBJECT_ARRAY_NUM_CHUNKS = 0x1C
FUOBJECT_ITEM_SIZE = 0x18
FUOBJECT_ITEM_OBJECT = 0x00
OBJECTS_PER_CHUNK = 64 * 1024


GUOBJECT_ARRAY_PATTERN = AobPattern.parse(
    "74 ?? 48 8D 0D ?? ?? ?? ?? "
    "C6 05 ?? ?? ?? ?? 01 E8 ?? ?? ?? ?? "
    "C6 05 ?? ?? ?? ?? 01"
)
GUOBJECT_ARRAY_DISPLACEMENT_OFFSET = 5

FNAME_TO_STRING_CALL_PATTERN = AobPattern.parse(
    "48 8B 48 ?? 48 89 4C 24 ?? 48 8D 4C 24 ?? "
    "E8 ?? ?? ?? ?? 83 7C 24 ?? 00 48 8D"
)
FNAME_TO_STRING_CALL_DISPLACEMENT_OFFSET = 15

FNAME_POOL_IN_FUNCTION_PATTERN = AobPattern.parse(
    "80 3D ?? ?? ?? ?? 00 74 ?? "
    "4C 8D 05 ?? ?? ?? ?? EB ?? "
    "48 8D 0D ?? ?? ?? ?? E8"
)
FNAME_POOL_DISPLACEMENT_OFFSET = 12


def _i32(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def _u16(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def resolve_rip_target(process: ProcessMemory, displacement_address: int) -> int:


    displacement = _i32(process.read(displacement_address, 4))
    return displacement_address + 4 + displacement


@dataclass(frozen=True)
class UnrealCoreAddresses:
    guobject_array: int
    fname_pool: int
    fname_to_string: int


@dataclass(frozen=True)
class UnrealObject:
    index: int
    address: int
    class_address: int
    outer_address: int
    name: str


@dataclass(frozen=True)
class ReflectedProperty:
    owner_name: str
    name: str
    field_address: int
    offset: int


def resolve_unreal_core(process: ProcessMemory) -> UnrealCoreAddresses:


    guobject_match = process.scan_unique(GUOBJECT_ARRAY_PATTERN)
    guobject_array = resolve_rip_target(
        process,
        guobject_match + GUOBJECT_ARRAY_DISPLACEMENT_OFFSET,
    )

    callers = process.scan(FNAME_TO_STRING_CALL_PATTERN)
    if not callers:
        raise PatternNotFoundError("没有找到 UE FName::ToString 调用特征")
    fname_targets = {
        resolve_rip_target(
            process,
            caller + FNAME_TO_STRING_CALL_DISPLACEMENT_OFFSET,
        )
        for caller in callers
    }
    if len(fname_targets) != 1:
        targets = "、".join(f"0x{value:X}" for value in sorted(fname_targets))
        raise PatternAmbiguousError(f"UE FName::ToString 目标不唯一：{targets}")
    fname_to_string = fname_targets.pop()

    function = process.read(fname_to_string, 0x180)
    local_matches = list(FNAME_POOL_IN_FUNCTION_PATTERN.finditer(function))
    if len(local_matches) != 1:
        raise PatternNotFoundError(
            "已找到 FName::ToString，但无法从当前函数唯一解析 FNamePool"
        )
    displacement = (
        fname_to_string
        + local_matches[0]
        + FNAME_POOL_DISPLACEMENT_OFFSET
    )
    fname_pool = resolve_rip_target(process, displacement)

    module = process.module()
    module_end = module.base + module.size
    for label, address in (
        ("GUObjectArray", guobject_array),
        ("FNamePool", fname_pool),
        ("FName::ToString", fname_to_string),
    ):
        if not module.base <= address < module_end:
            raise TrainerError(f"{label} 解析结果不在游戏主模块内：0x{address:X}")

    return UnrealCoreAddresses(
        guobject_array=guobject_array,
        fname_pool=fname_pool,
        fname_to_string=fname_to_string,
    )


class UnrealReflection:


    def __init__(
        self,
        process: ProcessMemory,
        addresses: UnrealCoreAddresses | None = None,
    ):
        self.process = process
        self.addresses = addresses or resolve_unreal_core(process)
        self._name_cache: dict[int, str] = {}
        self._object_name_cache: dict[int, str] = {}
        self._struct_cache: dict[tuple[str, tuple[str, ...]], int] = {}
        self._struct_index: dict[str, list[tuple[int, str]]] | None = None

    def read_u16(self, address: int) -> int:
        return _u16(self.process.read(address, 2))

    def read_u32(self, address: int) -> int:
        return _u32(self.process.read(address, 4))

    def read_i32(self, address: int) -> int:
        return _i32(self.process.read(address, 4))

    def read_u64(self, address: int) -> int:
        return _u64(self.process.read(address, 8))

    def object_array_stats(self) -> tuple[int, int, int, int, int]:
        array = self.addresses.guobject_array + FUOBJECT_ARRAY_OBJ_OBJECTS
        objects = self.read_u64(array + TUOBJECT_ARRAY_OBJECTS)
        max_elements = self.read_i32(array + TUOBJECT_ARRAY_MAX_ELEMENTS)
        num_elements = self.read_i32(array + TUOBJECT_ARRAY_NUM_ELEMENTS)
        max_chunks = self.read_i32(array + TUOBJECT_ARRAY_MAX_CHUNKS)
        num_chunks = self.read_i32(array + TUOBJECT_ARRAY_NUM_CHUNKS)

        if not objects or not (1 <= num_elements <= 8_000_000):
            raise TrainerError(
                f"GUObjectArray 尚未初始化或对象数量异常：{num_elements}"
            )
        expected_chunks = (num_elements + OBJECTS_PER_CHUNK - 1) // OBJECTS_PER_CHUNK
        if not (expected_chunks <= num_chunks <= max_chunks <= 4096):
            raise TrainerError(
                "GUObjectArray 分块信息异常："
                f"{num_chunks}/{max_chunks}，对象 {num_elements}"
            )
        return objects, max_elements, num_elements, max_chunks, num_chunks

    def decode_fname(self, comparison_index: int, number: int = 0) -> str:
        comparison_index &= 0xFFFFFFFF
        cached = self._name_cache.get(comparison_index)
        if cached is None:
            block = comparison_index >> 16
            offset = comparison_index & 0xFFFF
            if block > 8192:
                raise TrainerError(f"FName 块编号异常：{block}")
            block_address = self.read_u64(self.addresses.fname_pool + 0x10 + block * 8)
            if not block_address:
                raise TrainerError(f"FName 块 {block} 尚未分配")
            entry = block_address + offset * 2
            header = self.read_u16(entry)
            wide = bool(header & 1)
            length = header >> 6
            if not (0 < length <= 1024):
                raise TrainerError(
                    f"FName 条目长度异常：索引 0x{comparison_index:X}，长度 {length}"
                )
            raw = self.process.read(entry + 2, length * (2 if wide else 1))
            cached = raw.decode("utf-16-le" if wide else "utf-8", errors="replace")
            self._name_cache[comparison_index] = cached
        return f"{cached}_{number - 1}" if number else cached

    def fname_at(self, address: int) -> str:
        data = self.process.read(address, 8)
        return self.decode_fname(_u32(data), _u32(data, 4))

    def object_name(self, object_address: int) -> str:
        if not object_address:
            return "None"
        cached = self._object_name_cache.get(object_address)
        if cached is None:
            cached = self.fname_at(object_address + UOBJECT_NAME_PRIVATE)
            self._object_name_cache[object_address] = cached
        return cached

    def iter_object_addresses(self, *, limit: int | None = None) -> Iterator[tuple[int, int]]:
        objects, _, num_elements, _, num_chunks = self.object_array_stats()
        remaining = min(num_elements, limit) if limit is not None else num_elements
        index_base = 0
        for chunk_index in range(num_chunks):
            if remaining <= 0:
                return
            chunk = self.read_u64(objects + chunk_index * 8)
            count = min(remaining, OBJECTS_PER_CHUNK)
            if not chunk:
                index_base += count
                remaining -= count
                continue
            raw = self.process.read(chunk, count * FUOBJECT_ITEM_SIZE)
            for local_index in range(count):
                offset = local_index * FUOBJECT_ITEM_SIZE + FUOBJECT_ITEM_OBJECT
                object_address = _u64(raw, offset)
                if object_address:
                    yield index_base + local_index, object_address
            index_base += count
            remaining -= count

    def iter_objects(self, *, limit: int | None = None) -> Iterator[UnrealObject]:
        for index, address in self.iter_object_addresses(limit=limit):


            try:
                data = self.process.read(address + UOBJECT_CLASS_PRIVATE, 0x18)
            except MemoryReadError:
                continue
            class_address = _u64(data, 0)
            name_index = _u32(data, UOBJECT_NAME_PRIVATE - UOBJECT_CLASS_PRIVATE)
            name_number = _u32(
                data,
                UOBJECT_NAME_PRIVATE - UOBJECT_CLASS_PRIVATE + 4,
            )
            outer_address = _u64(
                data,
                UOBJECT_OUTER_PRIVATE - UOBJECT_CLASS_PRIVATE,
            )
            try:
                name = self.decode_fname(name_index, name_number)
            except TrainerError:
                continue
            yield UnrealObject(
                index=index,
                address=address,
                class_address=class_address,
                outer_address=outer_address,
                name=name,
            )

    def find_struct(
        self,
        struct_name: str,
        *,
        meta_names: tuple[str, ...] = (
            "Class",
            "BlueprintGeneratedClass",
            "ScriptStruct",
        ),
    ) -> int:
        wanted = struct_name.removesuffix("_C").casefold()
        cache_key = (wanted, meta_names)
        cached = self._struct_cache.get(cache_key)
        if cached:
            return cached
        if self._struct_index is None:
            index: dict[str, list[tuple[int, str]]] = {}
            for obj in self.iter_objects():
                try:
                    meta_name = self.object_name(obj.class_address)
                except TrainerError:
                    continue
                if meta_name not in {
                    "Class",
                    "BlueprintGeneratedClass",
                    "ScriptStruct",
                }:
                    continue
                key = obj.name.removesuffix("_C").casefold()
                index.setdefault(key, []).append((obj.address, meta_name))
            self._struct_index = index
        matches = [
            item
            for item in self._struct_index.get(wanted, ())
            if item[1] in meta_names
        ]
        if not matches:
            raise TrainerError(f"没有在 UE 对象表中找到结构：{struct_name}")

        priority = {"Class": 0, "ScriptStruct": 1, "BlueprintGeneratedClass": 2}
        result = min(matches, key=lambda item: priority.get(item[1], 99))[0]
        self._struct_cache[cache_key] = result
        return result

    def find_class(self, class_name: str) -> int:
        return self.find_struct(
            class_name,
            meta_names=("Class", "BlueprintGeneratedClass"),
        )

    def iter_properties(
        self,
        struct_address: int,
        *,
        include_supers: bool = True,
    ) -> Iterator[ReflectedProperty]:
        visited_structs: set[int] = set()
        visited_fields: set[int] = set()
        current = struct_address
        while current and current not in visited_structs:
            visited_structs.add(current)
            owner_name = self.object_name(current)
            field = self.read_u64(current + USTRUCT_CHILD_PROPERTIES)
            while field and field not in visited_fields:
                visited_fields.add(field)
                name = self.fname_at(field + FFIELD_NAME_PRIVATE)
                offset = self.read_i32(field + FPROPERTY_OFFSET_INTERNAL)
                if 0 <= offset <= 0x100000:
                    yield ReflectedProperty(
                        owner_name=owner_name,
                        name=name,
                        field_address=field,
                        offset=offset,
                    )
                field = self.read_u64(field + FFIELD_NEXT)
            if not include_supers:
                return
            current = self.read_u64(current + USTRUCT_SUPER_STRUCT)

    def property_offset(
        self,
        class_name: str,
        property_name: str,
        *,
        include_supers: bool = True,
    ) -> int:
        struct_address = self.find_struct(class_name)
        wanted = property_name.casefold()
        matches = [
            prop
            for prop in self.iter_properties(
                struct_address,
                include_supers=include_supers,
            )
            if prop.name.casefold() == wanted
        ]
        if not matches:
            raise TrainerError(f"没有找到属性：{class_name}.{property_name}")
        offsets = {prop.offset for prop in matches}
        if len(offsets) != 1:
            values = "、".join(hex(value) for value in sorted(offsets))
            raise TrainerError(
                f"属性偏移不唯一：{class_name}.{property_name} -> {values}"
            )
        return offsets.pop()

    def validate(self) -> dict[str, int | str]:
        _, max_elements, num_elements, max_chunks, num_chunks = self.object_array_stats()
        none_name = self.decode_fname(0)
        if none_name != "None":
            raise TrainerError(f"FNamePool 校验失败：索引 0 应为 None，实际 {none_name!r}")
        sample_count = 0
        class_count = 0
        for obj in self.iter_objects(limit=min(num_elements, 50_000)):
            sample_count += 1
            if obj.name == "Class":
                class_count += 1
            if sample_count >= 2500:
                break
        if sample_count < 100:
            raise TrainerError(f"UE 对象表有效样本过少：{sample_count}")
        return {
            "guobject_array": self.addresses.guobject_array,
            "fname_pool": self.addresses.fname_pool,
            "fname_to_string": self.addresses.fname_to_string,
            "max_elements": max_elements,
            "num_elements": num_elements,
            "max_chunks": max_chunks,
            "num_chunks": num_chunks,
            "sample_objects": sample_count,
            "sample_classes": class_count,
            "fname_zero": none_name,
        }
