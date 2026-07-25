import struct
import unittest

from memory_trainer import (
    AobPattern,
    PatchTransaction,
    ProcessMemory,
    absolute_jump,
    relative_jump,
)


class AobPatternTests(unittest.TestCase):
    def test_full_and_wildcard_bytes(self):
        pattern = AobPattern.parse("48 8B ?? A? ?F **")
        data = bytes.fromhex("00 48 8B 19 AB 2F FF 00")
        self.assertEqual(list(pattern.finditer(data)), [1])
        self.assertTrue(pattern.matches_at(data, 1))
        self.assertFalse(pattern.matches_at(data, 0))

    def test_multiple_matches_are_returned_in_order(self):
        pattern = AobPattern.parse("AA ?? CC")
        data = bytes.fromhex("AA 01 CC 00 AA FF CC")
        self.assertEqual(list(pattern.finditer(data)), [0, 4])

    def test_invalid_token_is_rejected(self):
        with self.assertRaises(ValueError):
            AobPattern.parse("48 XYZ 90")


class JumpEncodingTests(unittest.TestCase):
    def test_relative_jump_round_trip(self):
        source = 0x10000000
        target = 0x10001234
        encoded = relative_jump(source, target)
        self.assertEqual(encoded[0], 0xE9)
        displacement = struct.unpack("<i", encoded[1:])[0]
        self.assertEqual(source + 5 + displacement, target)

    def test_absolute_jump_contains_destination(self):
        target = 0x7FF612345678
        encoded = absolute_jump(target)
        self.assertEqual(encoded[:6], b"\xFF\x25\x00\x00\x00\x00")
        self.assertEqual(struct.unpack("<Q", encoded[6:])[0], target)


class PatchTransactionTests(unittest.TestCase):
    def test_live_code_caves_are_not_freed_on_restore(self):
        class FakeProcess:
            writable = True

            def __init__(self):
                self.freed = []

            def free(self, address):
                self.freed.append(address)

        process = FakeProcess()
        transaction = PatchTransaction(process)
        transaction._allocations.append(0x70000000)
        self.assertEqual(transaction.restore_all(), [])
        self.assertEqual(process.freed, [])


class ProcessMemoryTypedReadTests(unittest.TestCase):
    def test_read_u64_uses_little_endian(self):
        class FakeReader:
            def read(self, address, size):
                self.last_request = (address, size)
                return bytes.fromhex("8877665544332211")

        reader = FakeReader()
        value = ProcessMemory.read_u64(reader, 0x1234)
        self.assertEqual(value, 0x1122334455667788)
        self.assertEqual(reader.last_request, (0x1234, 8))


if __name__ == "__main__":
    unittest.main()
