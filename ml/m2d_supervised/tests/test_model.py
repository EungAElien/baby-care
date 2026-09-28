import unittest

import torch
from torch import nn

from m2d_supervised.model import M2DClassifier


class TinyEncoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.pos_embed = nn.Parameter(torch.zeros(1, 1, 4))

    def forward_encoder(
        self, inputs: torch.Tensor, mask_ratio: float, adjust_short: bool
    ) -> tuple[torch.Tensor]:
        del mask_ratio, adjust_short
        time_patches = inputs.shape[-1] // 16
        tokens = torch.arange(
            inputs.shape[0] * (1 + 5 * time_patches) * 4,
            dtype=inputs.dtype,
        ).reshape(inputs.shape[0], 1 + 5 * time_patches, 4)
        return (tokens,)


class ModelPreparationTest(unittest.TestCase):
    def test_pooling_contract_and_head_shape(self) -> None:
        model = M2DClassifier(
            TinyEncoder(),
            {"donate": ["a", "b", "c"]},
            ["donate"],
            head_seed=42,
        )
        inputs = torch.zeros(2, 1, 80, 32)
        self.assertEqual(tuple(model.features(inputs).shape), (2, 20))
        self.assertEqual(tuple(model(inputs, "donate").shape), (2, 3))

    def test_dataset_heads_are_separate_and_deterministic(self) -> None:
        donate_only = M2DClassifier(
            TinyEncoder(),
            {"donate": ["a", "b"]},
            ["donate"],
            head_seed=42,
        )
        joint = M2DClassifier(
            TinyEncoder(),
            {"donate": ["a", "b"], "enes": ["x", "y", "z"]},
            ["donate", "enes"],
            head_seed=42,
        )
        self.assertTrue(
            torch.equal(donate_only.heads["donate"].weight, joint.heads["donate"].weight)
        )
        self.assertTrue(torch.equal(donate_only.heads["donate"].bias, joint.heads["donate"].bias))

    def test_untrained_head_cannot_be_used(self) -> None:
        model = M2DClassifier(
            TinyEncoder(),
            {"donate": ["a", "b"], "enes": ["x", "y", "z"]},
            ["donate"],
            head_seed=42,
        )
        with self.assertRaisesRegex(RuntimeError, "was not trained"):
            model(torch.zeros(1, 1, 80, 32), "enes")


if __name__ == "__main__":
    unittest.main()
