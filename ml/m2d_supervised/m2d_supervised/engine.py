"""Fixed phase, accumulated weighted-loss and development-evaluation operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import torch
from torch.nn import functional as F

from .data import PlannedRow, RuntimePaths, microbatches
from .model import M2DClassifier


def phase_at(epoch: int, config: dict[str, Any]) -> str:
    return "warmup" if epoch < config["training"]["warmup"]["epochs"] else "finetune"


def partial_names(model: M2DClassifier, config: dict[str, Any]) -> set[str]:
    blocks = config["training"]["finetune"]["trainable_encoder_blocks"]
    norm = ("encoder.norm.",) if config["training"]["finetune"]["train_final_norm"] else ()
    prefixes = ("heads.", *norm, *(f"encoder.blocks.{i}." for i in blocks))
    return {name for name in model.state_dict() if name.startswith(prefixes)}


def make_optimizer(
    model: M2DClassifier, config: dict[str, Any], phase: str
) -> torch.optim.Optimizer:
    if phase not in {"warmup", "finetune"}:
        raise ValueError(f"Unknown training phase: {phase}")
    recipe = config["training"]
    model.requires_grad_(False)
    model.heads.requires_grad_(True)
    groups = [{"params": list(model.heads.parameters()), "lr": recipe["warmup"]["head_lr"]}]
    if phase == "finetune":
        tune = recipe["finetune"]
        for index in tune["trainable_encoder_blocks"]:
            model.encoder.blocks[index].requires_grad_(True)
        if tune["train_final_norm"]:
            model.encoder.norm.requires_grad_(True)
        groups[0]["lr"] = tune["head_lr"]
        encoder_parameters = [p for p in model.encoder.parameters() if p.requires_grad]
        if encoder_parameters:
            groups.append(
                {
                    "params": encoder_parameters,
                    "lr": tune["encoder_lr"],
                }
            )
    return torch.optim.AdamW(
        groups,
        betas=tuple(recipe["optimizer"]["betas"]),
        weight_decay=recipe["optimizer"]["weight_decay"],
        foreach=False,
    )


def weighted_losses(
    logits: torch.Tensor, targets: torch.Tensor, weights: torch.Tensor
) -> torch.Tensor:
    return F.cross_entropy(logits, targets, reduction="none") * weights[targets]


def train_update(
    model: M2DClassifier,
    optimizer: torch.optim.Optimizer,
    paths: RuntimePaths,
    config: dict[str, Any],
    selected: dict[str, list[PlannedRow]],
    weights: dict[str, torch.Tensor],
    *,
    seed: int,
    phase: str,
    epoch: int,
    device: str,
    interrupt: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    model.train()
    if phase == "warmup" or not any(p.requires_grad for p in model.encoder.parameters()):
        model.encoder.eval()
    optimizer.zero_grad(set_to_none=True)
    totals: dict[str, float] = {}
    examples: dict[str, int] = {}
    for dataset in model.trained_heads:
        coefficient = config["training"]["joint_loss"][dataset]
        if coefficient == 0:
            continue
        rows = selected[dataset]
        if not rows:
            raise ValueError("An optimizer update cannot contain an empty dataset batch.")
        totals[dataset] = 0.0
        examples[dataset] = 0
        for _, inputs, targets in microbatches(
            paths,
            config,
            dataset,
            rows,
            seed=seed,
            phase=phase,
            epoch=epoch,
            micro_batch=config["training"]["finetune"]["micro_batch_maximum"],
        ):
            interrupt()
            logits = model(inputs.to(device), dataset)
            # Use the full logical update's sample count, including its final short batch.
            loss = weighted_losses(logits, targets.to(device), weights[dataset].to(device))
            loss = loss.sum() / len(rows)
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError("Non-finite loss; optimizer update was not applied.")
            (coefficient * loss).backward()
            totals[dataset] += float(loss.detach())
            examples[dataset] += len(targets)
    if not totals:
        raise ValueError("No active dataset loss.")
    interrupt()
    parameters = [p for p in model.parameters() if p.requires_grad and p.grad is not None]
    norm = torch.nn.utils.clip_grad_norm_(
        parameters, config["training"]["gradient_clip_norm"], error_if_nonfinite=True
    )
    optimizer.step()
    for parameter in parameters:
        if not bool(torch.isfinite(parameter).all()):
            raise FloatingPointError("Non-finite weights; retain the preceding disk checkpoint.")
    return {"loss_by_dataset": totals, "examples": examples, "gradient_norm": float(norm)}


def metrics_from_predictions(rows: list[dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    if not rows:
        raise ValueError("Cannot score an empty prediction set.")
    matrix = np.zeros((len(labels), len(labels)), dtype=np.int64)
    for row in rows:
        matrix[row["true_index"], row["predicted_index"]] += 1
    support, predicted = matrix.sum(1), matrix.sum(0)
    true_positive = np.diag(matrix).astype(float)
    precision = np.divide(true_positive, predicted, out=np.zeros(len(labels)), where=predicted > 0)
    recall = np.divide(true_positive, support, out=np.zeros(len(labels)), where=support > 0)
    f1 = np.divide(
        2 * precision * recall,
        precision + recall,
        out=np.zeros(len(labels)),
        where=precision + recall > 0,
    )
    return {
        "macro_f1": float(f1.mean()),
        "accuracy": float(true_positive.sum() / support.sum()),
        "labels": labels,
        "confusion_matrix": matrix.tolist(),
        "unpredicted_labels": [label for i, label in enumerate(labels) if predicted[i] == 0],
        "per_class": {
            label: {
                "precision": float(precision[i]),
                "recall": float(recall[i]),
                "f1": float(f1[i]),
                "support": int(support[i]),
            }
            for i, label in enumerate(labels)
        },
    }


def evaluate(
    model: M2DClassifier,
    paths: RuntimePaths,
    config: dict[str, Any],
    dataset: str,
    rows: list[dict[str, str]],
    device: str,
    interrupt: Callable[[], None] = lambda: None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    model.eval()
    predictions = []
    with torch.inference_mode():
        for batch, inputs, targets in microbatches(
            paths,
            config,
            dataset,
            [(row, i) for i, row in enumerate(rows)],
            seed=0,
            phase="validation",
            epoch=0,
            micro_batch=config["training"]["finetune"]["micro_batch_maximum"],
        ):
            interrupt()
            probabilities = model(inputs.to(device), dataset).softmax(-1).cpu()
            if not bool(torch.isfinite(probabilities).all()):
                raise FloatingPointError("Non-finite validation predictions.")
            for (row, _), truth, probs in zip(
                batch, targets.tolist(), probabilities.tolist(), strict=True
            ):
                predictions.append(
                    {
                        "sample_id": row["sample_id"],
                        "group_id": row.get("group_id", row.get("baby_id")),
                        "baby_id": row.get("baby_id"),
                        "bout_id": row.get("bout_id"),
                        "true_index": truth,
                        "predicted_index": int(np.argmax(probs)),
                        "probabilities": probs,
                    }
                )
    return metrics_from_predictions(predictions, config["datasets"][dataset]["labels"]), predictions
