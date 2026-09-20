"""Development-only v2 sampling; no test manifest or Enes loader is used."""

from __future__ import annotations

import copy
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from .data import ManifestStore, PlannedRow
from .util import seed_for

VARIANTS = ("natural_head", "balanced_head", "balanced_finetune")


class DevelopmentStore(ManifestStore):
    def load(self, dataset: str, name: str, verify_hash: bool = True):
        allowed = {"development", *(f"fold{f}_{s}" for f in (0, 1, 2) for s in ("train", "val"))}
        if dataset != "donate" or name not in allowed or not verify_hash:
            raise ValueError("V2 only allows hash-verified Donate development manifests.")
        return super().load(dataset, name, verify_hash=True)

    def fold(self, fold: int):
        if fold not in self.config["training"]["folds"]:
            raise ValueError("Unsupported development fold.")
        return {
            "donate": {part: self.load("donate", f"fold{fold}_{part}") for part in ("train", "val")}
        }


def variant_config(base: dict[str, Any], protocol: dict[str, Any], variant: str) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError("Unknown v2 variant.")
    config = copy.deepcopy(base)
    plan = protocol["training"]
    config["experiment_version"] = protocol["experiment_version"]
    config["balanced_v2"] = {"variant": variant, "protocol": protocol}
    config["training"]["seeds"] = protocol["seeds"]
    config["training"]["folds"] = protocol["folds"]
    config["training"]["warmup"].update(
        epochs=plan["warmup_epochs"],
        effective_batch=plan["effective_batch"],
        head_lr=plan["head_lr"],
    )
    tune = variant == "balanced_finetune"
    config["training"]["finetune"].update(
        maximum_epochs=plan["maximum_total_epochs"] - plan["warmup_epochs"],
        effective_batch_per_dataset=plan["effective_batch"],
        patience=plan["patience"],
        minimum_absolute_improvement=plan["minimum_absolute_improvement"],
        head_lr=plan["head_lr"],
        encoder_lr=plan["encoder_lr"],
        trainable_encoder_blocks=[10, 11] if tune else [],
        train_final_norm=tune,
    )
    for key in ("minimum_free_gib", "checkpoint_every_updates"):
        config["training"][key] = plan[key]
    config["training"]["joint_loss"] = {"donate": 1.0, "enes": 0.0}
    return config


def balanced_plan(
    rows: list[dict[str, str]], labels: list[str], seed: int, phase: str, epoch: int
) -> list[PlannedRow]:
    """Keep N exposures; label counts differ by <=1, groups cycle before repeats.

    Within each (label, group), shuffled clips also cycle before repeating. Group
    identity is recording/source identity, NOT a verified infant identifier.
    """
    nested: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["label"] not in labels or not row.get("group_id"):
            raise ValueError("Unknown label or missing source group.")
        nested[row["label"]][row["group_id"]].append(row)
    if not rows or any(not nested[label] for label in labels):
        raise ValueError("Every training label must be present.")
    rng = np.random.default_rng(seed_for(seed, "v2_balanced", phase, epoch))
    label_order = np.resize(rng.permutation(len(labels)), len(rows))
    rng.shuffle(label_order)
    group_queues: dict[str, list[str]] = {}
    clip_queues: dict[tuple[str, str], list[dict[str, str]]] = {}
    result = []
    for occurrence, index in enumerate(label_order):
        label = labels[int(index)]
        if not group_queues.get(label):
            group_queues[label] = list(rng.permutation(sorted(nested[label])))
        group = str(group_queues[label].pop())
        key = (label, group)
        if not clip_queues.get(key):
            clips = sorted(nested[label][group], key=lambda row: row["sample_id"])
            clip_queues[key] = [clips[int(i)] for i in rng.permutation(len(clips))]
        result.append((clip_queues[key].pop(), occurrence))
    return result


def development_plan(manifests, config, seed, phase, epoch):
    rows = manifests["donate"]["train"]
    if config["balanced_v2"]["variant"] == "natural_head":
        order = np.random.default_rng(seed_for(seed, "donate_order", phase, epoch)).permutation(
            len(rows)
        )
        plan = [(rows[int(index)], i) for i, index in enumerate(order)]
    else:
        plan = balanced_plan(rows, config["datasets"]["donate"]["labels"], seed, phase, epoch)
    return {"donate": plan}


def small_balanced_subset(rows, labels, seed, examples_per_label):
    rng = np.random.default_rng(seed_for(seed, "v2_sanity_subset"))
    selected, used_groups = [], set()
    for label in labels:
        candidates = sorted((r for r in rows if r["label"] == label), key=lambda r: r["sample_id"])
        chosen = 0
        for index in rng.permutation(len(candidates)):
            row = candidates[int(index)]
            if row["group_id"] in used_groups:
                continue
            selected.append(row)
            used_groups.add(row["group_id"])
            chosen += 1
            if chosen == examples_per_label:
                break
        if chosen != examples_per_label:
            raise ValueError("Not enough distinct training groups for the fixed sanity subset.")
    return selected


def validate_development(store: DevelopmentStore):
    development = store.load("donate", "development")
    ids = {row["sample_id"] for row in development}
    labels = store.config["datasets"]["donate"]["labels"]
    if (
        len(ids) != len(development)
        or len(ids) != store.config["datasets"]["donate"]["counts"]["development"]
    ):
        raise ValueError("Development sample count or identity changed.")
    reference = {row["sample_id"]: row for row in development}
    validation_ids, folds = [], {}
    for fold in store.config["training"]["folds"]:
        split = store.fold(fold)["donate"]
        train, val = split["train"], split["val"]
        if any(reference.get(row["sample_id"]) != row for row in train + val):
            raise ValueError("Fold rows differ from the frozen development manifest.")
        if len({row["sample_id"] for row in train + val}) != len(train) + len(val):
            raise ValueError("Duplicate sample or train/validation overlap.")
        if {row["sample_id"] for row in train + val} != ids:
            raise ValueError("Fold does not partition development data.")
        if {r["group_id"] for r in train} & {r["group_id"] for r in val}:
            raise ValueError("Source group leakage between train and validation.")
        if any(
            not any(r["label"] == label for r in part) for part in (train, val) for label in labels
        ):
            raise ValueError("A fold is missing a label.")
        validation_ids.extend(row["sample_id"] for row in val)
        folds[str(fold)] = {
            part: dict(Counter(r["label"] for r in rows)) for part, rows in split.items()
        }
    if Counter(validation_ids) != Counter({key: 1 for key in ids}):
        raise ValueError("OOF validation must cover development exactly once.")
    return {
        "count": len(development),
        "groups": len({r["group_id"] for r in development}),
        "labels": dict(Counter(r["label"] for r in development)),
        "folds": folds,
        "test_used": False,
    }
