"""Frozen manifest loading, deterministic sampling, cropping and class weights."""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .util import ensure_within, read_csv, seed_for, sha256_file


def resolve_portable(root: Path, relative: str | Path) -> Path:
    """Resolve NFC/NFD-equivalent path components without embedding a machine path."""
    direct = root / relative
    if direct.exists():
        return direct
    current = root
    for part in Path(relative).parts:
        target = unicodedata.normalize("NFC", part)
        matches = [
            child
            for child in current.iterdir()
            if unicodedata.normalize("NFC", child.name) == target
        ]
        if len(matches) != 1:
            raise FileNotFoundError(
                f"Could not resolve configured path component {part!r} below {current}"
            )
        current = matches[0]
    return current


@dataclass(frozen=True)
class RuntimePaths:
    research_root: Path
    output_root: Path
    m2d_source_root: Path
    repository_root: Path

    def dataset_root(self, config: dict[str, Any], dataset: str) -> Path:
        return resolve_portable(self.research_root, config["datasets"][dataset]["root"])

    def checkpoint(self, config: dict[str, Any], experiment: str) -> Path:
        relative = config["model"]["registry"][experiment]["checkpoint"]
        return resolve_portable(self.research_root, relative)


class ManifestStore:
    def __init__(self, config: dict[str, Any], paths: RuntimePaths):
        self.config = config
        self.paths = paths
        self._cache: dict[tuple[str, str], list[dict[str, str]]] = {}

    def manifest_path(self, dataset: str, name: str) -> Path:
        return self.paths.dataset_root(self.config, dataset) / "manifests" / f"{name}.csv"

    def load(self, dataset: str, name: str, verify_hash: bool = True) -> list[dict[str, str]]:
        key = (dataset, name)
        if key not in self._cache:
            path = self.manifest_path(dataset, name)
            expected = self.config["datasets"][dataset]["manifest_sha256"].get(name)
            if verify_hash and expected is not None and sha256_file(path) != expected:
                raise RuntimeError(f"Frozen manifest changed: {dataset}/{name}")
            rows = read_csv(path)
            labels = self.config["datasets"][dataset]["labels"]
            if not rows:
                raise RuntimeError(f"Empty manifest: {dataset}/{name}")
            for row in rows:
                index = int(row["label_index"])
                if not 0 <= index < len(labels) or labels[index] != row["label"]:
                    raise RuntimeError(f"Label/index mismatch in {dataset}/{name}")
            self._cache[key] = rows
        return self._cache[key]

    def fold(self, fold: int) -> dict[str, dict[str, list[dict[str, str]]]]:
        if fold not in self.config["training"]["folds"]:
            raise ValueError(f"Unsupported fold: {fold}")
        return {
            dataset: {
                "train": self.load(dataset, f"fold{fold}_train"),
                "val": self.load(dataset, f"fold{fold}_val"),
            }
            for dataset in ("donate", "enes")
        }

    def development(self) -> dict[str, list[dict[str, str]]]:
        return {dataset: self.load(dataset, "development") for dataset in ("donate", "enes")}

    def test(self) -> dict[str, list[dict[str, str]]]:
        return {dataset: self.load(dataset, "test") for dataset in ("donate", "enes")}


