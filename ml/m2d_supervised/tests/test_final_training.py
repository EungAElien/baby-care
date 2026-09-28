import copy
import unittest
from unittest.mock import Mock, patch

import numpy as np
import test_training
import torch

from m2d_supervised.checkpoint import state_digest
from m2d_supervised.data import RuntimePaths, load_feature
from m2d_supervised.final_training import final_run
from m2d_supervised.inference import center_input
from m2d_supervised.sealed_test import evaluate_once


class FinalTrainingTest(unittest.TestCase):
    setUp = test_training.TrainingTest.setUp
    model = test_training.TrainingTest.model

    def plan(self, epochs):
        return {"seed": 42, "final_runs": [{"recipe": "C", "epochs": epochs}]}

    def test_fixed_full_development_resume_across_phase_boundary(self):
        initial = self.model()
        with (
            patch(
                "m2d_supervised.final_training.ManifestStore.load",
                side_effect=lambda d, split: self.rows[d]
                if split == "development"
                else (_ for _ in ()).throw(AssertionError("Test accessed")),
            ),
            patch(
                "m2d_supervised.final_training.create_model",
                side_effect=lambda *a: copy.deepcopy(initial),
            ),
        ):
            full = final_run(
                self.config, self.config_path, self.paths, self.plan(2), "C", device="cpu"
            )
            other = RuntimePaths(self.root, self.root / "resumed", self.root / "m2d", self.root)
            paused = final_run(
                self.config,
                self.config_path,
                other,
                self.plan(2),
                "C",
                device="cpu",
                max_additional_updates=1,
            )
            self.assertEqual(paused["status"], "paused")
            resumed = final_run(
                self.config, self.config_path, other, self.plan(2), "C", device="cpu"
            )
            self.assertEqual(full["epochs_executed"], 2)
            self.assertEqual(resumed["updates_executed"], 2)
            a = torch.load(
                self.paths.output_root / "post_training/final/C/model.pt", weights_only=True
            )
            b = torch.load(other.output_root / "post_training/final/C/model.pt", weights_only=True)
            self.assertEqual(state_digest(a["partial_model"]), state_digest(b["partial_model"]))
            self.assertFalse(full["test_used"])
            # Completion is verified without another optimizer or model load.
            with patch("m2d_supervised.final_training.create_model", side_effect=AssertionError):
                self.assertEqual(
                    final_run(
                        self.config, self.config_path, other, self.plan(2), "C", device="cpu"
                    ),
                    resumed,
                )

    def test_one_epoch_does_not_force_finetuning(self):
        initial = self.model()
        with (
            patch(
                "m2d_supervised.final_training.ManifestStore.load",
                side_effect=lambda d, _: self.rows[d],
            ),
            patch(
                "m2d_supervised.final_training.create_model",
                side_effect=lambda *a: copy.deepcopy(initial),
            ),
        ):
            result = final_run(
                self.config, self.config_path, self.paths, self.plan(1), "C", device="cpu"
            )
            saved = torch.load(
                self.paths.output_root / "post_training/final/C/model.pt", weights_only=True
            )
            for name, value in saved["partial_model"].items():
                if name.startswith("encoder."):
                    self.assertTrue(torch.equal(value, initial.state_dict()[name]))
            self.assertEqual(result["epochs_executed"], 1)

    def test_inference_center_preprocessing_matches_training_eval(self):
        for row in self.rows["donate"]:
            array = np.load(self.root / "donate" / row["file_name"])
            train = load_feature(
                self.paths,
                self.config,
                "donate",
                row,
                seed=42,
                phase="validation",
                epoch=0,
                occurrence=0,
            )
            self.assertTrue(torch.equal(train, center_input(array, self.config)))
        with self.assertRaises(ValueError):
            center_input(np.zeros((1, 80, 31), np.float32), self.config)

    def test_sealed_test_once_and_failed_claim_never_repeats(self):
        operation = Mock(return_value={"metrics": {"macro_f1": 0.1}})
        directory = self.root / "sealed"
        first = evaluate_once(directory, {"plan": 1}, {"recipe": "A"}, operation)
        self.assertEqual(evaluate_once(directory, {"plan": 1}, {"recipe": "A"}, operation), first)
        operation.assert_called_once()
        with self.assertRaises(RuntimeError):
            evaluate_once(directory, {"plan": 2}, {"recipe": "A"}, operation)
        interrupted = Mock(side_effect=InterruptedError("power loss"))
        with self.assertRaises(InterruptedError):
            evaluate_once(self.root / "failed", {}, {}, interrupted)
        with self.assertRaisesRegex(RuntimeError, "AMBIGUOUS_SEALED_TEST"):
            evaluate_once(self.root / "failed", {}, {}, interrupted)
        interrupted.assert_called_once()


if __name__ == "__main__":
    unittest.main()
