import unittest
from unittest.mock import patch

from palworld_offsets import PalworldOffsets


class FakeReflection:
    def __init__(self):
        self.calls = []

    def property_offset(self, owner, name):
        self.calls.append((owner, name))
        return len(self.calls) * 4


class PalworldOffsetsTests(unittest.TestCase):
    def test_every_offset_is_reflected_by_name(self):
        reflection = FakeReflection()
        with patch(
            "palworld_offsets._resolve_money_value_offset",
            return_value=0x58,
        ):
            offsets = PalworldOffsets.resolve(reflection)
        values = offsets.as_dict()
        flat_values = []
        for value in values.values():
            flat_values.extend(value if isinstance(value, tuple) else (value,))


        self.assertEqual(len(flat_values), len(reflection.calls) + 1)
        self.assertTrue(all(value > 0 and value % 4 == 0 for value in flat_values))
        self.assertIn(
            ("PalIndividualCharacterSaveParameter", "Hp"),
            reflection.calls,
        )
        self.assertIn(("PalOptionWorldSettings", "PalCaptureRate"), reflection.calls)


if __name__ == "__main__":
    unittest.main()