def used_frames(row: dict[str, str], config: dict[str, Any]) -> int:
    prep = config["preprocessing"]
    frames = int(row["valid_frames"])
    multiple = int(prep["frame_multiple"])
    used = min(int(prep["maximum_frames"]), frames // multiple * multiple)
    if used < int(prep["minimum_frames"]):
        raise ValueError(f"Input is shorter than the fixed minimum: {row['sample_id']}")
    return used


def load_feature(
    paths: RuntimePaths,
    config: dict[str, Any],
    dataset: str,
    row: dict[str, str],
    *,
    seed: int,
    phase: str,
    epoch: int,
    occurrence: int,
) -> torch.Tensor:
    root = paths.dataset_root(config, dataset)
    path = ensure_within(root, root / row["file_name"])
    array = np.load(path, allow_pickle=False)
    frames = int(row["valid_frames"])
    if array.dtype != np.float32 or array.shape != (1, 80, frames):
        raise ValueError(f"Feature shape or dtype changed: {dataset}/{row['sample_id']}")
    if not np.isfinite(array).all():
        raise ValueError(f"Non-finite feature: {dataset}/{row['sample_id']}")
    used = used_frames(row, config)
    if phase in {"validation", "test", "inference"}:
        start = (frames - used) // 2
    else:
        rng = np.random.default_rng(
            seed_for(seed, "crop", dataset, row["sample_id"], phase, epoch, occurrence)
        )
        start = int(rng.integers(frames - used + 1))
    crop = np.ascontiguousarray(array[..., start : start + used])
    prep = config["preprocessing"]
    return (torch.from_numpy(crop) - float(prep["normalization_mean"])) / float(
        prep["normalization_std"]
    )


PlannedRow = tuple[dict[str, str], int]


def epoch_plan(
    manifests: dict[str, dict[str, list[dict[str, str]]]],
    seed: int,
    phase: str,
    epoch: int,
) -> dict[str, list[PlannedRow]]:
    donate = manifests["donate"]["train"]
    order = np.random.default_rng(seed_for(seed, "donate_order", phase, epoch)).permutation(
        len(donate)
    )
    donate_plan = [(donate[int(index)], occurrence) for occurrence, index in enumerate(order)]

    nested: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in manifests["enes"]["train"]:
        nested[row["baby_id"]][row["bout_id"]].append(row)
    babies = sorted(nested)
    rng = np.random.default_rng(seed_for(seed, "enes_hierarchy", phase, epoch))
    enes_plan: list[PlannedRow] = []
    for occurrence in range(len(donate_plan)):
        baby = babies[int(rng.integers(len(babies)))]
        bouts = sorted(nested[baby])
        bout = bouts[int(rng.integers(len(bouts)))]
        cries = nested[baby][bout]
        row = cries[int(rng.integers(len(cries)))]
        enes_plan.append((row, occurrence))
    return {"donate": donate_plan, "enes": enes_plan}


def microbatches(
    paths: RuntimePaths,
    config: dict[str, Any],
    dataset: str,
    selected: list[PlannedRow],
    *,
    seed: int,
    phase: str,
    epoch: int,
    micro_batch: int,
) -> Iterator[tuple[list[PlannedRow], torch.Tensor, torch.Tensor]]:
    buckets: dict[int, list[PlannedRow]] = defaultdict(list)
    for item in selected:
        buckets[used_frames(item[0], config)].append(item)
    for length in sorted(buckets):
        bucket = buckets[length]
        for start in range(0, len(bucket), micro_batch):
            batch = bucket[start : start + micro_batch]
            features = torch.stack(
                [
                    load_feature(
                        paths,
                        config,
                        dataset,
                        row,
                        seed=seed,
                        phase=phase,
                        epoch=epoch,
                        occurrence=occurrence,
                    )
                    for row, occurrence in batch
                ]
            )
            labels = torch.tensor([int(row["label_index"]) for row, _ in batch], dtype=torch.long)
            yield batch, features, labels


def donate_class_weights(rows: list[dict[str, str]], labels: list[str]) -> torch.Tensor:
    counts = np.array([sum(row["label"] == label for row in rows) for label in labels], dtype=float)
    if np.any(counts <= 0):
        raise ValueError("A Donate training class is absent.")
    probabilities = counts / counts.sum()
    weights = 1.0 / np.sqrt(probabilities)
    weights /= float(np.dot(probabilities, weights))
    return torch.tensor(weights, dtype=torch.float32)


def enes_class_weights(rows: list[dict[str, str]], labels: list[str]) -> torch.Tensor:
    nested: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        nested[row["baby_id"]][row["bout_id"]].append(row)
    probabilities = np.zeros(len(labels), dtype=float)
    for bouts in nested.values():
        for cries in bouts.values():
            for row in cries:
                probabilities[int(row["label_index"])] += (
                    1.0 / len(nested) / len(bouts) / len(cries)
                )
    if np.any(probabilities <= 0) or not math.isclose(float(probabilities.sum()), 1.0):
        raise ValueError("Invalid Enes hierarchy-induced class probabilities.")
    weights = 1.0 / np.sqrt(probabilities)
    weights /= float(np.dot(probabilities, weights))
    return torch.tensor(weights, dtype=torch.float32)


def feature_integrity(
    paths: RuntimePaths,
    config: dict[str, Any],
    dataset: str,
    rows: list[dict[str, str]],
) -> dict[str, int]:
    root = paths.dataset_root(config, dataset)
    verified = 0
    total_bytes = 0
    for row in rows:
        path = ensure_within(root, root / row["file_name"])
        if sha256_file(path) != row["spec_sha256"]:
            raise RuntimeError(f"Feature hash changed: {dataset}/{row['sample_id']}")
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        if array.dtype != np.float32 or array.shape != (1, 80, int(row["valid_frames"])):
            raise RuntimeError(f"Feature metadata changed: {dataset}/{row['sample_id']}")
        if not bool(np.isfinite(array).all()):
            raise RuntimeError(f"Feature contains non-finite values: {dataset}/{row['sample_id']}")
        verified += 1
        total_bytes += path.stat().st_size
    return {"verified_files": verified, "verified_bytes": total_bytes}
