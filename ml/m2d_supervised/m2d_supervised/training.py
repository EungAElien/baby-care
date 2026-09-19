"""Resumable development-fold training; sealed tests are never evaluated here."""

from __future__ import annotations

import gc
import importlib.metadata
import math
import os
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .checkpoint import (
    atomic_torch,
    capture_rng,
    frozen_digest,
    load_partial,
    partial_state,
    restore_rng,
    restore_training,
    resume_payload,
)
from .data import ManifestStore, RuntimePaths, donate_class_weights, enes_class_weights, epoch_plan
from .engine import evaluate, make_optimizer, metrics_from_predictions, phase_at, train_update
from .model import M2DClassifier, load_encoder, verify_m2d_source
from .util import atomic_json, code_hash, object_sha256, read_json, sha256_file, utc_now


def runtime_versions() -> dict[str, str]:
    return {name: importlib.metadata.version(name) for name in ("torch", "numpy", "timm")}


def identity_for(
    config: dict[str, Any],
    config_path: Path,
    experiment: str,
    fold: int,
    seed: int,
    *,
    device: str = "mps",
    purpose: str = "development",
) -> dict[str, Any]:
    if experiment not in config["model"]["registry"]:
        raise ValueError("Unknown initialization experiment.")
    if fold not in config["training"]["folds"] or seed not in config["training"]["seeds"]:
        raise ValueError("Use one of the frozen seeds and folds.")
    digest, _ = code_hash(Path(__file__).parent, config_path)
    return {
        "experiment": experiment,
        "fold": fold,
        "seed": seed,
        "purpose": purpose,
        "experiment_version": config["experiment_version"],
        "initialization": config["model"]["registry"][experiment],
        "config_sha256": object_sha256(config),
        "code_sha256": digest,
        "manifest_sha256": {
            d: config["datasets"][d]["manifest_sha256"] for d in ("donate", "enes")
        },
        "m2d_source_commit": config["model"]["source_commit"],
        "runtime_versions": runtime_versions(),
        "device": device,
    }


def create_model(
    config: dict[str, Any], paths: RuntimePaths, experiment: str, seed: int
) -> M2DClassifier:
    entry = config["model"]["registry"][experiment]
    verify_m2d_source(paths.m2d_source_root, config["model"]["source_commit"])
    encoder, _ = load_encoder(
        paths.m2d_source_root,
        paths.checkpoint(config, experiment),
        expected_sha256=entry["sha256"],
        expected_norm_stats=(
            config["preprocessing"]["normalization_mean"],
            config["preprocessing"]["normalization_std"],
        ),
        expected_norm_stats_origin=entry["norm_stats_origin"],
    )
    return M2DClassifier(
        encoder,
        {d: config["datasets"][d]["labels"] for d in ("donate", "enes")},
        entry["trained_heads"],
        head_seed=seed,
    )


def new_state() -> dict[str, Any]:
    return {
        "epoch": 0,
        "next_update": 0,
        "global_step": 0,
        "best_score": -1.0,
        "best_epoch": 0,
        "bad_epochs": 0,
        "history": [],
        "updates": [],
        "exposures": {},
        "compute_seconds": 0.0,
        "validation_seconds": 0.0,
        "checkpoint_seconds": 0.0,
    }


def record_exposures(
    state: dict[str, Any], selected: dict[str, Any], heads: tuple[str, ...]
) -> None:
    for dataset in heads:
        counts = state["exposures"].setdefault(
            dataset, {"total": 0, "samples": {}, "babies": {}, "bouts": {}}
        )
        for row, _ in selected[dataset]:
            counts["total"] += 1
            for field, key in (
                ("samples", "sample_id"),
                ("babies", "baby_id"),
                ("bouts", "bout_id"),
            ):
                if row.get(key):
                    counts[field][row[key]] = counts[field].get(row[key], 0) + 1


def completed_report(run: Path, identity: dict[str, Any]) -> dict[str, Any] | None:
    if not (run / "completion.json").exists():
        return None
    report = read_json(run / "completion.json")
    if report["identity"] != identity:
        raise RuntimeError("Completed run identity changed.")
    for name, expected in report["artifact_sha256"].items():
        if sha256_file(run / name) != expected:
            raise RuntimeError(f"Completed artifact changed: {name}")
    return report


