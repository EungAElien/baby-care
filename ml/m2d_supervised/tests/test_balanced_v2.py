"""V2 sampler, data boundary, matched budgets and checkpoint regression checks."""

import copy
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import test_training as fixtures
import torch

from m2d_supervised.balanced_data import (
    VARIANTS,
    DevelopmentStore,
    balanced_plan,
    development_plan,
    small_balanced_subset,
    validate_development,
    variant_config,
)
from m2d_supervised.balanced_report import paired_bootstrap
from m2d_supervised.balanced_runtime import execute, load_protocol, pin_json, verify_gate
from m2d_supervised.checkpoint import state_digest
from m2d_supervised.data import RuntimePaths
from m2d_supervised.engine import make_optimizer, partial_names, train_update
from m2d_supervised.training import run_fold
from m2d_supervised.util import atomic_json, read_json

PACKAGE = Path(__file__).parents[1]


def sample(label, group, index):
    return {"sample_id": f"{label}-{group}-{index}", "label": label, "group_id": group}


class BalancedDataTest(unittest.TestCase):
    def setUp(self):
        self.rows = [sample("a", "a1", i) for i in range(41)] + [
            sample("a", "a2", 0),
            sample("b", "b1", 0),
            sample("b", "b2", 0),
        ]
        self.base = read_json(PACKAGE / "config/experiment_v1.json")
        self.protocol = read_json(PACKAGE / "config/balanced_v2.json")

    def test_label_group_and_clip_cycles_and_determinism(self):
        plan = balanced_plan(self.rows, ["a", "b"], 42, "warmup", 0)
        self.assertEqual(plan, balanced_plan(self.rows, ["a", "b"], 42, "warmup", 0))
        self.assertNotEqual(plan, balanced_plan(self.rows, ["a", "b"], 43, "warmup", 0))
        self.assertEqual(len(plan), len(self.rows))
        self.assertEqual([i for _, i in plan], list(range(len(self.rows))))
        counts = Counter(row["label"] for row, _ in plan)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        groups = Counter(row["group_id"] for row, _ in plan)
        self.assertLessEqual(abs(groups["a1"] - groups["a2"]), 1)
        chosen = [row["sample_id"] for row, _ in plan if row["group_id"] == "a1"]
        self.assertEqual(len(chosen), len(set(chosen)))

    def test_missing_label_and_group_fail(self):
        with self.assertRaises(ValueError):
            balanced_plan(self.rows, ["a", "b", "c"], 42, "warmup", 0)
        with self.assertRaises(ValueError):
            balanced_plan([{**self.rows[0], "group_id": ""}], ["a"], 42, "warmup", 0)

    def test_natural_samples_once_and_balanced_arms_share_plans(self):
        manifests = {"donate": {"train": self.rows}}
        configs = {v: variant_config(self.base, self.protocol, v) for v in VARIANTS}
        for config in configs.values():
            config["datasets"]["donate"]["labels"] = ["a", "b"]
        plans = {v: development_plan(manifests, c, 42, "finetune", 3) for v, c in configs.items()}
        natural = plans["natural_head"]["donate"]
        self.assertEqual({r["sample_id"] for r, _ in natural}, {r["sample_id"] for r in self.rows})
        self.assertEqual(len(natural), len(self.rows))
        self.assertEqual(plans["balanced_head"], plans["balanced_finetune"])
        self.assertEqual(
            {c["training"]["warmup"]["effective_batch"] for c in configs.values()}, {16}
        )

    def test_sanity_uses_distinct_groups(self):
        subset = small_balanced_subset(self.rows, ["a", "b"], 42, 2)
        self.assertEqual(len(subset), 4)
        self.assertEqual(len({r["group_id"] for r in subset}), 4)
        with self.assertRaises(ValueError):
            small_balanced_subset(self.rows, ["a", "b"], 42, 3)

    def test_test_enes_all_and_unverified_reads_are_forbidden(self):
        store = DevelopmentStore(
            self.base, RuntimePaths(Path("."), Path("out"), Path("m2d"), Path("."))
        )
        with patch("m2d_supervised.data.ManifestStore.load") as loader:
            for dataset, name, verify in (
                ("donate", "test", True),
                ("donate", "all", True),
                ("enes", "development", True),
                ("donate", "development", False),
            ):
                with self.assertRaises(ValueError):
                    store.load(dataset, name, verify)
            loader.assert_not_called()

    def test_folds_partition_development_and_reject_group_leakage(self):
        rows = [sample(label, f"g{fold}-{label}", 0) for fold in range(3) for label in ("a", "b")]
        config = copy.deepcopy(self.base)
        config["datasets"]["donate"].update(labels=["a", "b"], counts={"development": 6})
        store = DevelopmentStore(config, None)
        splits = {
            fold: {
                "donate": {
                    "val": rows[fold * 2 : fold * 2 + 2],
                    "train": rows[: fold * 2] + rows[fold * 2 + 2 :],
                }
            }
            for fold in range(3)
        }
        with (
            patch.object(store, "load", return_value=rows),
            patch.object(store, "fold", side_effect=lambda f: splits[f]),
        ):
            self.assertEqual(validate_development(store)["count"], 6)
            rows[2]["group_id"] = rows[0]["group_id"]
            with self.assertRaisesRegex(ValueError, "group leakage"):
                validate_development(store)

    def test_paired_bootstrap_identity_and_alignment(self):
        rows = [
            {"sample_id": str(i), "group_id": str(i), "true_index": i % 2, "predicted_index": i % 2}
            for i in range(10)
        ]
        seeds = {42: rows, 43: rows, 44: rows}
        result = paired_bootstrap(seeds, seeds, ["a", "b"], 200, 42)
        self.assertEqual(result["percentile_95_interval"], [0, 0])
        other = copy.deepcopy(seeds)
        other[42] = other[42][1:]
        with self.assertRaises(ValueError):
            paired_bootstrap(seeds, other, ["a", "b"], 10, 42)

    def test_protocol_and_pins_and_failed_gate_are_not_silently_replaced(self):
        load_protocol(PACKAGE / "config/experiment_v1.json", PACKAGE / "config/balanced_v2.json")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pin.json"
            pin_json(path, {"a": 1})
            with self.assertRaises(RuntimeError):
                pin_json(path, {"a": 2})
            atomic_json(path, {"status": "failed", "identity": {"a": 1}})
            with self.assertRaises(RuntimeError):
                verify_gate(path, {"a": 1})


class BalancedTrainingTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TrainingTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        protocol = read_json(PACKAGE / "config/balanced_v2.json")
        self.config = variant_config(self.fixture.config, protocol, "balanced_head")
        self.config["training"]["warmup"].update(epochs=1, effective_batch=2)
        self.config["training"]["finetune"].update(
            maximum_epochs=1, effective_batch_per_dataset=2, micro_batch_maximum=1
        )
        self.config["training"].update(minimum_free_gib=0, checkpoint_every_updates=1)

    def test_head_only_stays_frozen_after_warmup(self):
        model = self.fixture.model(("donate",))
        before = state_digest(model.encoder.state_dict())
        optimizer = make_optimizer(model, self.config, "finetune")
        self.assertTrue(all(key.startswith("heads.") for key in partial_names(model, self.config)))
        self.assertEqual(len(optimizer.param_groups), 1)
        train_update(
            model,
            optimizer,
            self.fixture.paths,
            self.config,
            {"donate": self.fixture.selected["donate"]},
            {"donate": torch.ones(2)},
            seed=42,
            phase="finetune",
            epoch=1,
            device="cpu",
        )
        self.assertEqual(state_digest(model.encoder.state_dict()), before)
        self.assertFalse(model.encoder.training)

    def test_identity_rejection_does_not_overwrite_existing_journal(self):
        self.fixture.paths.output_root.mkdir()
        path = self.fixture.paths.output_root / "state.json"
        original = {"identity": {"other": True}, "status": "paused"}
        atomic_json(path, original)
        with self.assertRaisesRegex(RuntimeError, "dependencies changed"):
            execute(
                PACKAGE / "config/experiment_v1.json",
                PACKAGE / "config/balanced_v2.json",
                self.fixture.paths,
            )
        self.assertEqual(read_json(path), original)

    def test_balanced_fold_resume_and_completed_reentry(self):
        initial = self.fixture.model(("donate",))
        manifests = {
            "donate": {"train": self.fixture.rows["donate"], "val": self.fixture.rows["donate"]}
        }
        observed_weights = []

        def record_update(*args, **kwargs):
            observed_weights.append(args[5]["donate"].tolist())
            return train_update(*args, **kwargs)

        with (
            patch("m2d_supervised.balanced_data.DevelopmentStore.fold", return_value=manifests),
            patch(
                "m2d_supervised.training.create_model",
                side_effect=lambda *args: copy.deepcopy(initial),
            ),
            patch("m2d_supervised.training.train_update", side_effect=record_update),
        ):
            full = run_fold(
                self.config, self.fixture.config_path, self.fixture.paths, "B", 0, 42, device="cpu"
            )
            paths = RuntimePaths(
                self.fixture.root,
                self.fixture.root / "resumed",
                self.fixture.root / "m2d",
                self.fixture.root,
            )
            paused = run_fold(
                self.config,
                self.fixture.config_path,
                paths,
                "B",
                0,
                42,
                device="cpu",
                max_additional_updates=1,
            )
            self.assertEqual(paused["status"], "paused")
            resumed = run_fold(
                self.config, self.fixture.config_path, paths, "B", 0, 42, device="cpu"
            )
            count = len(observed_weights)
            self.assertEqual(
                resumed,
                run_fold(self.config, self.fixture.config_path, paths, "B", 0, 42, device="cpu"),
            )
            self.assertEqual(count, len(observed_weights))
        self.assertTrue(all(weights == [1, 1] for weights in observed_weights))
        first = torch.load(
            self.fixture.paths.output_root / "development" / full["run_id"] / "best.pt",
            weights_only=True,
        )
        second = torch.load(
            paths.output_root / "development" / resumed["run_id"] / "best.pt", weights_only=True
        )
        self.assertEqual(
            state_digest(first["partial_model"]), state_digest(second["partial_model"])
        )
        history = read_json(paths.output_root / "development" / resumed["run_id"] / "history.json")
        self.assertEqual(history["exposures"]["donate"]["labels"], {"a": 2, "b": 2})
        self.assertFalse(resumed["test_used"])
        self.assertIn("donate_train_diagnostic.json", resumed["artifact_sha256"])


if __name__ == "__main__":
    unittest.main()
