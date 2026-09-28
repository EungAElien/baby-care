import unittest
from unittest.mock import patch

import test_training
import torch

from m2d_supervised.checkpoint import atomic_torch, state_digest
from m2d_supervised.inference import load_bundle
from m2d_supervised.util import atomic_json, sha256_file


class ExportingTest(unittest.TestCase):
    setUp = test_training.TrainingTest.setUp
    model = test_training.TrainingTest.model

    def test_bundle_loads_full_weights_without_initial_checkpoint(self):
        model = self.model(("donate",))
        bundle = self.root / "bundle"
        bundle.mkdir()
        atomic_torch(bundle / "model.pt", {"full_state_dict": model.state_dict()}, 0)
        atomic_json(bundle / "config.json", self.config)
        metadata = {
            "labels": model.labels,
            "trained_heads": ["donate"],
            "artifact_sha256": {
                name: sha256_file(bundle / name) for name in ("model.pt", "config.json")
            },
        }
        atomic_json(bundle / "metadata.json", metadata)
        with (
            patch("m2d_supervised.inference.verify_m2d_source"),
            patch(
                "m2d_supervised.inference.build_encoder", return_value=test_training.TinyEncoder()
            ),
            patch("m2d_supervised.model.load_encoder", side_effect=AssertionError("Initial load")),
        ):
            restored, _, _ = load_bundle(bundle, self.root / "missing_architecture_fixture")
            self.assertEqual(state_digest(model.state_dict()), state_digest(restored.state_dict()))
            with self.assertRaises(RuntimeError):
                restored(torch.zeros(1, 1, 80, 32), "enes")
            atomic_json(bundle / "config.json", {"changed": True})
            with self.assertRaisesRegex(RuntimeError, "Bundle artifact changed"):
                load_bundle(bundle, self.root / "missing_architecture_fixture")


if __name__ == "__main__":
    unittest.main()
