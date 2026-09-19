import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import m2d_supervised.post_training as runner
from m2d_supervised.data import RuntimePaths
from m2d_supervised.engine import metrics_from_predictions
from m2d_supervised.util import atomic_json, code_hash, read_json


class PostTrainingTest(unittest.TestCase):
    def test_two_completed_models_gate_test_and_completed_pipeline_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = RuntimePaths(root, root / "output", root / "source", root)
            post = paths.output_root / "post_training"
            config_path = Path(__file__).parents[1] / "config/experiment_v1.json"
            config = read_json(config_path)
            config["datasets"]["donate"]["counts"]["test"] = 2
            labels = config["datasets"]["donate"]["labels"]
            digest = code_hash(Path(runner.__file__).parent, config_path)[0]
            atomic_json(post / "smoke.json", {"status": "passed", "code_sha256": digest})
            plan = {
                "selected": "B",
                "baseline": "A",
                "seed": 42,
                "final_runs": [{"recipe": r, "epochs": 1} for r in "BA"],
                "evaluation": [{"recipe": r, "dataset": "donate"} for r in "BA"],
            }
            predictions = [
                {
                    "sample_id": str(i),
                    "group_id": str(i),
                    "true_index": i,
                    "predicted_index": 0,
                    "probabilities": [1, 0, 0, 0, 0],
                }
                for i in range(2)
            ]
            rows = [{"label": labels[i], "sample_id": str(i)} for i in range(2)]
            events = []

            def compare(*_):
                atomic_json(post / "comparison.json", {})
                atomic_json(post / "selection_plan.json", plan)
                return plan

            def train(_config, _path, _paths, _plan, recipe, **_kwargs):
                self.assertTrue((post / "selection_plan.json").exists())
                self.assertFalse((post / "evaluation_plan.json").exists())
                events.append("train-" + recipe)
                value = {"status": "completed", "artifact_sha256": {}}
                atomic_json(post / "final" / recipe / "completion.json", value)
                return value

            def manifest(dataset, split):
                self.assertEqual(dataset, "donate")  # Unselected Enes is never opened.
                if split == "test":
                    self.assertTrue((post / "final/A/completion.json").exists())
                    self.assertTrue((post / "final/B/completion.json").exists())
                    self.assertTrue((post / "evaluation_plan.json").exists())
                    events.append("test")
                return rows

            def export(*_args, **_kwargs):
                events.append("export")
                atomic_json(
                    post / "export/selected/completion.json",
                    {"status": "completed", "artifact_sha256": {}},
                )

            with (
                patch.object(runner.RuntimeGuard, "check"),
                patch.object(runner, "compare_development", side_effect=compare),
                patch.object(runner, "prepare_probes", return_value=([], [])),
                patch.object(runner, "final_run", side_effect=train) as final_mock,
                patch.object(runner.ManifestStore, "load", side_effect=manifest),
                patch.object(runner, "load_final", return_value=Mock()),
                patch.object(
                    runner,
                    "evaluate",
                    return_value=(metrics_from_predictions(predictions, labels), predictions),
                ) as eval_mock,
                patch.object(runner, "export_selected", side_effect=export),
                patch.object(runner.torch.mps, "empty_cache"),
            ):
                completed = runner.run_post_training(config, config_path, paths)
                self.assertEqual(completed["status"], "completed")
                self.assertEqual(events, ["train-B", "train-A", "test", "test", "export"])
                self.assertEqual(runner.run_post_training(config, config_path, paths), completed)
                self.assertEqual(final_mock.call_count, 2)
                self.assertEqual(eval_mock.call_count, 2)
                atomic_json(post / "evaluation_summary.json", {"changed": True})
                with self.assertRaisesRegex(RuntimeError, "Artifact integrity"):
                    runner.run_post_training(config, config_path, paths)


if __name__ == "__main__":
    unittest.main()
