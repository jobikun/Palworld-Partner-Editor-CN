import struct
import unittest

from memory_trainer import MemoryReadError
from unreal_reflection import (
    UnrealCoreAddresses,
    UnrealReflection,
    resolve_rip_target,
)


class FakeProcess:
    def __init__(self):
        self.memory = {}

    def put(self, address, data):
        for index, byte in enumerate(data):
            self.memory[address + index] = byte

    def read(self, address, size):
        return bytes(self.memory[address + index] for index in range(size))


class UnrealReflectionTests(unittest.TestCase):
    def test_rip_relative_target(self):
        process = FakeProcess()
        process.put(0x1004, struct.pack("<i", 0x1234))
        self.assertEqual(resolve_rip_target(process, 0x1004), 0x223C)

    def test_ansi_and_wide_fname_decode(self):
        process = FakeProcess()
        pool = 0x10000
        block = 0x20000
        process.put(pool + 0x10, struct.pack("<Q", block))


        process.put(block + 2, struct.pack("<H", 4 << 6) + b"Test")
        wide_index = 4
        wide_text = "中文"
        process.put(
            block + wide_index * 2,
            struct.pack("<H", (len(wide_text) << 6) | 1)
            + wide_text.encode("utf-16-le"),
        )
        reflection = UnrealReflection(
            process,
            UnrealCoreAddresses(
                guobject_array=0x30000,
                fname_pool=pool,
                fname_to_string=0x40000,
            ),
        )
        self.assertEqual(reflection.decode_fname(1), "Test")
        self.assertEqual(reflection.decode_fname(wide_index), wide_text)
        self.assertEqual(reflection.decode_fname(1, 3), "Test_2")

    def test_destroyed_object_is_skipped_during_iteration(self):
        class StaleObjectProcess:
            def read(self, _address, _size):
                raise MemoryReadError("object was destroyed")

        reflection = UnrealReflection(
            StaleObjectProcess(),
            UnrealCoreAddresses(
                guobject_array=0x30000,
                fname_pool=0x10000,
                fname_to_string=0x40000,
            ),
        )
        reflection.iter_object_addresses = lambda **_kwargs: iter(((7, 0x50000),))
        self.assertEqual(list(reflection.iter_objects()), [])


if __name__ == "__main__":
    unittest.main()
