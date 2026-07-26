import unittest

from trainer_runtime import (
    FeatureState,
    OverheatWeaponLayout,
    PalTarget,
    _filter_owned_pals,
)


class TrainerRuntimeModelTests(unittest.TestCase):
    def test_feature_state_retains_numeric_value(self):
        state = FeatureState(False, 99999)
        state.enabled = True
        self.assertEqual(state.value, 99999)

    def test_pal_target_addresses_are_explicit(self):
        target = PalTarget(actor=1, component=2, individual=3, save=4)
        self.assertEqual((target.actor, target.component, target.individual, target.save), (1, 2, 3, 4))

    def test_only_local_players_pals_are_selected(self):
        local_uid = bytes.fromhex("00000000000000000000000001000000")
        wild_uid = b"\0" * 16
        targets = [
            PalTarget(actor=1, component=2, individual=3, save=0x1000),
            PalTarget(actor=5, component=6, individual=7, save=0x2000),
        ]

        class FakeProcess:
            def read(self, address, size):
                if size != 16:
                    raise AssertionError(size)
                return {
                    0x1010: local_uid,
                    0x2010: wild_uid,
                }[address]

        selected = _filter_owned_pals(FakeProcess(), targets, 0x10, local_uid)
        self.assertEqual(selected, targets[:1])

    def test_overheat_weapon_layout_tracks_inherited_weapon_classes(self):
        layout = OverheatWeaponLayout(
            base_class=1,
            meta_classes=(2, 3),
            weapon_classes=(4, 5),
            instances=(6,),
            heat_value=0x720,
            heat_per_shot=0x728,
            cooling_speed=0x730,
            is_overheated=0x738,
            displayed_heat=0x750,
            is_in_cool_time=0x6C1,
        )
        self.assertIn(5, layout.weapon_classes)
        self.assertEqual(layout.instances, (6,))


if __name__ == "__main__":
    unittest.main()
