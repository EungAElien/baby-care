"""Fresh full-development retraining at a sealed, fixed total epoch count."""

from __future__ import annotations

import gc
import math
import os
import random
import time
from pathlib import Path

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
from .comparison import checked_artifacts, freeze_json
from .data import ManifestStore, donate_class_weights, enes_class_weights, epoch_plan
from .engine import evaluate, make_optimizer, phase_at, train_update
from .training import create_model, new_state, record_exposures, runtime_versions
from .util import atomic_json, code_hash, object_sha256, read_json, sha256_file, utc_now


def final_identity(config, config_path, plan, recipe, device):
    item = next(r for r in plan["final_runs"] if r["recipe"] == recipe)
    return {
        "purpose": "fixed_full_development_retrain",
        "recipe": recipe,
        "seed": plan["seed"],
        "total_epochs": item["epochs"],
        "selection_plan_sha256": object_sha256(plan),
        "config_sha256": object_sha256(config),
        "code_sha256": code_hash(Path(__file__).parent, config_path)[0],
        "initialization": config["model"]["registry"][recipe],
        "runtime_versions": runtime_versions(),
        "device": device,
    }


def final_run(
    config,
    config_path,
    paths,
    plan,
    recipe,
    *,
    device="mps",
    interrupt=lambda: None,
    progress=lambda _: None,
    max_additional_updates=None,
):
    identity = final_identity(config, config_path, plan, recipe, device)
    total_epochs, seed = identity["total_epochs"], identity["seed"]
    maximum = (
        config["training"]["warmup"]["epochs"] + config["training"]["finetune"]["maximum_epochs"]
    )
    if not 1 <= total_epochs <= maximum or seed != config["selection"]["final_seed"]:
        raise RuntimeError("Final schedule violates the frozen recipe.")
    run = paths.output_root / "post_training" / "final" / recipe
    run.mkdir(parents=True, exist_ok=True)
    freeze_json(run / "identity.json", identity)
    if (run / "completion.json").exists():
        report = read_json(run / "completion.json")
        if report["identity"] != identity:
            raise RuntimeError("Final completion identity differs.")
        checked_artifacts(run, report)
        return report
    torch.set_num_threads(1)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if device == "mps":
        torch.mps.manual_seed(seed)
    heads = config["model"]["registry"][recipe]["trained_heads"]
    store = ManifestStore(config, paths)
    manifests = {d: {"train": store.load(d, "development")} for d in heads}
    # epoch_plan always constructs the hierarchical auxiliary plan; B/A never consume it.
    if "enes" not in manifests:
        manifests["enes"] = {"train": store.load("enes", "development")}
    model = create_model(config, paths, recipe, seed).to(device)
    baseline = frozen_digest(model, config)
    weights = {"donate": donate_class_weights(manifests["donate"]["train"], model.labels["donate"])}
    if "enes" in heads:
        weights["enes"] = enes_class_weights(manifests["enes"]["train"], model.labels["enes"])
    optimizer = make_optimizer(model, config, "warmup")
    state, phase = new_state(), "warmup"
    last = run / "last.pt"
    if last.exists():
        payload = torch.load(last, map_location="cpu", weights_only=True)
        optimizer, state, phase = restore_training(
            model, config, payload, identity, baseline, device
        )
        del payload
    starting_step = state["global_step"]

    def save():
        atomic_torch(
            last,
            resume_payload(model, config, optimizer, state, identity, phase, device, baseline),
            config["training"]["minimum_free_gib"],
        )

    def announce(status, **extra):
        report = {
            "status": status,
            "recipe": recipe,
            "seed": seed,
            "pid": os.getpid(),
            "completed_epochs": state["epoch"],
            "total_epochs": total_epochs,
            "next_update": state["next_update"],
            "global_step": state["global_step"],
            "compute_seconds": state["compute_seconds"],
            "updated_utc": utc_now(),
            **extra,
        }
        atomic_json(run / "progress.json", report)
        progress(report)
        return report

    try:
        save()
        while state["epoch"] < total_epochs:
            interrupt()
            epoch = state["epoch"]
            wanted_phase = phase_at(epoch, config)
            if wanted_phase != phase:
                phase = wanted_phase
                optimizer = make_optimizer(model, config, phase)
                save()
            scheduled = epoch_plan(manifests, seed, phase, epoch)
            batch = (
                config["training"]["warmup"]["effective_batch"]
                if phase == "warmup"
                else config["training"]["finetune"]["effective_batch_per_dataset"]
            )
            for update in range(state["next_update"], math.ceil(len(scheduled["donate"]) / batch)):
                interrupt()
                selected = {
                    d: rows[update * batch : (update + 1) * batch] for d, rows in scheduled.items()
                }
                before = capture_rng(device)
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
                    optimizer.zero_grad(set_to_none=True)
                    restore_rng(before, device)
                    raise
                state["compute_seconds"] += time.monotonic() - started
                state["global_step"] += 1
                state["next_update"] = update + 1
                state["updates"].append({"epoch": epoch + 1, "update": update + 1, **result})
                record_exposures(state, selected, model.trained_heads)
                if (
                    state["global_step"] == 1
                    or state["global_step"] % config["training"]["checkpoint_every_updates"] == 0
                ):
                    save()
                announce("training", epoch=epoch + 1, phase=phase)
                if (
                    max_additional_updates
                    and state["global_step"] - starting_step >= max_additional_updates
                ):
                    raise InterruptedError("Requested bounded update limit reached.")
            state["history"].append({"epoch": epoch + 1, "phase": phase})
            state["epoch"] += 1
            state["next_update"] = 0
            save()
            print(f"Final {recipe}: epoch {state['epoch']}/{total_epochs} complete", flush=True)
        interrupt()
        # Development inputs only: this is artifact fidelity, not a selection/validation score.
        probe_rows = manifests["donate"]["train"][:2]
        _, expected = evaluate(model, paths, config, "donate", probe_rows, device, interrupt)
        payload = {
            "identity": identity,
            "frozen_sha256": baseline,
            "partial_model": partial_state(model, config),
            "trained_heads": heads,
        }
        atomic_torch(run / "model.pt", payload, config["training"]["minimum_free_gib"])
        saved = torch.load(run / "model.pt", map_location="cpu", weights_only=True)
        load_partial(model, config, saved, identity, baseline)
        _, actual = evaluate(model, paths, config, "donate", probe_rows, device, interrupt)
        delta = max(
            abs(a - b)
            for x, y in zip(expected, actual, strict=True)
            for a, b in zip(x["probabilities"], y["probabilities"], strict=True)
        )
        if delta > 1e-6:
            raise RuntimeError("Final partial checkpoint does not reproduce its development probe.")
        atomic_json(run / "history.json", state)
        atomic_json(
            run / "reload_probe.json",
            {"split": "development", "predictions": actual, "max_abs_delta": delta},
        )
        report = {
            "status": "completed",
            "identity": identity,
            "completed_utc": utc_now(),
            "epochs_executed": state["epoch"],
            "updates_executed": state["global_step"],
            "compute_seconds": state["compute_seconds"],
            "test_used": False,
            "frozen_parameters_verified": frozen_digest(model, config) == baseline,
            "artifact_sha256": {
                name: sha256_file(run / name)
                for name in ("identity.json", "model.pt", "history.json", "reload_probe.json")
            },
        }
        atomic_json(run / "completion.json", report)
        checked_artifacts(run, report)
        announce("completed")
        return report
    except InterruptedError as error:
        save()
        return announce("paused", reason=str(error))
    except Exception as error:
        announce("failed", reason=f"{type(error).__name__}: {error}")
        raise
    finally:
        model = optimizer = None
        gc.collect()
        if device == "mps":
            torch.mps.empty_cache()
