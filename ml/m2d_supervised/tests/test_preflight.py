import unittest

from m2d_supervised.preflight import _planned_development_runs, _validate_dataset_splits


def row(sample: str, group: str, label: str) -> dict[str, str]:
    return {"sample_id": sample, "group_id": group, "label": label}


class FakeStore:
    def __init__(self, values: dict[str, list[dict[str, str]]]) -> None:
        self.values = values

    def load(self, dataset: str, name: str) -> list[dict[str, str]]:
        if dataset != "donate":
            raise AssertionError(dataset)
        return self.values[name]


class PreflightSplitTest(unittest.TestCase):
    def setUp(self) -> None:
        development = [row("a", "ga", "x"), row("b", "gb", "y"), row("c", "gc", "x")]
        test = [row("d", "gd", "y")]
        self.store = FakeStore(
            {
                "all": development + test,
                "development": development,
                "test": test,
                "fold0_train": development[1:],
                "fold0_val": development[:1],
                "fold1_train": [development[0], development[2]],
                "fold1_val": development[1:2],
                "fold2_train": development[:2],
                "fold2_val": development[2:],
            }
        )
        self.config = {
            "datasets": {
                "donate": {
                    "counts": {"all": 4, "development": 3, "test": 1},
                    "group_field": "group_id",
                }
            },
            "training": {"folds": [0, 1, 2]},
        }

    def test_exactly_once_validation_coverage_passes(self) -> None:
        result = _validate_dataset_splits("donate", self.store, self.config)
        self.assertTrue(result["exactly_once_development_validation_coverage"])
        self.assertEqual(result["group_leakage"], 0)

    def test_group_leakage_fails(self) -> None:
        self.store.values["test"] = [row("d", "ga", "y")]
        self.store.values["all"] = self.store.values["development"] + self.store.values["test"]
        with self.assertRaisesRegex(RuntimeError, "group leakage"):
            _validate_dataset_splits("donate", self.store, self.config)


class PlannedRunsTest(unittest.TestCase):
    def test_frozen_matrix_contains_paired_bc_then_a(self) -> None:
        config = {
            "model": {"registry": {"A": {}, "B": {}, "C": {}}},
            "training": {
                "seeds": [42, 43, 44],
                "folds": [0, 1, 2],
                "execution_order": ["B", "C", "A"],
            },
        }
        runs = _planned_development_runs(config)
        self.assertEqual(len(runs), 27)
        self.assertEqual(
            [run["run_id"] for run in runs[:6]],
            [
                "B-s42-f0",
                "C-s42-f0",
                "A-s42-f0",
                "B-s42-f1",
                "C-s42-f1",
                "A-s42-f1",
            ],
        )


if __name__ == "__main__":
    unittest.main()
