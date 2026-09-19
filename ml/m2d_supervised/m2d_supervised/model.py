"""Pinned M2D encoder loading and dataset-separated classification heads."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import torch
from torch import nn

from .util import sha256_file


def verify_m2d_source(source_root: Path, expected_commit: str) -> dict[str, Any]:
    marker_path = source_root / ".babycry-stage2-source.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("commit") != expected_commit:
        raise RuntimeError("Unexpected M2D source revision.")
    observed: dict[str, str] = {}
    for relative, expected in marker.get("generated_sha256", {}).items():
        path = source_root / relative
        digest = sha256_file(path)
        observed[relative] = digest
        if digest != expected:
            raise RuntimeError(f"Pinned M2D source changed: {relative}")
    return {
        "marker_sha256": sha256_file(marker_path),
        "commit": marker["commit"],
        "verified_generated_files": observed,
    }


def build_encoder(source_root: Path, norm_stats: tuple[float, float]) -> nn.Module:
    source = str(source_root.resolve())
    if source not in sys.path:
        sys.path.insert(0, source)
    from m2d import models_mae

    return models_mae.m2d_vit_base_encoder_only(
        img_size=(80, 608),
        patch_size=(16, 16),
        num_classes=0,
        qkv_bias=True,
        norm_stats=norm_stats,
    )


def checkpoint_state(checkpoint: Path) -> dict[str, torch.Tensor]:
    with torch.serialization.safe_globals([argparse.Namespace]):
        stored = torch.load(checkpoint, map_location="cpu", weights_only=True)
    state = stored.get("model", stored)
    if not isinstance(state, dict) or not state:
        raise RuntimeError("Checkpoint has no model state.")
    if any(not isinstance(value, torch.Tensor) for value in state.values()):
        raise RuntimeError("Checkpoint model state contains a non-tensor value.")
    return state


def load_encoder(
    source_root: Path,
    checkpoint: Path,
    *,
    expected_sha256: str,
    expected_norm_stats: tuple[float, float],
    expected_norm_stats_origin: str,
) -> tuple[nn.Module, str]:
    observed = sha256_file(checkpoint)
    if observed != expected_sha256:
        raise RuntimeError(
            f"Initial checkpoint hash mismatch: expected {expected_sha256}, observed {observed}"
        )
    encoder = build_encoder(source_root, expected_norm_stats)
    source_state = checkpoint_state(checkpoint)
    wanted = encoder.state_dict()
    norm_stats_origin = "checkpoint"
    if "norm_stats" not in source_state:
        norm_stats_origin = "m2d_2022_backward_compatibility_default"
        source_state = {
            **source_state,
            "norm_stats": torch.tensor(
                expected_norm_stats,
                dtype=wanted["norm_stats"].dtype,
            ),
        }
    if norm_stats_origin != expected_norm_stats_origin:
        raise RuntimeError(
            "Normalization-stat origin changed: "
            f"expected {expected_norm_stats_origin}, observed {norm_stats_origin}"
        )
    expected_norm_tensor = torch.tensor(
        expected_norm_stats,
        dtype=wanted["norm_stats"].dtype,
    )
    observed_norm_tensor = (
        source_state["norm_stats"].detach().cpu().to(dtype=wanted["norm_stats"].dtype)
    )
    if not torch.equal(observed_norm_tensor, expected_norm_tensor):
        raise RuntimeError(
            "Checkpoint normalization statistics changed: "
            f"expected {expected_norm_stats}, observed {observed_norm_tensor.tolist()}"
        )
    missing = sorted(set(wanted) - set(source_state))
    extras = sorted(set(source_state) - set(wanted))

    def allowed_extra(name: str) -> bool:
        return name == "mask_token" or name.startswith(("decoder_", "target_"))

    forbidden_extras = [name for name in extras if not allowed_extra(name)]
    shape_mismatch = {
        name: [list(source_state[name].shape), list(wanted[name].shape)]
        for name in set(source_state) & set(wanted)
        if source_state[name].shape != wanted[name].shape
    }
    if missing or forbidden_extras or shape_mismatch:
        raise RuntimeError(
            "Incompatible online encoder checkpoint: "
            f"missing={missing}; forbidden_extra={forbidden_extras}; "
            f"shape_mismatch={shape_mismatch}"
        )
    encoder.load_state_dict({name: source_state[name] for name in wanted}, strict=True)
    return encoder, norm_stats_origin


def _initialize_linear(linear: nn.Linear, seed: int) -> None:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    bound = 1.0 / math.sqrt(linear.in_features)
    with torch.no_grad():
        linear.weight.uniform_(-bound, bound, generator=generator)
        if linear.bias is not None:
            linear.bias.uniform_(-bound, bound, generator=generator)


class M2DClassifier(nn.Module):
    def __init__(
        self,
        encoder: nn.Module,
        labels: dict[str, list[str]],
        trained_heads: Iterable[str],
        *,
        head_seed: int,
    ) -> None:
        super().__init__()
        self.encoder = encoder
        self.labels = {name: list(values) for name, values in labels.items()}
        self.trained_heads = tuple(sorted(trained_heads))
        unknown = set(self.trained_heads) - set(self.labels)
        if unknown:
            raise ValueError(f"Unknown trained heads: {sorted(unknown)}")
        self.feature_dim = 5 * int(encoder.pos_embed.shape[-1])
        self.heads = nn.ModuleDict(
            {
                name: nn.Linear(self.feature_dim, len(self.labels[name]))
                for name in self.trained_heads
            }
        )
        for name, head in self.heads.items():
            _initialize_linear(head, head_seed_for(head_seed, name))

    def features(self, inputs: torch.Tensor) -> torch.Tensor:
        if inputs.ndim != 4 or tuple(inputs.shape[1:3]) != (1, 80):
            raise ValueError("Expected [batch, 1, 80, frames] input.")
        if inputs.shape[-1] % 16:
            raise ValueError("Frame length must be a multiple of 16.")
        latent, *_ = self.encoder.forward_encoder(inputs, mask_ratio=0.0, adjust_short=True)
        dimension = latent.shape[-1]
        time_patches = inputs.shape[-1] // 16
        if latent.shape[1] != 1 + 5 * time_patches:
            raise RuntimeError("Unexpected M2D patch-token ordering.")
        patches = latent[:, 1:].reshape(inputs.shape[0], 5, time_patches, dimension)
        return patches.mean(dim=2).flatten(start_dim=1)

    def forward(self, inputs: torch.Tensor, head: str) -> torch.Tensor:
        if head not in self.trained_heads:
            raise RuntimeError(f"Head {head!r} was not trained and cannot be used.")
        return self.heads[head](self.features(inputs))


def head_seed_for(seed: int, head: str) -> int:
    digest = hashlib.sha256(f"{seed}:head:{head}".encode()).digest()
    return int.from_bytes(digest[:8], "little") % (2**63)
