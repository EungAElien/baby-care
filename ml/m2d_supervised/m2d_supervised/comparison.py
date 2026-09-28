"""Audited, paired out-of-fold comparison; no test features are read here."""

from __future__ import annotations

import itertools
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from .data import ManifestStore, RuntimePaths
from .engine import metrics_from_predictions
from .util import atomic_json, code_hash, object_sha256, read_json, sha256_file


def freeze_json(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    """Idempotent under the runner's exclusive lock; never replace a different decision."""
    if path.exists():
        if read_json(path) != value:
            raise RuntimeError(f"Frozen record differs: {path}")
    else:
        atomic_json(path, value)
    return value


def checked_artifacts(directory: Path, report: dict[str, Any]) -> int:
    for name, expected in report["artifact_sha256"].items():
        path = directory / name
        if path.resolve().parent != directory.resolve() or sha256_file(path) != expected:
            raise RuntimeError(f"Artifact integrity failure: {directory.name}/{name}")
    return len(report["artifact_sha256"])


def check_predictions(predictions, manifest, labels):
    expected = {r["sample_id"]: r for r in manifest}
    if len(expected) != len(manifest) or len(predictions) != len(expected):
        raise RuntimeError("Prediction count/manifest uniqueness mismatch.")
    observed = {}
    for row in predictions:
        sid = row["sample_id"]
        if sid not in expected or sid in observed:
            raise RuntimeError("Unexpected or duplicate prediction.")
        original = expected[sid]
        for key in ("group_id", "baby_id", "bout_id"):
            if (row.get(key) or None) != (original.get(key) or None):
                raise RuntimeError(f"Prediction grouping differs: {key}")
        probabilities = np.asarray(row["probabilities"])
        if (
            probabilities.shape != (len(labels),)
            or not np.isfinite(probabilities).all()
            or np.any(probabilities < 0)
            or not np.isclose(probabilities.sum(), 1, atol=1e-6)
            or row["predicted_index"] != int(probabilities.argmax())
            or row["true_index"] != int(original["label_index"])
        ):
            raise RuntimeError("Prediction label/probability mismatch.")
        observed[sid] = row
    return [observed[sid] for sid in sorted(observed)]


def summarize_seed_metrics(values, labels):
    scores = [m["macro_f1"] for m in values]
    return {
        "macro_f1_mean": statistics.mean(scores),
        "macro_f1_seed_sample_sd": statistics.stdev(scores) if len(scores) > 1 else None,
        "per_class_mean": {
            label: {
                key: statistics.mean(m["per_class"][label][key] for m in values)
                for key in ("precision", "recall", "f1", "support")
            }
            for label in labels
        },
    }


def choose_recipe(seed_metrics, config):
    labels = config["datasets"]["donate"]["labels"]
    summary = {r: summarize_seed_metrics(ms, labels) for r, ms in seed_metrics.items()}
    rule = config["selection"]
    standalone = "A" if summary["A"]["macro_f1_mean"] > summary["B"]["macro_f1_mean"] else "B"
    differences = [
        c["macro_f1"] - b["macro_f1"]
        for c, b in zip(seed_metrics["C"], seed_metrics[standalone], strict=True)
    ]
    drops = {
        label: summary[standalone]["per_class_mean"][label]["recall"]
        - summary["C"]["per_class_mean"][label]["recall"]
        for label in labels
        if label != "hungry"
    }
    checks = {
        "mean_gain": statistics.mean(differences) >= rule["c_minimum_mean_gain"],
        "improved_seeds": sum(d > 0 for d in differences) >= rule["c_minimum_improved_seeds"],
        "every_non_hungry_recall": all(
            drop <= rule["c_maximum_non_hungry_recall_drop"] for drop in drops.values()
        ),
    }
    selected = "C" if all(checks.values()) else standalone
    return {
        "selected": selected,
        "baseline": rule["baseline_for_selected"][selected],
        "standalone_best": standalone,
        "summary": summary,
        "c_comparison_reference": standalone,
        "c_paired_seed_differences": differences,
        "c_mean_gain": statistics.mean(differences),
        "c_non_hungry_recall_drops": drops,
        "c_acceptance_checks": checks,
    }


def paired_group_bootstrap(predictions, labels, *, repetitions=2000, seed=42):
    """Resample groups once per replicate, paired across recipes AND fixed seeds."""
    names = sorted(predictions)
    first = predictions[names[0]][0]
    groups = sorted({r["group_id"] for r in first})
    if not groups or any(not g for g in groups):
        raise RuntimeError("Bootstrap requires nonempty recording groups.")
    index = {group: i for i, group in enumerate(groups)}
    reference = sorted((r["sample_id"], r["group_id"], r["true_index"]) for r in first)
    n = len(labels)
    rng = np.random.default_rng(seed)
    counts = rng.multinomial(len(groups), np.full(len(groups), 1 / len(groups)), repetitions)
    samples = {}
    seeds = len(predictions[names[0]])
    for name in names:
        if len(predictions[name]) != seeds:
            raise RuntimeError("Paired seed count differs.")
        scores = []
        for rows in predictions[name]:
            if sorted((r["sample_id"], r["group_id"], r["true_index"]) for r in rows) != reference:
                raise RuntimeError("Bootstrap predictions are not paired.")
            matrices = np.zeros((len(groups), n, n), dtype=np.int64)
            for row in rows:
                matrices[index[row["group_id"]], row["true_index"], row["predicted_index"]] += 1
            confusion = (counts @ matrices.reshape(len(groups), -1)).reshape(-1, n, n)
            numerator = 2 * np.diagonal(confusion, axis1=1, axis2=2)
            denominator = confusion.sum(axis=1) + confusion.sum(axis=2)
            f1 = np.divide(
                numerator,
                denominator,
                out=np.zeros_like(numerator, dtype=float),
                where=denominator != 0,
            )
            scores.append(f1.mean(axis=1))
        samples[name] = np.mean(scores, axis=0)
    return {
        "repetitions": repetitions,
        "seed": seed,
        "groups": len(groups),
        "method": "paired recording-group percentile bootstrap; fixed seed-average Macro-F1",
        "limitation": "Conditional on trained seeds; not a seed CI or infant-generalization CI. "
        "Rare-label groups make intervals unstable; absent labels receive F1=0.",
        "macro_f1_ci95": {k: np.quantile(v, [0.025, 0.975]).tolist() for k, v in samples.items()},
        "paired_difference_ci95": {
            f"{right}-{left}": np.quantile(samples[right] - samples[left], [0.025, 0.975]).tolist()
            for left, right in itertools.combinations(names, 2)
        },
    }


def compare_development(config, config_path: Path, paths: RuntimePaths):
    matrix = read_json(paths.output_root / "matrix_progress.json")
    if (
        matrix["status"] != "completed_development_training"
        or matrix["completed_runs"] != 27
        or matrix["total_runs"] != 27
        or len(set(matrix["completed"])) != 27
        or matrix["test_used"] is not False
    ):
        raise RuntimeError("Development matrix is not complete and test-sealed.")
    try:
        os.kill(matrix["pid"], 0)
    except ProcessLookupError:
        pass
    else:
        raise RuntimeError("Development PID is still alive; inspect before post-training.")
    snapshot = paths.output_root / "code" / matrix["code_sha256"]
    old_config = snapshot / "config" / "experiment_v1.json"
    if (
        sha256_file(old_config) != matrix["config_sha256"]
        or sha256_file(config_path) != matrix["config_sha256"]
        or code_hash(snapshot / "m2d_supervised", old_config)[0] != matrix["code_sha256"]
    ):
        raise RuntimeError("Frozen development source/config differs.")
    store = ManifestStore(config, paths)
    labels = config["datasets"]["donate"]["labels"]
    development = store.load("donate", "development")
    seed_order = config["training"]["seeds"]
    expected = set(itertools.product("ABC", seed_order, config["training"]["folds"]))
    seen, artifacts = set(), 0
    pooled, enes_pooled = defaultdict(list), defaultdict(list)
    epochs, folds, input_hashes = defaultdict(list), [], {}
    for run_id in matrix["completed"]:
        run = paths.output_root / "development" / run_id
        report = read_json(run / "completion.json")
        identity = report["identity"]
        recipe, seed, fold = (identity[k] for k in ("experiment", "seed", "fold"))
        key = (recipe, seed, fold)
        if key not in expected or key in seen or report["test_used"] is not False:
            raise RuntimeError("Duplicate, unexpected or test-contaminated development run.")
        if (
            identity["code_sha256"] != matrix["code_sha256"]
            or identity["config_sha256"] != object_sha256(config)
            or identity["initialization"] != config["model"]["registry"][recipe]
            or identity["purpose"] != "development"
            or report["status"] != "completed"
            or report["frozen_tensors_verified"] is not True
            or report["best_reload_maximum_probability_delta"] > 1e-6
            or report["trained_heads"] != config["model"]["registry"][recipe]["trained_heads"]
            or identity["m2d_source_commit"] != config["model"]["source_commit"]
            or report["run_id"] != run_id
            or read_json(run / "identity.json") != identity
            or read_json(run / "config.json") != config
        ):
            raise RuntimeError("Development identity/config differs.")
        seen.add(key)
        artifacts += checked_artifacts(run, report)
        input_hashes[run_id] = sha256_file(run / "completion.json")
        validation = read_json(run / "donate_validation.json")
        val_rows = store.load("donate", f"fold{fold}_val")
        predictions = check_predictions(validation["predictions"], val_rows, labels)
        metrics = metrics_from_predictions(predictions, labels)
        if (
            metrics != validation["metrics"]
            or metrics["macro_f1"] != report["best_donate_macro_f1"]
        ):
            raise RuntimeError("Recorded and recomputed validation metrics differ.")
        history = read_json(run / "history.json")
        train_count = len(store.load("donate", f"fold{fold}_train"))
        total = report["epochs_executed"]
        if (
            len(history["history"]) != total
            or history["epoch"] != total
            or history["global_step"] != report["updates_executed"]
            or history["best_epoch"] != report["best_epoch"]
            or not 1 <= report["best_epoch"] <= total
            or any(e["total"] != train_count * total for e in history["exposures"].values())
            or set(history["exposures"]) != set(report["trained_heads"])
            or any(count != total for count in history["exposures"]["donate"]["samples"].values())
            or len(history["exposures"]["donate"]["samples"]) != train_count
        ):
            raise RuntimeError("Development epoch/update/exposure audit failed.")
        pooled[(recipe, seed)].extend(predictions)
        epochs[recipe].append(report["best_epoch"])
        folds.append(
            {
                "recipe": recipe,
                "seed": seed,
                "fold": fold,
                "metrics": metrics,
                "best_epoch": report["best_epoch"],
                "epochs_executed": total,
                "updates_executed": report["updates_executed"],
                "compute_seconds": history["compute_seconds"],
                "validation_seconds": history["validation_seconds"],
                "checkpoint_seconds": history["checkpoint_seconds"],
                "exposures": {
                    d: {
                        "total": e["total"],
                        "distinct_samples": len(e["samples"]),
                        "distinct_babies": len(e["babies"]),
                        "distinct_bouts": len(e["bouts"]),
                    }
                    for d, e in history["exposures"].items()
                },
            }
        )
        if recipe == "C":
            enes = read_json(run / "enes_validation.json")
            rows = check_predictions(
                enes["predictions"],
                store.load("enes", f"fold{fold}_val"),
                config["datasets"]["enes"]["labels"],
            )
            enes_pooled[seed].extend(rows)
    if (
        seen != expected
        or len(list((paths.output_root / "development").glob("*/completion.json"))) != 27
    ):
        raise RuntimeError("The exact 27 completed runs are required.")
    seed_predictions = {
        r: [check_predictions(pooled[(r, seed)], development, labels) for seed in seed_order]
        for r in "ABC"
    }
    seed_metrics = {
        r: [metrics_from_predictions(rows, labels) for rows in runs]
        for r, runs in seed_predictions.items()
    }
    decision = choose_recipe(seed_metrics, config)
    majority = []
    for fold in config["training"]["folds"]:
        count = Counter(int(r["label_index"]) for r in store.load("donate", f"fold{fold}_train"))
        predicted = max(range(len(labels)), key=lambda i: count[i])
        majority.extend(
            {"true_index": int(r["label_index"]), "predicted_index": predicted}
            for r in store.load("donate", f"fold{fold}_val")
        )
    comparison = {
        "development_code_sha256": matrix["code_sha256"],
        "config_sha256": object_sha256(config),
        "completion_sha256": input_hashes,
        "artifact_hashes_verified": artifacts,
        "seeds": seed_order,
        "folds": folds,
        "seed_metrics": seed_metrics,
        "decision": decision,
        "majority_train_fold_baseline": metrics_from_predictions(majority, labels),
        "label_recording_group_counts": {
            label: len({r["group_id"] for r in development if r["label"] == label})
            for label in labels
        },
        "bootstrap": paired_group_bootstrap(
            seed_predictions,
            labels,
            repetitions=config["training"]["bootstrap_repetitions"],
            seed=config["selection"]["final_seed"],
        ),
        "enes_seed_metrics": {
            str(seed): grouped_metrics(
                check_predictions(
                    rows, store.load("enes", "development"), config["datasets"]["enes"]["labels"]
                ),
                config["datasets"]["enes"]["labels"],
            )
            for seed, rows in enes_pooled.items()
        },
        "test_used": False,
        "label_truth": "caregiver-provided labels, not clinical causes",
    }
    post = paths.output_root / "post_training"
    freeze_json(post / "comparison.json", comparison)
    recipes = [decision["selected"], decision["baseline"]]
    plan = {
        "schema_version": 1,
        "comparison_sha256": sha256_file(post / "comparison.json"),
        "config_sha256": object_sha256(config),
        "selected": recipes[0],
        "baseline": recipes[1],
        "decision": decision,
        "seed": config["selection"]["final_seed"],
        "final_runs": [
            {
                "recipe": r,
                "epochs": int(statistics.median(epochs[r])),
                "development_selected_epochs": epochs[r],
            }
            for r in recipes
        ],
        "evaluation": [{"recipe": r, "dataset": "donate"} for r in recipes]
        + ([{"recipe": "C", "dataset": "enes"}] if recipes[0] == "C" else []),
        "selection_test_used": False,
        "calibration_status": "NOT_VALIDATED",
        "release_ready": False,
    }
    return freeze_json(post / "selection_plan.json", plan)


def grouped_metrics(rows, labels):
    result = {"clip": metrics_from_predictions(rows, labels)}
    for field in ("baby_id", "bout_id"):
        groups = defaultdict(list)
        for row in rows:
            groups[row[field]].append(row)
        metrics = {g: metrics_from_predictions(rs, labels) for g, rs in groups.items()}
        result[field] = {
            "groups": len(groups),
            "per_group": metrics,
            "equal_group_mean_macro_f1": statistics.mean(m["macro_f1"] for m in metrics.values()),
        }
    return result
