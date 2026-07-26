import copy
import unittest

from backend import (
    MAX_PAL_LEVEL,
    PASSIVE_DATABASE,
    TOP_PASSIVE_PRESETS,
    EditorError,
    PartnerEntity,
)


def partner_data():
    return {
        "value": {
            "RawData": {
                "value": {
                    "object": {
                        "SaveParameter": {
                            "value": {
                                "CharacterID": {"value": "SheepBall"},
                                "Level": {
                                    "id": None,
                                    "value": {"value": 10, "type": "None"},
                                    "type": "ByteProperty",
                                },
                                "Exp": {"id": None, "value": 100, "type": "IntProperty"},
                                "PassiveSkillList": {
                                    "array_type": "NameProperty",
                                    "id": None,
                                    "value": {"values": ["Legend"]},
                                    "type": "ArrayProperty",
                                },
                            }
                        }
                    }
                }
            }
        }
    }


def character_data(code):
    data = partner_data()
    data["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]["CharacterID"]["value"] = code
    return data


class PassiveEditingTests(unittest.TestCase):
    def test_each_partner_keeps_its_own_passives(self):
        first = PartnerEntity(partner_data())
        second = PartnerEntity(copy.deepcopy(partner_data()))
        first.SetPassives(["PAL_ALLAttack_up3", "Legend"])
        self.assertEqual(first.GetPassives(), ["PAL_ALLAttack_up3", "Legend"])
        self.assertEqual(second.GetPassives(), ["Legend"])

    def test_duplicate_and_fifth_passive_are_rejected(self):
        entity = PartnerEntity(partner_data())
        with self.assertRaises(EditorError):
            entity.SetPassives(["Legend", "Legend"])
        with self.assertRaises(EditorError):
            entity.SetPassives(["a", "b", "c", "d", "e"])

    def test_all_preset_ids_exist_in_current_database(self):
        missing = {
            code
            for preset in TOP_PASSIVE_PRESETS.values()
            for code in preset
            if code not in PASSIVE_DATABASE
        }
        self.assertEqual(missing, set())

    def test_max_level_updates_level_and_experience(self):
        entity = PartnerEntity(partner_data())
        entity.SetLevel(MAX_PAL_LEVEL)
        self.assertEqual(entity.GetLevel(), MAX_PAL_LEVEL)
        self.assertGreater(entity._obj["Exp"]["value"], 100)

    def test_unknown_npc_is_not_treated_as_supported_pal(self):
        entity = PartnerEntity(character_data("Police_Rifle_Crime_Lv5"))
        self.assertFalse(entity.IsSupportedPal())

    def test_known_pal_is_supported(self):
        entity = PartnerEntity(character_data("SheepBall"))
        self.assertTrue(entity.IsSupportedPal())


if __name__ == "__main__":
    unittest.main()
