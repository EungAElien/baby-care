"""Isolated MPS timing and serialization/resume validation on development inputs."""

from __future__ import annotations

import copy
import gc
import math
import time
from pathlib import Path
from typing import Any

import torch

from .checkpoint import (
    atomic_torch,
    cpu_tree,
    frozen_digest,
    partial_state,
    restore_training,
    resume_payload,
    state_digest,
)
from .data import ManifestStore, RuntimePaths, donate_class_weights, enes_class_weights, epoch_plan
from .engine import evaluate, make_optimizer, train_update
from .preflight import run_preflight
from .runtime import RuntimeGuard, training_locks
from .training import create_model, identity_for, new_state, runtime_versions
from .util import atomic_json, code_hash, free_bytes, utc_now


def run_pilot(config: dict[str, Any], config_path: Path, paths: RuntimePaths) -> dict[str, Any]:
    output = paths.output_root / "pilot"
    output.mkdir(parents=True, exist_ok=True)
    digest, _ = code_hash(Path(__file__).parent, config_path)
    report: dict[str, Any] = {
        "status": "running",
        "purpose": "MPS_PILOT_NOT_A_DEVELOPMENT_RUN",
        "code_sha256": digest,
        "started_utc": utc_now(),
        "test_used": False,
        "runtime_versions": runtime_versions(),
    }
    guard = RuntimeGuard(paths, config["training"]["minimum_free_gib"])
    try:
        with training_locks(paths):
            guard.check()
            torch.set_num_threads(1)
            run_preflight(config, config_path, paths, locks_held=True)
            manifests = ManifestStore(config, paths).fold(0)
            model = create_model(config, paths, "C", 42).to("mps")
            frozen = frozen_digest(model, config)
            encoder_before = state_digest(model.encoder.state_dict())
            weights = {
                "donate": donate_class_weights(
                    manifests["donate"]["train"], model.labels["donate"]
                ),
                "enes": enes_class_weights(manifests["enes"]["train"], model.labels["enes"]),
            }
            timings = {}
            for phase in ("warmup", "finetune"):
                optimizer = make_optimizer(model, config, phase)
                plan = epoch_plan(manifests, 42, phase, 0)
                batch = (
                    config["training"]["warmup"]["effective_batch"]
                    if phase == "warmup"
                    else config["training"]["finetune"]["effective_batch_per_dataset"]
                )
                for recipe in ("B", "C"):
                    benchmark_config = copy.deepcopy(config)
                    if recipe == "B":
                        benchmark_config["training"]["joint_loss"]["enes"] = 0.0
                    samples = []
                    for update in range(2):
                        selected = {
                            d: rows[update * batch : (update + 1) * batch]
                            for d, rows in plan.items()
                        }
                        started = time.monotonic()
                        train_update(
                            model,
                            optimizer,
                            paths,
                            benchmark_config,
                            selected,
                            weights,
                            seed=42,
                            phase=phase,
                            epoch=0,
                            device="mps",
                            interrupt=guard.check,
                        )
                        torch.mps.synchronize()
                        samples.append(time.monotonic() - started)
                    timings[f"{recipe}_{phase}_update_seconds"] = samples
                if phase == "warmup" and state_digest(model.encoder.state_dict()) != encoder_before:
                    raise RuntimeError("Warm-up changed the encoder.")
            if frozen_digest(model, config) != frozen:
                raise RuntimeError("Finetuning changed a frozen tensor.")
            report["warmup_encoder_unchanged"] = True
            report["finetune_frozen_tensors_unchanged"] = True

            identity = identity_for(config, config_path, "C", 0, 42, purpose="pilot")
            state = new_state()
            state.update(epoch=3, global_step=8)
            started = time.monotonic()
            checkpoint_bytes = atomic_torch(
                output / "resume.pt",
                resume_payload(
                    model, config, optimizer, state, identity, "finetune", "mps", frozen
                ),
                config["training"]["minimum_free_gib"],
            )
            checkpoint_seconds = time.monotonic() - started
            before_logits, before_predictions = evaluate(
                model, paths, config, "donate", manifests["donate"]["val"][:4], "mps", guard.check
            )
            selected = {
                d: rows[:16] for d, rows in epoch_plan(manifests, 42, "finetune", 3).items()
            }
            train_update(
                model,
                optimizer,
                paths,
                config,
                selected,
                weights,
                seed=42,
                phase="finetune",
                epoch=3,
                device="mps",
                interrupt=guard.check,
            )
            expected = cpu_tree(partial_state(model, config))
            del optimizer, model
            gc.collect()
            torch.mps.empty_cache()
            model = create_model(config, paths, "C", 42).to("mps")
            saved = torch.load(output / "resume.pt", map_location="cpu", weights_only=True)
            optimizer, restored_state, phase = restore_training(
                model, config, saved, identity, frozen, "mps"
            )
            after_logits, after_predictions = evaluate(
                model, paths, config, "donate", manifests["donate"]["val"][:4], "mps", guard.check
            )
            if before_logits != after_logits or before_predictions != after_predictions:
                raise RuntimeError("Partial checkpoint inference round-trip differs.")
            # Evaluation consumes no RNG in the pinned model; reset the recorded state explicitly.
            from .checkpoint import restore_rng

            restore_rng(saved["rng"], "mps")
            train_update(
                model,
                optimizer,
                paths,
                config,
                selected,
                weights,
                seed=42,
                phase=phase,
                epoch=3,
                device="mps",
                interrupt=guard.check,
            )
            actual = cpu_tree(partial_state(model, config))
            maximum_delta = max(
                float((actual[key] - expected[key]).abs().max()) for key in expected
            )
            if maximum_delta > 1e-6 or restored_state["global_step"] != 8:
                raise RuntimeError(f"MPS resume next-update mismatch: {maximum_delta}")
            report["mps_resume_maximum_weight_delta"] = maximum_delta
            report["partial_checkpoint_inference_equal"] = True

            validation_times = {}
            for dataset in ("donate", "enes"):
                rows = manifests[dataset]["val"][:32]
                started = time.monotonic()
                evaluate(model, paths, config, dataset, rows, "mps", guard.check)
                torch.mps.synchronize()
                validation_times[dataset] = (time.monotonic() - started) / len(rows)
            best_bytes = atomic_torch(
                output / "partial.pt",
                {"partial_model": partial_state(model, config)},
                config["training"]["minimum_free_gib"],
            )
            estimate = {}
            for fine_epochs in (5, 20):
                seconds = 0.0
                for fold in config["training"]["folds"]:
                    fold_rows = ManifestStore(config, paths).fold(fold)
                    count = len(fold_rows["donate"]["train"])
                    val_count = len(fold_rows["donate"]["val"])
                    for recipe in ("B", "C", "A"):
                        timing_recipe = "B" if recipe == "A" else recipe
                        seconds += (
                            3
                            * math.ceil(count / 32)
                            * max(timings[f"{timing_recipe}_warmup_update_seconds"])
                        )
                        seconds += (
                            fine_epochs
                            * math.ceil(count / 16)
                            * max(timings[f"{timing_recipe}_finetune_update_seconds"])
                        )
                        seconds += (4 + fine_epochs) * val_count * validation_times["donate"]
                        # Include epoch-boundary, best and periodic checkpoint saves.
                        seconds += (3 + fine_epochs) * checkpoint_seconds * 3
                        if recipe == "C":
                            seconds += len(fold_rows["enes"]["val"]) * validation_times["enes"]
                estimate[f"finetune_{fine_epochs}_epochs"] = seconds * len(
                    config["training"]["seeds"]
                )
            report.update(
                status="passed",
                completed_utc=utc_now(),
                timings=timings,
                validation_seconds_per_example=validation_times,
                resume_checkpoint_bytes=checkpoint_bytes,
                best_partial_bytes=best_bytes,
                checkpoint_seconds=checkpoint_seconds,
                estimated_matrix_seconds=estimate,
                estimate_note=(
                    "Pilot-based scenarios; loading, thermal throttling and other apps "
                    "add uncertainty."
                ),
                free_bytes_after=free_bytes(paths.output_root),
                mps_current_allocated_bytes=torch.mps.current_allocated_memory(),
                mps_driver_allocated_bytes=torch.mps.driver_allocated_memory(),
                estimated_retained_bytes=27 * best_bytes + checkpoint_bytes + best_bytes,
            )
            del model, optimizer, saved
            gc.collect()
            torch.mps.empty_cache()
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        atomic_json(output / "report.json", report)
    return report
