"""Self-contained Windows process-memory engine for the Palworld trainer.

This module is independently implemented.  It does not load or call any
third-party trainer.  The public classes deliberately separate read-only
discovery/scanning from write access so that signatures can be validated
before a patch is ever applied.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from ctypes import wintypes


PALWORLD_PROCESS_NAME = "Palworld-Win64-Shipping.exe"

PROCESS_TERMINATE = 0x0001
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

MEM_COMMIT = 0x00001000
MEM_RESERVE = 0x00002000
MEM_RELEASE = 0x00008000
MEM_FREE = 0x00010000

PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100

IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_READ = 0x40000000

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260


class TrainerError(RuntimeError):
    """Base exception presented to the trainer UI."""


class ProcessNotFoundError(TrainerError):
    pass


class ProcessAccessError(TrainerError):
    pass


class MemoryReadError(TrainerError):
    pass


class MemoryWriteError(TrainerError):
    pass


class PatternNotFoundError(TrainerError):
    pass


class PatternAmbiguousError(TrainerError):
    pass


class PatchConflictError(TrainerError):
    pass


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * MAX_PATH),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class SYSTEM_INFO_UNION_STRUCT(ctypes.Structure):
    _fields_ = [
        ("wProcessorArchitecture", wintypes.WORD),
        ("wReserved", wintypes.WORD),
    ]


class SYSTEM_INFO_UNION(ctypes.Union):
    _anonymous_ = ("struct",)
    _fields_ = [
        ("dwOemId", wintypes.DWORD),
        ("struct", SYSTEM_INFO_UNION_STRUCT),
    ]


class SYSTEM_INFO(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("union", SYSTEM_INFO_UNION),
        ("dwPageSize", wintypes.DWORD),
        ("lpMinimumApplicationAddress", ctypes.c_void_p),
        ("lpMaximumApplicationAddress", ctypes.c_void_p),
        ("dwActiveProcessorMask", ctypes.c_size_t),
        ("dwNumberOfProcessors", wintypes.DWORD),
        ("dwProcessorType", wintypes.DWORD),
        ("dwAllocationGranularity", wintypes.DWORD),
        ("wProcessorLevel", wintypes.WORD),
        ("wProcessorRevision", wintypes.WORD),
    ]


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    executable: str


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    path: Path
    base: int
    size: int


@dataclass(frozen=True)
class ModuleSection:
    name: str
    address: int
    size: int
    characteristics: int

    @property
    def executable(self) -> bool:
        return bool(self.characteristics & IMAGE_SCN_MEM_EXECUTE)

    @property
    def readable(self) -> bool:
        return bool(self.characteristics & IMAGE_SCN_MEM_READ)


@dataclass(frozen=True)
class GameFingerprint:
    path: Path
    size: int
    mtime_ns: int
    sha256: str

    @classmethod
    def from_path(cls, path: Path) -> "GameFingerprint":
        path = Path(path)
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        return cls(
            path=path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            sha256=digest.hexdigest().upper(),
        )


@dataclass(frozen=True)
class AobPattern:
    values: bytes
    masks: bytes
    source: str

    @classmethod
    def parse(cls, expression: str) -> "AobPattern":
        tokens = expression.replace(",", " ").split()
        if not tokens:
            raise ValueError("AOB 特征码不能为空")
        values = bytearray()
        masks = bytearray()
        for token in tokens:
            token = token.strip().upper()
            if token in {"?", "??", "*", "**"}:
                values.append(0)
                masks.append(0)
                continue
            if len(token) != 2 or any(character not in "0123456789ABCDEF?" for character in token):
                raise ValueError(f"无效 AOB 字节：{token}")
            value = 0
            mask = 0
            for shift, character in ((4, token[0]), (0, token[1])):
                if character != "?":
                    value |= int(character, 16) << shift
                    mask |= 0xF << shift
            values.append(value)
            masks.append(mask)
        return cls(bytes(values), bytes(masks), expression)

    def __len__(self) -> int:
        return len(self.values)

    def matches_at(self, data: bytes | bytearray | memoryview, offset: int) -> bool:
        if offset < 0 or offset + len(self) > len(data):
            return False
        return all(
            (data[offset + index] & mask) == (value & mask)
            for index, (value, mask) in enumerate(zip(self.values, self.masks))
        )

    def finditer(self, data: bytes | bytearray | memoryview) -> Iterator[int]:
        length = len(self)
        if length > len(data):
            return
        haystack = data if isinstance(data, bytes) else bytes(data)
        best_start = None
        best_length = 0
        run_start = None
        for index, mask in enumerate(self.masks + b"\x00"):
            if mask == 0xFF:
                if run_start is None:
                    run_start = index
            elif run_start is not None:
                run_length = index - run_start
                if run_length > best_length:
                    best_start = run_start
                    best_length = run_length
                run_start = None
        if best_start is None:
            for offset in range(len(haystack) - length + 1):
                if self.matches_at(haystack, offset):
                    yield offset
            return
        needle = self.values[best_start : best_start + best_length]
        start = 0
        maximum = len(haystack) - length
        while start <= maximum:
            found = haystack.find(needle, start + best_start)
            if found < 0:
                return
            offset = found - best_start
            if offset >= start and offset <= maximum and self.matches_at(haystack, offset):
                yield offset
            start = max(start + 1, offset + 1)


class WindowsApi:
    """Typed kernel32 bindings, initialized lazily for testability."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("实时内存引擎只支持 Windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32 = self.kernel32

        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        k32.Module32FirstW.restype = wintypes.BOOL
        k32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
        k32.Module32NextW.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.ReadProcessMemory.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k32.ReadProcessMemory.restype = wintypes.BOOL
        k32.WriteProcessMemory.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k32.WriteProcessMemory.restype = wintypes.BOOL
        k32.VirtualProtectEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_size_t,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.VirtualProtectEx.restype = wintypes.BOOL
        k32.VirtualAllocEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_size_t,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        k32.VirtualAllocEx.restype = ctypes.c_void_p
        k32.VirtualFreeEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_size_t,
            wintypes.DWORD,
        ]
        k32.VirtualFreeEx.restype = wintypes.BOOL
        k32.VirtualQueryEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(MEMORY_BASIC_INFORMATION),
            ctypes.c_size_t,
        ]
        k32.VirtualQueryEx.restype = ctypes.c_size_t
        k32.FlushInstructionCache.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
        k32.FlushInstructionCache.restype = wintypes.BOOL
        k32.GetSystemInfo.argtypes = [ctypes.POINTER(SYSTEM_INFO)]
        k32.GetSystemInfo.restype = None
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL

    @staticmethod
    def last_error(prefix: str) -> OSError:
        code = ctypes.get_last_error()
        return ctypes.WinError(code, f"{prefix}（Win32 错误 {code}）")


