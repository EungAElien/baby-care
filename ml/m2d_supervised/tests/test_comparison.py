import copy
import tempfile
import unittest
from pathlib import Path

from m2d_supervised.comparison import (
    check_predictions,
    choose_recipe,
    freeze_json,
    paired_group_bootstrap,
)
from m2d_supervised.util import read_json

CONFIG = read_json(Path(__file__).parents[1] / "config" / "experiment_v1.json")


def metric(score, recall=0.5):
    return {
        "macro_f1": score,
        "per_class": {
            label: {"precision": 0.5, "recall": recall, "f1": score, "support": 3}
            for label in CONFIG["datasets"]["donate"]["labels"]
        },
    }


class ComparisonTest(unittest.TestCase):
    def test_tie_b_and_c_thresholds(self):
        values = {r: [metric(0.25)] * 3 for r in "ABC"}
        self.assertEqual(choose_recipe(values, CONFIG)["selected"], "B")
        values["C"] = [metric(0.3)] * 3
        self.assertEqual(choose_recipe(values, CONFIG)["selected"], "C")
        values["C"] = [metric(0.3, recall=0.44)] * 3
        result = choose_recipe(values, CONFIG)
        self.assertEqual(result["selected"], "B")
        self.assertFalse(result["c_acceptance_checks"]["every_non_hungry_recall"])

    def test_c_compares_to_best_standalone_not_always_b(self):
        values = {"A": [metric(0.4)] * 3, "B": [metric(0.25)] * 3, "C": [metric(0.3)] * 3}
        result = choose_recipe(values, CONFIG)
        self.assertEqual(result["selected"], "A")
        self.assertEqual(result["baseline"], "B")
        values["C"] = [metric(0.8), metric(0.39), metric(0.39)]
        self.assertFalse(choose_recipe(values, CONFIG)["c_acceptance_checks"]["improved_seeds"])

    def test_group_bootstrap_paired_deterministic_and_alignment(self):
        rows = [
            {
                "sample_id": str(i),
                "group_id": str(i // 2),
                "true_index": i % 2,
                "predicted_index": i % 2,
            }
            for i in range(12)
        ]
        predictions = {"A": [rows] * 3, "B": [rows] * 3}
        a = paired_group_bootstrap(predictions, ["a", "b"])
        self.assertEqual(a, paired_group_bootstrap(predictions, ["a", "b"]))
        self.assertEqual(a["paired_difference_ci95"]["B-A"], [0, 0])
        changed = {
            name: [copy.deepcopy(rows) for rows in runs] for name, runs in predictions.items()
        }
        changed["B"][0][0]["group_id"] = "different"
        with self.assertRaisesRegex(RuntimeError, "not paired"):
            paired_group_bootstrap(changed, ["a", "b"])

    def test_predictions_reject_duplicates_and_wrong_probabilities(self):
        manifest = [{"sample_id": "s", "group_id": "g", "label_index": "0"}]
        rows = [
            {
                "sample_id": "s",
                "group_id": "g",
                "true_index": 0,
                "predicted_index": 0,
                "probabilities": [0.7, 0.3],
            }
        ]
        self.assertEqual(check_predictions(rows, manifest, ["a", "b"]), rows)
        with self.assertRaises(RuntimeError):
            check_predictions(rows * 2, manifest, ["a", "b"])
        rows[0]["probabilities"] = [0.3, 0.7]
        with self.assertRaises(RuntimeError):
            check_predictions(rows, manifest, ["a", "b"])

    def test_decision_is_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            freeze_json(path, {"selected": "A"})
            freeze_json(path, {"selected": "A"})
            with self.assertRaisesRegex(RuntimeError, "Frozen record"):
                freeze_json(path, {"selected": "B"})


if __name__ == "__main__":
    unittest.main()
