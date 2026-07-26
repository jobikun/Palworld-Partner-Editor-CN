import struct
import unittest
from types import SimpleNamespace

from trainer_runtime import (
    _first_call_target_with_prefix,
    _is_build_material_copy_constructor,
    _resolve_stealth_branch,
)


class FakeProcess:
    def __init__(self):
        self.base = 0x100000
        self.data = bytearray(b"\x90" * 0x10000)

    def module(self):
        return SimpleNamespace(base=self.base, size=len(self.data))

    def read(self, address, size):
        offset = address - self.base
        if offset < 0 or offset + size > len(self.data):
            raise RuntimeError("out of range")
        return bytes(self.data[offset : offset + size])

    def write_bytes(self, address, payload):
        offset = address - self.base
        self.data[offset : offset + len(payload)] = payload

    def write_call(self, address, target):
        displacement = target - (address + 5)
        self.write_bytes(address, b"\xE8" + struct.pack("<i", displacement))


class TrainerResolutionTests(unittest.TestCase):
    def test_reflected_wrapper_call_is_resolved_by_callee_prefix(self):
        process = FakeProcess()
        wrapper = process.base + 0x1000
        target = process.base + 0x2000
        process.write_call(wrapper + 7, target)
        process.write_bytes(target, bytes.fromhex("48 89 5C 24 08 57"))
        result = _first_call_target_with_prefix(
            process,
            wrapper,
            bytes.fromhex("48 89 5C 24 08 57"),
            search_size=0x40,
        )
        self.assertEqual(result, target)

    def test_zero_wrapper_is_not_read(self):
        process = FakeProcess()
        self.assertEqual(
            _first_call_target_with_prefix(
                process,
                0,
                b"\x48",
                search_size=0x20,
            ),
            0,
        )

    def test_build_material_hook_uses_copy_constructor(self):
        process = FakeProcess()
        address = process.base + 0x2900
        process.write_bytes(
            address - 0x9A,
            bytes.fromhex(
                "48 8D 05 11 22 33 44 "
                "48 89 01 48 8B 42 08 48 89 41 08"
            ),
        )
        self.assertTrue(
            _is_build_material_copy_constructor(process, address)
        )

    def test_plain_row_copy_is_not_build_material_target(self):
        process = FakeProcess()
        address = process.base + 0x2900
        process.write_bytes(
            address - 0x90,
            bytes.fromhex("48 8B 42 08 48 89 41 08"),
        )
        self.assertFalse(
            _is_build_material_copy_constructor(process, address)
        )

    def test_sight_check_resolves_cone_rejection_branch(self):
        process = FakeProcess()
        sight = process.base + 0x3000
        helper = process.base + 0x4000
        cone = process.base + 0x5000
        process.write_call(sight + 0x20, helper)
        process.write_call(helper + 0x70, cone)
        branch = helper + 0x75
        process.write_bytes(branch, bytes.fromhex("84 C0 74"))
        self.assertEqual(
            _resolve_stealth_branch(process, sight, cone),
            branch,
        )

    def test_ambiguous_sight_branches_are_rejected(self):
        process = FakeProcess()
        sight = process.base + 0x3000
        cone = process.base + 0x5000
        for index, helper in enumerate(
            (process.base + 0x4000, process.base + 0x6000)
        ):
            process.write_call(sight + 0x20 + index * 8, helper)
            process.write_call(helper + 0x70, cone)
            process.write_bytes(helper + 0x75, bytes.fromhex("84 C0 74"))
        self.assertEqual(_resolve_stealth_branch(process, sight, cone), 0)


if __name__ == "__main__":
    unittest.main()
