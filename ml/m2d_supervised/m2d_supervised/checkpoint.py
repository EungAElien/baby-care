"""Compact atomic checkpoints; immutable encoder provenance and exact RNG restore."""

from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .engine import make_optimizer, partial_names
from .model import M2DClassifier
from .util import canonical_json, require_free_space


def cpu_tree(value: Any) -> Any:
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {key: cpu_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [cpu_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(cpu_tree(item) for item in value)
    return value


def tensor_bytes(value: Any) -> int:
    if isinstance(value, torch.Tensor):
        return value.numel() * value.element_size()
    if isinstance(value, dict):
        return sum(tensor_bytes(item) for item in value.values())
    if isinstance(value, list | tuple):
        return sum(tensor_bytes(item) for item in value)
    return 0


def atomic_torch(path: Path, payload: dict[str, Any], minimum_free_gib: float) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = cpu_tree(payload)
    require_free_space(path.parent, minimum_free_gib, tensor_bytes(payload) + 16 * 1024**2)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        torch.save(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())
    require_free_space(path.parent, minimum_free_gib)
    os.replace(temporary, path)
    return path.stat().st_size


def state_digest(state: dict[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(state.items()):
        value = tensor.detach().cpu().contiguous()
        digest.update(canonical_json([name, str(value.dtype), list(value.shape)]).encode())
        digest.update(value.numpy().tobytes())
    return digest.hexdigest()


def frozen_digest(model: M2DClassifier, config: dict[str, Any]) -> str:
    selected = partial_names(model, config)
    return state_digest(
        {name: value for name, value in model.state_dict().items() if name not in selected}
    )


def partial_state(model: M2DClassifier, config: dict[str, Any]) -> dict[str, torch.Tensor]:
    names = partial_names(model, config)
    return {name: value for name, value in model.state_dict().items() if name in names}


def capture_rng(device: str) -> dict[str, Any]:
    numpy_state = np.random.get_state()
    return {
        "python": random.getstate(),
        "numpy": (numpy_state[0], numpy_state[1].tolist(), *numpy_state[2:]),
        "torch": torch.get_rng_state(),
        "mps": torch.mps.get_rng_state() if device == "mps" else None,
    }


def restore_rng(state: dict[str, Any], device: str) -> None:
    random.setstate(state["python"])
    numpy_state = state["numpy"]
    np.random.set_state(
        (numpy_state[0], np.asarray(numpy_state[1], dtype=np.uint32), *numpy_state[2:])
    )
    torch.set_rng_state(state["torch"].cpu())
    if device == "mps":
        torch.mps.set_rng_state(state["mps"].cpu())


def load_partial(
    model: M2DClassifier,
    config: dict[str, Any],
    payload: dict[str, Any],
    identity: dict[str, Any],
    baseline_frozen_digest: str,
) -> None:
    if payload["identity"] != identity:
        raise RuntimeError(
            "Checkpoint identity differs: code, data, seed, fold or initialization changed."
        )
    if payload["frozen_sha256"] != baseline_frozen_digest:
        raise RuntimeError("Frozen encoder provenance differs.")
    if frozen_digest(model, config) != baseline_frozen_digest:
        raise RuntimeError("Frozen tensors changed.")
    state = payload["partial_model"]
    if set(state) != partial_names(model, config):
        raise RuntimeError("Partial checkpoint tensor keys changed.")
    wanted = model.state_dict()
    for name, tensor in state.items():
        if tensor.shape != wanted[name].shape or tensor.dtype != wanted[name].dtype:
            raise RuntimeError(f"Partial tensor metadata differs: {name}")
        if not bool(torch.isfinite(tensor).all()):
            raise RuntimeError(f"Non-finite partial tensor: {name}")
    model.load_state_dict({**wanted, **state}, strict=True)


def resume_payload(
    model: M2DClassifier,
    config: dict[str, Any],
    optimizer: torch.optim.Optimizer,
    state: dict[str, Any],
    identity: dict[str, Any],
    phase: str,
    device: str,
    baseline_frozen_digest: str,
) -> dict[str, Any]:
    if frozen_digest(model, config) != baseline_frozen_digest:
        raise RuntimeError("Frozen encoder changed; refusing to save an incomplete model.")
    return {
        "schema_version": 1,
        "identity": identity,
        "frozen_sha256": baseline_frozen_digest,
        "partial_model": partial_state(model, config),
        "optimizer": optimizer.state_dict(),
        "optimizer_phase": phase,
        "state": state,
        "rng": capture_rng(device),
    }


def restore_training(
    model: M2DClassifier,
    config: dict[str, Any],
    payload: dict[str, Any],
    identity: dict[str, Any],
    baseline_frozen_digest: str,
    device: str,
) -> tuple[torch.optim.Optimizer, dict[str, Any], str]:
    load_partial(model, config, payload, identity, baseline_frozen_digest)
    phase = payload["optimizer_phase"]
    optimizer = make_optimizer(model, config, phase)
    optimizer.load_state_dict(payload["optimizer"])
    restore_rng(payload["rng"], device)
    return optimizer, cpu_tree(payload["state"]), phase