def _valid_handle(handle) -> bool:
    value = ctypes.cast(handle, ctypes.c_void_p).value if handle else None
    return value not in (None, 0, INVALID_HANDLE_VALUE)


def iter_processes(api: WindowsApi | None = None) -> Iterator[ProcessInfo]:
    api = api or WindowsApi()
    snapshot = api.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not _valid_handle(snapshot):
        raise ProcessAccessError(str(api.last_error("无法枚举进程")))
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        success = api.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            yield ProcessInfo(int(entry.th32ProcessID), entry.szExeFile)
            success = api.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        api.kernel32.CloseHandle(snapshot)


def find_process(executable: str = PALWORLD_PROCESS_NAME, api: WindowsApi | None = None) -> ProcessInfo:
    wanted = executable.casefold()
    matches = [process for process in iter_processes(api) if process.executable.casefold() == wanted]
    if not matches:
        raise ProcessNotFoundError(f"没有找到游戏进程 {executable}")
    return max(matches, key=lambda process: process.pid)


class ProcessMemory:
    def __init__(self, pid: int, *, writable: bool = False, api: WindowsApi | None = None):
        self.api = api or WindowsApi()
        self.pid = int(pid)
        self.writable = bool(writable)
        access = PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ | SYNCHRONIZE
        if writable:
            access |= PROCESS_VM_OPERATION | PROCESS_VM_WRITE
        self.handle = self.api.kernel32.OpenProcess(access, False, self.pid)
        if not _valid_handle(self.handle):
            hint = "；如果游戏以管理员身份运行，请同样以管理员身份运行编辑器" if writable else ""
            raise ProcessAccessError(f"无法打开游戏进程 PID {self.pid}{hint}：{self.api.last_error('OpenProcess')}")
        self._closed = False
        self._lock = threading.RLock()

    @classmethod
    def attach_palworld(cls, *, writable: bool = False) -> "ProcessMemory":
        process = find_process(PALWORLD_PROCESS_NAME)
        return cls(process.pid, writable=writable)

    def close(self):
        with self._lock:
            if not self._closed and _valid_handle(self.handle):
                self.api.kernel32.CloseHandle(self.handle)
            self._closed = True
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def _require_open(self):
        if self._closed or not _valid_handle(self.handle):
            raise ProcessAccessError("游戏进程句柄已经关闭")

    def is_alive(self) -> bool:
        self._require_open()
        code = wintypes.DWORD()
        if not self.api.kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE

    def modules(self) -> list[ModuleInfo]:
        self._require_open()
        flags = TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32
        snapshot = self.api.kernel32.CreateToolhelp32Snapshot(flags, self.pid)
        if not _valid_handle(snapshot):
            raise ProcessAccessError(str(self.api.last_error("无法枚举游戏模块")))
        result = []
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            success = self.api.kernel32.Module32FirstW(snapshot, ctypes.byref(entry))
            while success:
                base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
                result.append(
                    ModuleInfo(
                        name=entry.szModule,
                        path=Path(entry.szExePath),
                        base=int(base),
                        size=int(entry.modBaseSize),
                    )
                )
                success = self.api.kernel32.Module32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.api.kernel32.CloseHandle(snapshot)
        return result

    def module(self, name: str = PALWORLD_PROCESS_NAME) -> ModuleInfo:
        wanted = name.casefold()
        for module in self.modules():
            if module.name.casefold() == wanted:
                return module
        raise ProcessNotFoundError(f"游戏进程中没有找到模块 {name}")

    def read(self, address: int, size: int) -> bytes:
        self._require_open()
        if address <= 0 or size < 0:
            raise ValueError("无效内存读取范围")
        if size == 0:
            return b""
        buffer = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t()
        success = self.api.kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(read),
        )
        if not success or read.value != size:
            raise MemoryReadError(
                f"读取游戏内存失败：0x{address:X}，请求 {size} 字节，实际 {read.value} 字节"
            )
        return bytes(buffer)

    def read_u64(self, address: int) -> int:
        """Read one little-endian unsigned 64-bit value from the game."""
        return struct.unpack("<Q", self.read(address, 8))[0]

    def write(self, address: int, data: bytes, *, executable: bool = False):
        self._require_open()
        if not self.writable:
            raise MemoryWriteError("当前连接是只读模式，拒绝写入游戏内存")
        payload = bytes(data)
        if address <= 0 or not payload:
            raise ValueError("无效内存写入范围")
        old_protect = wintypes.DWORD()
        changed_protection = False
        if executable:
            changed_protection = bool(
                self.api.kernel32.VirtualProtectEx(
                    self.handle,
                    ctypes.c_void_p(address),
                    len(payload),
                    PAGE_EXECUTE_READWRITE,
                    ctypes.byref(old_protect),
                )
            )
            if not changed_protection:
                raise MemoryWriteError(str(self.api.last_error("无法修改代码页保护")))
        try:
            source = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
            written = ctypes.c_size_t()
            success = self.api.kernel32.WriteProcessMemory(
                self.handle,
                ctypes.c_void_p(address),
                source,
                len(payload),
                ctypes.byref(written),
            )
            if not success or written.value != len(payload):
                raise MemoryWriteError(
                    f"写入游戏内存失败：0x{address:X}，请求 {len(payload)} 字节，实际 {written.value} 字节"
                )
            if executable:
                self.api.kernel32.FlushInstructionCache(
                    self.handle,
                    ctypes.c_void_p(address),
                    len(payload),
                )
        finally:
            if changed_protection:
                restored = wintypes.DWORD()
                self.api.kernel32.VirtualProtectEx(
                    self.handle,
                    ctypes.c_void_p(address),
                    len(payload),
                    old_protect.value,
                    ctypes.byref(restored),
                )

    def allocate(self, size: int, *, near: int | None = None) -> int:
        self._require_open()
        if not self.writable:
            raise MemoryWriteError("当前连接是只读模式，拒绝分配游戏内存")
        if size <= 0:
            raise ValueError("分配大小必须大于零")
        if near is None:
            address = self.api.kernel32.VirtualAllocEx(
                self.handle,
                None,
                size,
                MEM_RESERVE | MEM_COMMIT,
                PAGE_EXECUTE_READWRITE,
            )
            if not address:
                raise MemoryWriteError(str(self.api.last_error("无法在游戏中分配内存")))
            return int(address)
        return self._allocate_near(size, near)

    def _allocate_near(self, size: int, near: int) -> int:
        system = SYSTEM_INFO()
        self.api.kernel32.GetSystemInfo(ctypes.byref(system))
        granularity = max(int(system.dwAllocationGranularity), 0x10000)
        low = max(0x10000, near - 0x70000000)
        high = min(int(system.lpMaximumApplicationAddress or (near + 0x70000000)), near + 0x70000000)
        candidates = []
        distance = 0
        while distance <= 0x70000000:
            for candidate in (near + distance, near - distance):
                aligned = candidate - (candidate % granularity)
                if low <= aligned <= high:
                    candidates.append(aligned)
            distance += granularity * 16
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            address = self.api.kernel32.VirtualAllocEx(
                self.handle,
                ctypes.c_void_p(candidate),
                size,
                MEM_RESERVE | MEM_COMMIT,
                PAGE_EXECUTE_READWRITE,
            )
            if address and abs(int(address) - near) <= 0x7FFFFFFF:
                return int(address)
        raise MemoryWriteError("无法在补丁地址 ±2 GB 内分配代码空间")

    def free(self, address: int):
        self._require_open()
        if not self.writable:
            raise MemoryWriteError("当前连接是只读模式，拒绝释放游戏内存")
        if address and not self.api.kernel32.VirtualFreeEx(
            self.handle,
            ctypes.c_void_p(address),
            0,
            MEM_RELEASE,
        ):
            raise MemoryWriteError(str(self.api.last_error("释放游戏内存失败")))

    def module_sections(self, module: ModuleInfo | None = None) -> list[ModuleSection]:
        module = module or self.module()
        header = self.read(module.base, min(module.size, 0x2000))
        if header[:2] != b"MZ":
            raise TrainerError("游戏模块缺少 MZ 头")
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        if pe_offset + 24 > len(header):
            header = self.read(module.base, min(module.size, pe_offset + 0x1000))
        if header[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise TrainerError("游戏模块缺少 PE 头")
        number_of_sections = struct.unpack_from("<H", header, pe_offset + 6)[0]
        optional_header_size = struct.unpack_from("<H", header, pe_offset + 20)[0]
        section_offset = pe_offset + 24 + optional_header_size
        required = section_offset + number_of_sections * 40
        if required > len(header):
            header = self.read(module.base, min(module.size, required))
        result = []
        for index in range(number_of_sections):
            offset = section_offset + index * 40
            name = header[offset : offset + 8].rstrip(b"\0").decode("ascii", errors="replace")
            virtual_size, virtual_address, raw_size = struct.unpack_from("<III", header, offset + 8)
            characteristics = struct.unpack_from("<I", header, offset + 36)[0]
            size = min(max(virtual_size, raw_size), max(0, module.size - virtual_address))
            if size > 0:
                result.append(
                    ModuleSection(
                        name=name,
                        address=module.base + virtual_address,
                        size=size,
                        characteristics=characteristics,
                    )
                )
        return result

    def scan(
        self,
        pattern: AobPattern | str,
        *,
        module: ModuleInfo | None = None,
        section_names: Sequence[str] = (".text",),
        executable_only: bool = True,
        chunk_size: int = 8 * 1024 * 1024,
    ) -> list[int]:
        pattern = AobPattern.parse(pattern) if isinstance(pattern, str) else pattern
        module = module or self.module()
        wanted = {name.casefold() for name in section_names}
        sections = [
            section
            for section in self.module_sections(module)
            if (not wanted or section.name.casefold() in wanted)
            and (not executable_only or section.executable)
        ]
        matches = []
        overlap = max(0, len(pattern) - 1)
        for section in sections:
            offset = 0
            tail = b""
            while offset < section.size:
                size = min(chunk_size, section.size - offset)
                address = section.address + offset
                block = self.read(address, size)
                combined = tail + block
                combined_base = address - len(tail)
                for match in pattern.finditer(combined):
                    absolute = combined_base + match
                    if section.address <= absolute < section.address + section.size:
                        matches.append(absolute)
                tail = combined[-overlap:] if overlap else b""
                offset += size
        return sorted(set(matches))

    def scan_unique(self, pattern: AobPattern | str, **kwargs) -> int:
        parsed = AobPattern.parse(pattern) if isinstance(pattern, str) else pattern
        matches = self.scan(parsed, **kwargs)
        if not matches:
            raise PatternNotFoundError(f"没有找到特征码：{parsed.source}")
        if len(matches) != 1:
            locations = "、".join(f"0x{address:X}" for address in matches[:8])
            raise PatternAmbiguousError(
                f"特征码不是唯一匹配（共 {len(matches)} 个）：{parsed.source}\n{locations}"
            )
        return matches[0]


@dataclass
class MemoryPatch:
    name: str
    address: int
    enabled_bytes: bytes
    expected_bytes: tuple[bytes, ...]
    original_bytes: bytes | None = None
    enabled: bool = False

    def enable(self, process: ProcessMemory):
        if not self.enabled_bytes:
            raise ValueError("补丁内容不能为空")
        current = process.read(self.address, len(self.enabled_bytes))
        if current == self.enabled_bytes:
            self.enabled = True
            return
        allowed = tuple(value[: len(self.enabled_bytes)] for value in self.expected_bytes)
        if current not in allowed:
            raise PatchConflictError(
                f"{self.name} 的目标指令与适配数据不一致："
                f"地址 0x{self.address:X}，实际 {current.hex(' ').upper()}"
            )
        self.original_bytes = current
        process.write(self.address, self.enabled_bytes, executable=True)
        verify = process.read(self.address, len(self.enabled_bytes))
        if verify != self.enabled_bytes:
            raise MemoryWriteError(f"{self.name} 写入后校验失败")
        self.enabled = True

    def disable(self, process: ProcessMemory):
        if not self.enabled:
            return
        if self.original_bytes is None:
            raise PatchConflictError(f"{self.name} 缺少原始指令备份，拒绝恢复")
        current = process.read(self.address, len(self.enabled_bytes))
        if current != self.enabled_bytes:
            raise PatchConflictError(
                f"{self.name} 已被其他程序改写，拒绝覆盖："
                f"地址 0x{self.address:X}，实际 {current.hex(' ').upper()}"
            )
        process.write(self.address, self.original_bytes, executable=True)
        verify = process.read(self.address, len(self.original_bytes))
        if verify != self.original_bytes:
            raise MemoryWriteError(f"{self.name} 恢复后校验失败")
        self.enabled = False


class PatchTransaction:
    def __init__(self, process: ProcessMemory):
        if not process.writable:
            raise MemoryWriteError("补丁事务需要可写游戏连接")
        self.process = process
        self._patches: list[MemoryPatch] = []
        self._allocations: list[int] = []
        self._lock = threading.RLock()

    def apply(self, patch: MemoryPatch):
        with self._lock:
            patch.enable(self.process)
            if patch not in self._patches:
                self._patches.append(patch)

    def allocate(self, size: int, *, near: int | None = None) -> int:
        with self._lock:
            address = self.process.allocate(size, near=near)
            self._allocations.append(address)
            return address

    def restore_all(self) -> list[str]:
        errors: list[str] = []
        with self._lock:
            for attempt in range(3):
                errors.clear()
                for patch in reversed(self._patches):
                    if not patch.enabled:
                        continue
                    try:
                        patch.disable(self.process)
                    except Exception as exc:
                        errors.append(f"{patch.name}：{exc}")
                if not any(patch.enabled for patch in self._patches):
                    break
                time.sleep(0.05)

            self._patches = [patch for patch in self._patches if patch.enabled]
            # Never release a remote code cave while the game is running.  A
            # worker can already have fetched the JMP just before its entry
            # bytes are restored; freeing the page then creates a rare use-
            # after-free crash.  These allocations are tiny and Windows
            # reclaims them automatically when Palworld exits.
            self._allocations.clear()
        return errors


def relative_jump(source: int, destination: int, instruction_size: int = 5) -> bytes:
    displacement = destination - (source + instruction_size)
    if not -(2**31) <= displacement < 2**31:
        raise ValueError("跳转目标超出 rel32 范围")
    return b"\xE9" + struct.pack("<i", displacement)


def absolute_jump(destination: int) -> bytes:
    return b"\xFF\x25\x00\x00\x00\x00" + struct.pack("<Q", destination)


def format_addresses(addresses: Iterable[int]) -> str:
    return "、".join(f"0x{address:X}" for address in addresses)
