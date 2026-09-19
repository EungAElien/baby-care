"""Behavioral checks on tiny models; no source audio or sealed test split is used."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch
from torch import nn

from m2d_supervised.checkpoint import (
    atomic_torch,
    cpu_tree,
    frozen_digest,
    load_partial,
    restore_training,
    resume_payload,
    state_digest,
)
from m2d_supervised.data import RuntimePaths
from m2d_supervised.engine import (
    make_optimizer,
    metrics_from_predictions,
    train_update,
    weighted_losses,
)
from m2d_supervised.model import M2DClassifier
from m2d_supervised.runtime import RuntimeGuard, training_locks
from m2d_supervised.training import run_fold
from m2d_supervised.util import read_json


class TinyEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.pos_embed = nn.Parameter(torch.zeros(1, 11, 4))
        self.blocks = nn.ModuleList([nn.Linear(4, 4) for _ in range(4)])
        self.norm = nn.LayerNorm(4)
        self.dropout = nn.Dropout(0.1)

    def forward_encoder(self, inputs, **kwargs):
        batch, _, _, frames = inputs.shape
        tokens = inputs.reshape(batch, 5, 16, frames // 16, 16).mean((2, 4))
        tokens = tokens.flatten(1).unsqueeze(-1).expand(-1, -1, 4)
        tokens = torch.cat([torch.zeros(batch, 1, 4, device=inputs.device), tokens], dim=1)
        for block in self.blocks:
            tokens = torch.tanh(block(tokens))
        return self.norm(self.dropout(tokens)), None, None


class TrainingTest(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(91)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = RuntimePaths(self.root, self.root / "output", self.root / "m2d", self.root)
        self.config_path = Path(__file__).parents[1] / "config/experiment_v1.json"
        self.config = read_json(self.config_path)
        self.config["training"]["finetune"].update(
            trainable_encoder_blocks=[2, 3],
            maximum_epochs=1,
            patience=1,
            micro_batch_maximum=1,
            effective_batch_per_dataset=2,
        )
        self.config["training"]["warmup"].update(epochs=1, effective_batch=2)
        self.config["training"]["minimum_free_gib"] = 0
        self.config["training"]["checkpoint_every_updates"] = 1
        self.rows = {}
        for dataset in ("donate", "enes"):
            self.config["datasets"][dataset].update(
                root=dataset, labels=["a", "b"], manifest_sha256={}
            )
            (self.root / dataset).mkdir()
            self.rows[dataset] = []
            for index, frames in enumerate((35, 50)):
                np.save(
                    self.root / dataset / f"{index}.npy",
                    np.random.default_rng(index).normal(size=(1, 80, frames)).astype("float32"),
                )
                self.rows[dataset].append(
                    {
                        "sample_id": f"{dataset}{index}",
                        "file_name": f"{index}.npy",
                        "label": ["a", "b"][index],
                        "label_index": str(index),
                        "valid_frames": str(frames),
                        "group_id": str(index),
                        "baby_id": str(index),
                        "bout_id": str(index),
                    }
                )
        self.selected = {
            d: list(zip(rows, range(len(rows)), strict=True)) for d, rows in self.rows.items()
        }
        self.weights = {d: torch.tensor([0.6, 1.4]) for d in self.rows}

    def model(self, heads=("donate", "enes")):
        return M2DClassifier(TinyEncoder(), {d: ["a", "b"] for d in self.rows}, heads, head_seed=42)

    def update(self, model, optimizer, phase="finetune", interrupt=lambda: None):
        return train_update(
            model,
            optimizer,
            self.paths,
            self.config,
            self.selected,
            self.weights,
            seed=42,
            phase=phase,
            epoch=0,
            device="cpu",
            interrupt=interrupt,
        )

    def test_weight_is_not_cancelled_in_single_example_microbatch(self):
        logits = torch.tensor([[0.2, -0.5]])
        plain = torch.nn.functional.cross_entropy(logits, torch.tensor([1]))
        result = weighted_losses(logits, torch.tensor([1]), torch.tensor([1.0, 3.0]))
        self.assertAlmostEqual(float(result), float(plain) * 3, places=6)

    def test_zero_auxiliary_loss_matches_b_update(self):
        joint = self.model()
        single = copy.deepcopy(joint)
        single.trained_heads = ("donate",)
        del single.heads["enes"]
        self.config["training"]["joint_loss"]["enes"] = 0
        opt_single = make_optimizer(single, self.config, "finetune")
        opt_joint = make_optimizer(joint, self.config, "finetune")
        torch.manual_seed(17)
        self.update(single, opt_single)
        torch.manual_seed(17)
        self.update(joint, opt_joint)
        self.assertTrue(
            all(
                torch.equal(value, joint.state_dict()[key])
                for key, value in single.state_dict().items()
            )
        )

    def test_phase_freezing_and_both_losses_reach_encoder(self):
        model = self.model()
        encoder_before = state_digest(model.encoder.state_dict())
        head_before = model.heads["donate"].weight.detach().clone()
        optimizer = make_optimizer(model, self.config, "warmup")
        self.update(model, optimizer, "warmup")
        self.assertEqual(encoder_before, state_digest(model.encoder.state_dict()))
        self.assertFalse(torch.equal(head_before, model.heads["donate"].weight))
        baseline = frozen_digest(model, self.config)
        for dataset in ("donate", "enes"):
            candidate = copy.deepcopy(model)
            candidate.trained_heads = (dataset,)
            optimizer = make_optimizer(candidate, self.config, "finetune")
            before = candidate.encoder.blocks[-1].weight.detach().clone()
            self.update(candidate, optimizer)
            self.assertFalse(torch.equal(before, candidate.encoder.blocks[-1].weight))
            self.assertEqual(baseline, frozen_digest(candidate, self.config))

    def test_partial_roundtrip_and_rng_reproduce_next_cpu_update(self):
        model = self.model()
        initial = copy.deepcopy(model)
        baseline = frozen_digest(model, self.config)
        optimizer = make_optimizer(model, self.config, "finetune")
        self.update(model, optimizer)
        identity = {"initialization": "B", "manifest": "same"}
        payload = cpu_tree(
            resume_payload(
                model, self.config, optimizer, {"step": 1}, identity, "finetune", "cpu", baseline
            )
        )
        full_hash = state_digest(model.state_dict())
        self.update(model, optimizer)
        expected = state_digest(model.state_dict())
        restored_optimizer, state, phase = restore_training(
            initial, self.config, payload, identity, baseline, "cpu"
        )
        self.assertEqual(full_hash, state_digest(initial.state_dict()))
        self.assertEqual(state, {"step": 1})
        self.update(initial, restored_optimizer, phase)
        self.assertEqual(expected, state_digest(initial.state_dict()))
        with self.assertRaisesRegex(RuntimeError, "identity differs"):
            load_partial(initial, self.config, payload, {"initialization": "A"}, baseline)
        with self.assertRaisesRegex(RuntimeError, "identity differs"):
            load_partial(
                initial, self.config, payload, {**identity, "manifest": "changed"}, baseline
            )

    def test_nonfinite_loss_and_interrupt_do_not_update_weights(self):
        model = self.model()
        optimizer = make_optimizer(model, self.config, "finetune")
        before = state_digest(model.state_dict())
        self.weights["donate"][0] = float("nan")
        with self.assertRaises(FloatingPointError):
            self.update(model, optimizer)
        self.assertEqual(before, state_digest(model.state_dict()))
        self.weights["donate"][0] = 0.6
        with self.assertRaises(InterruptedError):
            self.update(
                model, optimizer, interrupt=lambda: (_ for _ in ()).throw(InterruptedError())
            )
        self.assertEqual(before, state_digest(model.state_dict()))

    def test_atomic_disk_failure_preserves_prior_checkpoint(self):
        path = self.root / "state.pt"
        atomic_torch(path, {"step": torch.tensor(1)}, 0)
        with (
            patch("m2d_supervised.checkpoint.require_free_space", side_effect=RuntimeError("full")),
            self.assertRaises(RuntimeError),
        ):
            atomic_torch(path, {"step": torch.tensor(2)}, 8)
        self.assertEqual(torch.load(path, weights_only=True)["step"].item(), 1)

    def test_power_and_storage_guard_and_exclusive_locks(self):
        self.paths.output_root.mkdir()
        guard = RuntimeGuard(self.paths, 0)
        with (
            patch("m2d_supervised.runtime.ac_power", return_value=False),
            self.assertRaisesRegex(InterruptedError, "power"),
        ):
            guard.check()
        guard.next_check = 0
        with (
            patch("m2d_supervised.runtime.ac_power", return_value=True),
            patch("m2d_supervised.runtime.require_free_space", side_effect=RuntimeError("full")),
            self.assertRaisesRegex(InterruptedError, "Storage"),
        ):
            guard.check()
        with (
            training_locks(self.paths),
            self.assertRaisesRegex(RuntimeError, "Another training"),
            training_locks(self.paths),
        ):
            self.fail("Lock must be exclusive")

    def test_uneven_microbatches_match_full_weighted_loss(self):
        first = self.model(("donate",))
        first.encoder.dropout.p = 0
        second = copy.deepcopy(first)
        opt_first = make_optimizer(first, self.config, "finetune")
        opt_second = make_optimizer(second, self.config, "finetune")
        inputs = torch.randn(3, 1, 80, 32)
        targets = torch.tensor([0, 1, 1])
        selected = {"donate": self.selected["donate"] + self.selected["donate"][:1]}
        batches = [([], inputs[:2], targets[:2]), ([], inputs[2:], targets[2:])]
        with patch("m2d_supervised.engine.microbatches", return_value=iter(batches)):
            observed = train_update(
                first,
                opt_first,
                self.paths,
                self.config,
                selected,
                self.weights,
                seed=42,
                phase="finetune",
                epoch=0,
                device="cpu",
            )
        second.train()
        expected = weighted_losses(second(inputs, "donate"), targets, self.weights["donate"]).mean()
        expected.backward()
        torch.nn.utils.clip_grad_norm_([p for p in second.parameters() if p.requires_grad], 1.0)
        opt_second.step()
        self.assertAlmostEqual(
            observed["loss_by_dataset"]["donate"], float(expected.detach()), places=6
        )
        for key, value in first.state_dict().items():
            self.assertTrue(torch.allclose(value, second.state_dict()[key], atol=1e-7), key)

    def test_pause_resume_across_phase_boundary_equals_continuous(self):
        manifests = {d: {"train": rows, "val": rows} for d, rows in self.rows.items()}
        initial = self.model()
        with (
            patch("m2d_supervised.training.ManifestStore.fold", return_value=manifests),
            patch(
                "m2d_supervised.training.create_model",
                side_effect=lambda *a: copy.deepcopy(initial),
            ),
        ):
            full = run_fold(self.config, self.config_path, self.paths, "C", 0, 42, device="cpu")
            other = RuntimePaths(self.root, self.root / "resumed", self.root / "m2d", self.root)
            paused = run_fold(
                self.config,
                self.config_path,
                other,
                "C",
                0,
                42,
                device="cpu",
                max_additional_updates=1,
            )
            self.assertEqual(paused["status"], "paused")
            resumed = run_fold(self.config, self.config_path, other, "C", 0, 42, device="cpu")
            self.assertEqual(full["best_donate_macro_f1"], resumed["best_donate_macro_f1"])
            self.assertEqual(full["updates_executed"], 2)
            self.assertEqual(resumed["updates_executed"], 2)
            first = torch.load(
                self.paths.output_root / "development" / full["run_id"] / "best.pt",
                weights_only=True,
            )
            second = torch.load(
                other.output_root / "development" / resumed["run_id"] / "best.pt", weights_only=True
            )
            self.assertEqual(
                state_digest(first["partial_model"]), state_digest(second["partial_model"])
            )
            self.assertFalse(full["test_used"])
            self.assertFalse(
                (other.output_root / "development" / resumed["run_id"] / "last.pt").exists()
            )

    def test_macro_f1_counts_unpredicted_classes(self):
        result = metrics_from_predictions(
            [{"true_index": 0, "predicted_index": 0}, {"true_index": 1, "predicted_index": 0}],
            ["a", "b"],
        )
        self.assertAlmostEqual(result["macro_f1"], 1 / 3)


if __name__ == "__main__":
    unittest.main()
