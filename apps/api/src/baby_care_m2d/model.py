"""Inference-only classifier copied from PR #18 at fa271ba; no training entrypoints."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, cast

import torch
from torch import nn

from .registry import ModelIntegrityError


class M2DEncoder(Protocol):
    pos_embed: torch.Tensor

    def forward_encoder(
        self, inputs: torch.Tensor, *, mask_ratio: float, adjust_short: bool
    ) -> tuple[torch.Tensor, ...]: ...


def build_encoder(
    source_root: Path,
    norm_stats: tuple[float, float],
    architecture: dict[str, Any],
) -> nn.Module:
    source = str(source_root)
    source_package = str(source_root / "m2d")
    for name in ("m2d", "m2d.models_mae", "m2d.masking", "pos_embed"):
        loaded = sys.modules.get(name)
        loaded_file = getattr(loaded, "__file__", None)
        if loaded_file is not None and not Path(loaded_file).resolve().is_relative_to(source_root):
            raise ModelIntegrityError("A different M2D source is already loaded")
    if source not in sys.path:
        sys.path.insert(0, source)
    if source_package not in sys.path:
        # The pinned source uses an absolute `pos_embed` import.
        sys.path.insert(0, source_package)
    importlib.invalidate_caches()
    models_mae = importlib.import_module("m2d.models_mae")
    module_file = getattr(models_mae, "__file__", None)
    if module_file is None or not Path(module_file).resolve().is_relative_to(source_root):
        raise ModelIntegrityError("The imported M2D architecture escaped its source root")
    encoder = models_mae.m2d_vit_base_encoder_only(
        img_size=tuple(architecture["input_size"]),
        patch_size=tuple(architecture["patch_size"]),
        num_classes=0,
        qkv_bias=True,
        norm_stats=norm_stats,
    )
    return encoder


def _initialize_linear(linear: nn.Linear, seed: int) -> None:
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    bound = 1.0 / math.sqrt(linear.in_features)
    with torch.no_grad():
        linear.weight.uniform_(-bound, bound, generator=generator)
        if linear.bias is not None:
            linear.bias.uniform_(-bound, bound, generator=generator)


def head_seed_for(seed: int, head: str) -> int:
    digest = hashlib.sha256(f"{seed}:head:{head}".encode()).digest()
    return int.from_bytes(digest[:8], "little") % (2**63)


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
        encoder_api = cast(M2DEncoder, encoder)
        self.feature_dim = 5 * int(encoder_api.pos_embed.shape[-1])
        self.heads = nn.ModuleDict(
            {
                name: nn.Linear(self.feature_dim, len(self.labels[name]))
                for name in self.trained_heads
            }
        )
        for name, head in self.heads.items():
            _initialize_linear(cast(nn.Linear, head), head_seed_for(head_seed, name))

    def features(self, inputs: torch.Tensor) -> torch.Tensor:
        if inputs.ndim != 4 or tuple(inputs.shape[1:3]) != (1, 80):
            raise ValueError("Expected [batch, 1, 80, frames] input")
        if inputs.shape[-1] % 16:
            raise ValueError("Frame length must be a multiple of 16")
        encoder_api = cast(M2DEncoder, self.encoder)
        latent, *_ = encoder_api.forward_encoder(inputs, mask_ratio=0.0, adjust_short=True)
        dimension = latent.shape[-1]
        time_patches = inputs.shape[-1] // 16
        if latent.shape[1] != 1 + 5 * time_patches:
            raise RuntimeError("Unexpected M2D patch-token ordering")
        patches = latent[:, 1:].reshape(inputs.shape[0], 5, time_patches, dimension)
        return patches.mean(dim=2).flatten(start_dim=1)

    def forward(self, inputs: torch.Tensor, head: str) -> torch.Tensor:
        if head not in self.trained_heads:
            raise RuntimeError("An untrained output head was requested")
        return self.heads[head](self.features(inputs))


def state_digest(state: dict[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(state.items()):
        value = tensor.detach().cpu().contiguous()
        identity = json.dumps(
            [name, str(value.dtype), list(value.shape)],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest.update(identity.encode("utf-8"))
        digest.update(value.numpy().tobytes())
    return digest.hexdigest()
