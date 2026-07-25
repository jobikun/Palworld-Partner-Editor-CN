import unittest

from trainer_features import FEATURES, FEATURE_BY_ID


class TrainerFeatureContractTests(unittest.TestCase):
    def test_contract_contains_exactly_48_unique_features(self):
        self.assertEqual(len(FEATURES), 48)
        self.assertEqual(len(FEATURE_BY_ID), 48)

    def test_hotkeys_are_unique(self):
        hotkeys = [feature.hotkey for feature in FEATURES]
        self.assertEqual(len(hotkeys), len(set(hotkeys)))

    def test_all_value_features_have_bounds_and_defaults(self):
        for feature in FEATURES:
            if not feature.has_value:
                continue
            self.assertIsNotNone(feature.default, feature.feature_id)
            self.assertIsNotNone(feature.minimum, feature.feature_id)
            self.assertIsNotNone(feature.maximum, feature.feature_id)
            self.assertLessEqual(feature.minimum, feature.default, feature.feature_id)
            self.assertLessEqual(feature.default, feature.maximum, feature.feature_id)


if __name__ == "__main__":
    unittest.main()
