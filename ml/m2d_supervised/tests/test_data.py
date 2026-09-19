import tempfile
import unittest
from pathlib import Path

import numpy as np

from m2d_supervised.data import (
    RuntimePaths,
    donate_class_weights,
    enes_class_weights,
    epoch_plan,
    load_feature,
    used_frames,
)


class DataPreparationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "preprocessing": {
                "maximum_frames": 32,
                "frame_multiple": 16,
                "minimum_frames": 16,
                "normalization_mean": 0.0,
                "normalization_std": 1.0,
            },
            "datasets": {"donate": {"root": "donate"}},
        }

    def test_center_crop_uses_only_complete_patch_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "donate"
            dataset.mkdir()
            values = np.broadcast_to(np.arange(49, dtype=np.float32), (1, 80, 49)).copy()
            np.save(dataset / "feature.npy", values)
            row = {"sample_id": "s1", "file_name": "feature.npy", "valid_frames": "49"}
            paths = RuntimePaths(root, root / "out", root / "m2d", root / "repo")
            crop = load_feature(
                paths,
                self.config,
                "donate",
                row,
                seed=42,
                phase="validation",
                epoch=0,
                occurrence=0,
            )
            self.assertEqual(tuple(crop.shape), (1, 80, 32))
            np.testing.assert_array_equal(crop.numpy()[0, 0], np.arange(8, 40))
            self.assertEqual(used_frames(row, self.config), 32)

    def test_training_crop_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "donate"
            dataset.mkdir()
            values = np.broadcast_to(np.arange(64, dtype=np.float32), (1, 80, 64)).copy()
            np.save(dataset / "feature.npy", values)
            row = {"sample_id": "s1", "file_name": "feature.npy", "valid_frames": "64"}
            paths = RuntimePaths(root, root / "out", root / "m2d", root / "repo")
            first = load_feature(
                paths,
                self.config,
                "donate",
                row,
                seed=42,
                phase="train",
                epoch=2,
                occurrence=3,
            )
            second = load_feature(
                paths,
                self.config,
                "donate",
                row,
                seed=42,
                phase="train",
                epoch=2,
                occurrence=3,
            )
            np.testing.assert_array_equal(first.numpy(), second.numpy())

    def test_enes_epoch_plan_matches_donate_count(self) -> None:
        manifests = {
            "donate": {"train": [{"sample_id": f"d{index}"} for index in range(7)]},
            "enes": {
                "train": [
                    {"sample_id": "e1", "baby_id": "b1", "bout_id": "x"},
                    {"sample_id": "e2", "baby_id": "b1", "bout_id": "y"},
                    {"sample_id": "e3", "baby_id": "b2", "bout_id": "z"},
                ]
            },
        }
        first = epoch_plan(manifests, seed=42, phase="train", epoch=1)
        second = epoch_plan(manifests, seed=42, phase="train", epoch=1)
        self.assertEqual(first, second)
        self.assertEqual(len(first["donate"]), 7)
        self.assertEqual(len(first["enes"]), 7)

    def test_class_weights_have_expected_mean_one(self) -> None:
        donate = [
            {"label": "a"},
            {"label": "b"},
            {"label": "b"},
            {"label": "b"},
        ]
        donate_weights = donate_class_weights(donate, ["a", "b"]).numpy()
        self.assertAlmostEqual(float(0.25 * donate_weights[0] + 0.75 * donate_weights[1]), 1.0)

        enes = [
            {"baby_id": "b1", "bout_id": "x", "label_index": "0"},
            {"baby_id": "b1", "bout_id": "x", "label_index": "0"},
            {"baby_id": "b1", "bout_id": "y", "label_index": "1"},
            {"baby_id": "b2", "bout_id": "z", "label_index": "1"},
        ]
        enes_weights = enes_class_weights(enes, ["a", "b"]).numpy()
        self.assertAlmostEqual(float(0.25 * enes_weights[0] + 0.75 * enes_weights[1]), 1.0)


if __name__ == "__main__":
    unittest.main()