def run_fold(
    config: dict[str, Any],
    config_path: Path,
    paths: RuntimePaths,
    experiment: str,
    fold: int,
    seed: int,
    *,
    device: str = "mps",
    interrupt: Callable[[], None] = lambda: None,
    progress: Callable[[dict[str, Any]], None] = lambda _: None,
    max_additional_updates: int | None = None,
) -> dict[str, Any]:
    identity = identity_for(config, config_path, experiment, fold, seed, device=device)
    run_id = f"{experiment}-s{seed}-f{fold}-{object_sha256(identity)[:12]}"
    run = paths.output_root / "development" / run_id
    run.mkdir(parents=True, exist_ok=True)
    complete = completed_report(run, identity)
    if complete is not None:
        return complete
    if (run / "identity.json").exists() and read_json(run / "identity.json") != identity:
        raise RuntimeError("Existing run directory belongs to another experiment.")
    atomic_json(run / "identity.json", identity)
    atomic_json(run / "config.json", config)
    torch.set_num_threads(1)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if device == "mps":
        torch.mps.manual_seed(seed)
    manifests = ManifestStore(config, paths).fold(fold)
    model = create_model(config, paths, experiment, seed).to(device)
    baseline_frozen = frozen_digest(model, config)
    weights = {"donate": donate_class_weights(manifests["donate"]["train"], model.labels["donate"])}
    if "enes" in model.trained_heads:
        weights["enes"] = enes_class_weights(manifests["enes"]["train"], model.labels["enes"])
    optimizer = make_optimizer(model, config, "warmup")
    state, optimizer_phase = new_state(), "warmup"
    last_path = run / "last.pt"
    if last_path.exists():
        payload = torch.load(last_path, map_location="cpu", weights_only=True)
        optimizer, state, optimizer_phase = restore_training(
            model, config, payload, identity, baseline_frozen, device
        )
        del payload
    starting_step = state["global_step"]

    def announce(status: str, **extra: Any) -> dict[str, Any]:
        value = {
            "status": status,
            "run_id": run_id,
            "experiment": experiment,
            "seed": seed,
            "fold": fold,
            "epoch": state["epoch"] + 1,
            "phase": optimizer_phase,
            "next_update": state["next_update"],
            "global_step": state["global_step"],
            "best_epoch": state["best_epoch"],
            "best_macro_f1": state["best_score"],
            "compute_seconds": state["compute_seconds"],
            "updated_utc": utc_now(),
            "pid": os.getpid(),
            **extra,
        }
        atomic_json(run / "progress.json", value)
        progress(value)
        return value

    def save() -> None:
        started = time.monotonic()
        payload = resume_payload(
            model, config, optimizer, state, identity, optimizer_phase, device, baseline_frozen
        )
        atomic_torch(last_path, payload, config["training"]["minimum_free_gib"])
        state["checkpoint_seconds"] += time.monotonic() - started

    total_epochs = (
        config["training"]["warmup"]["epochs"] + config["training"]["finetune"]["maximum_epochs"]
    )
    save()
    announce("running")
    try:
        while (
            state["epoch"] < total_epochs
            and state["bad_epochs"] < config["training"]["finetune"]["patience"]
        ):
            interrupt()
            epoch = state["epoch"]
            phase = phase_at(epoch, config)
            if phase != optimizer_phase:
                optimizer = make_optimizer(model, config, phase)
                optimizer_phase = phase
                save()
            plan = epoch_plan(manifests, seed, phase, epoch)
            batch = (
                config["training"]["warmup"]["effective_batch"]
                if phase == "warmup"
                else config["training"]["finetune"]["effective_batch_per_dataset"]
            )
            updates = math.ceil(len(plan["donate"]) / batch)
            for update in range(state["next_update"], updates):
                interrupt()
                selected = {
                    d: rows[update * batch : (update + 1) * batch] for d, rows in plan.items()
                }
                rng_before = capture_rng(device)
                started = time.monotonic()
                try:
                    result = train_update(
                        model,
                        optimizer,
                        paths,
                        config,
                        selected,
                        weights,
                        seed=seed,
                        phase=phase,
                        epoch=epoch,
                        device=device,
                        interrupt=interrupt,
                    )
                except InterruptedError:
                    # Interrupts are checked before optimizer.step; replay the entire update.
                    optimizer.zero_grad(set_to_none=True)
                    restore_rng(rng_before, device)
                    raise
                elapsed = time.monotonic() - started
                state["compute_seconds"] += elapsed
                state["global_step"] += 1
                state["next_update"] = update + 1
                state["updates"].append(
                    {"epoch": epoch + 1, "update": update + 1, "seconds": elapsed, **result}
                )
                record_exposures(state, selected, model.trained_heads)
                if (
                    state["global_step"] == 1
                    or state["global_step"] % config["training"]["checkpoint_every_updates"] == 0
                ):
                    save()
                announce(
                    "running",
                    updates_in_epoch=updates,
                    last_update=result,
                    last_update_seconds=elapsed,
                    last_update_examples_per_second=sum(result["examples"].values()) / elapsed,
                )
                if (
                    max_additional_updates
                    and state["global_step"] - starting_step >= max_additional_updates
                ):
                    raise InterruptedError("Requested bounded update limit reached.")
            # The checkpoint makes an interrupted validation replayable without repeating training.
            save()
            announce("validating")
            started = time.monotonic()
            metrics, predictions = evaluate(
                model, paths, config, "donate", manifests["donate"]["val"], device, interrupt
            )
            state["validation_seconds"] += time.monotonic() - started
            score = metrics["macro_f1"]
            improved = (
                score
                > state["best_score"]
                + config["training"]["finetune"]["minimum_absolute_improvement"]
            )
            state["history"].append(
                {"epoch": epoch + 1, "phase": phase, "donate": metrics, "selected": improved}
            )
            if improved:
                best = {
                    "identity": identity,
                    "frozen_sha256": baseline_frozen,
                    "partial_model": partial_state(model, config),
                    "epoch": epoch + 1,
                    "metrics": metrics,
                    "predictions": predictions,
                    "trained_heads": list(model.trained_heads),
                }
                atomic_torch(run / "best.pt", best, config["training"]["minimum_free_gib"])
                state["best_score"], state["best_epoch"], state["bad_epochs"] = score, epoch + 1, 0
            elif phase == "finetune":
                state["bad_epochs"] += 1
            state["epoch"] += 1
            state["next_update"] = 0
            save()
            print(
                f"{run_id}: epoch {epoch + 1}, Donate validation Macro-F1={score:.4f}", flush=True
            )

        announce("verifying_best")
        best = torch.load(run / "best.pt", map_location="cpu", weights_only=True)
        load_partial(model, config, best, identity, baseline_frozen)
        # A second forward verifies the saved partial artifact, not another selection attempt.
        restored_metrics, restored = evaluate(
            model, paths, config, "donate", manifests["donate"]["val"], device, interrupt
        )
        expected = {r["sample_id"]: r["probabilities"] for r in best["predictions"]}
        delta = max(
            abs(a - b)
            for row in restored
            for a, b in zip(row["probabilities"], expected[row["sample_id"]], strict=True)
        )
        if delta > 1e-6 or restored_metrics != best["metrics"]:
            raise RuntimeError(f"Saved best predictions do not reproduce: maximum delta {delta}")
        atomic_json(
            run / "donate_validation.json",
            {"metrics": best["metrics"], "predictions": best["predictions"]},
        )
        artifacts = ["best.pt", "donate_validation.json", "identity.json", "config.json"]
        if "enes" in model.trained_heads:
            announce("enes_validation")
            enes_metrics, enes_predictions = evaluate(
                model, paths, config, "enes", manifests["enes"]["val"], device, interrupt
            )
            grouped = {}
            for key in ("baby_id", "bout_id"):
                grouped[key] = {
                    group: metrics_from_predictions(
                        [r for r in enes_predictions if r[key] == group], model.labels["enes"]
                    )
                    for group in sorted({r[key] for r in enes_predictions})
                }
            atomic_json(
                run / "enes_validation.json",
                {"metrics": enes_metrics, "by_group": grouped, "predictions": enes_predictions},
            )
            artifacts.append("enes_validation.json")
        atomic_json(run / "history.json", state)
        artifacts.append("history.json")
        report = {
            "status": "completed",
            "run_id": run_id,
            "identity": identity,
            "completed_utc": utc_now(),
            "best_epoch": best["epoch"],
            "best_donate_macro_f1": best["metrics"]["macro_f1"],
            "epochs_executed": state["epoch"],
            "updates_executed": state["global_step"],
            "compute_seconds": state["compute_seconds"],
            "best_reload_maximum_probability_delta": delta,
            "frozen_tensors_verified": True,
            "trained_heads": list(model.trained_heads),
            "test_used": False,
            "calibration_status": "NOT_VALIDATED",
            "release_ready": False,
            "artifact_sha256": {name: sha256_file(run / name) for name in artifacts},
        }
        atomic_json(run / "completion.json", report)
        completed_report(run, identity)
        # Only our redundant resume file is removed after the best/result hashes are verified.
        last_path.unlink(missing_ok=True)
        announce("completed")
        return report
    except InterruptedError as error:
        # During best verification the disk checkpoint still contains the final training state.
        if (
            state["epoch"] < total_epochs
            and state["bad_epochs"] < config["training"]["finetune"]["patience"]
        ):
            save()
        return announce("paused", reason=str(error), resume_checkpoint=str(last_path))
    except Exception as error:
        announce(
            "failed", reason=f"{type(error).__name__}: {error}", resume_checkpoint=str(last_path)
        )
        raise
    finally:
        model = None
        optimizer = None
        gc.collect()
        if device == "mps":
            torch.mps.empty_cache()
