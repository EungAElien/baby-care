"""Paired development-only v2 comparisons; never chooses a service model."""

from __future__ import annotations

import statistics

import numpy as np

from .balanced_data import VARIANTS, DevelopmentStore
from .engine import metrics_from_predictions
from .util import atomic_json, read_json, sha256_file, utc_now


def macro_from_matrix(matrix):
    true_positive = np.diagonal(matrix, axis1=-2, axis2=-1)
    denominator = matrix.sum(axis=-1) + matrix.sum(axis=-2)
    return np.divide(
        2 * true_positive,
        denominator,
        out=np.zeros_like(true_positive, dtype=float),
        where=denominator > 0,
    ).mean(axis=-1)


def paired_bootstrap(first, second, labels, repetitions, seed):
    if set(first) != set(second) or not first:
        raise ValueError("Paired predictions have different seeds.")
    groups = sorted({row["group_id"] for row in first[next(iter(first))]})
    group_index = {group: i for i, group in enumerate(groups)}
    counts = []
    for predictions in (first, second):
        seed_counts = []
        for run_seed in sorted(first):
            anchor = {r["sample_id"]: (r["group_id"], r["true_index"]) for r in first[run_seed]}
            observed = {
                r["sample_id"]: (r["group_id"], r["true_index"]) for r in predictions[run_seed]
            }
            if anchor != observed or len(observed) != len(predictions[run_seed]):
                raise ValueError("Unpaired or duplicate OOF predictions.")
            matrix = np.zeros((len(groups), len(labels), len(labels)), dtype=np.int64)
            for row in predictions[run_seed]:
                matrix[group_index[row["group_id"]], row["true_index"], row["predicted_index"]] += 1
            seed_counts.append(matrix)
        counts.append(np.stack(seed_counts))
    rng = np.random.default_rng(seed)
    draws = rng.multinomial(len(groups), np.full(len(groups), 1 / len(groups)), size=repetitions)
    differences = macro_from_matrix(np.einsum("rg,sgij->rsij", draws, counts[0])).mean(
        axis=1
    ) - macro_from_matrix(np.einsum("rg,sgij->rsij", draws, counts[1])).mean(axis=1)
    return {
        "mean_difference": float(
            macro_from_matrix(counts[0].sum(axis=1)).mean()
            - macro_from_matrix(counts[1].sum(axis=1)).mean()
        ),
        "percentile_95_interval": np.quantile(differences, [0.025, 0.975]).tolist(),
        "groups": len(groups),
        "repetitions": repetitions,
        "seed": seed,
        "interpretation": (
            "Exploratory paired source-group uncertainty conditional on fixed seeds; "
            "validation also selected epochs, not independent test performance."
        ),
    }


def compare_completed(base, protocol, paths, completed):
    planned = {(v, s, f) for v in VARIANTS for s in protocol["seeds"] for f in protocol["folds"]}
    keys = [(item["variant"], item["seed"], item["fold"]) for item in completed]
    if set(keys) != planned or len(keys) != len(planned):
        raise ValueError("The exact v2 development matrix is not complete.")
    development = DevelopmentStore(base, paths).load("donate", "development")
    anchor = {row["sample_id"]: (row["group_id"], int(row["label_index"])) for row in development}
    labels = base["datasets"]["donate"]["labels"]
    pooled = {variant: {seed: [] for seed in protocol["seeds"]} for variant in VARIANTS}
    runs = {variant: [] for variant in VARIANTS}
    artifacts = {}
    for item in completed:
        directory = paths.output_root / "arms" / item["variant"] / "development" / item["run_id"]
        record = read_json(directory / "completion.json")
        if record["status"] != "completed" or record["test_used"]:
            raise ValueError("Invalid completed development record.")
        for name, expected in record["artifact_sha256"].items():
            if sha256_file(directory / name) != expected:
                raise ValueError("A completed v2 artifact changed.")
        artifacts[str(directory.relative_to(paths.output_root) / "completion.json")] = sha256_file(
            directory / "completion.json"
        )
        saved = read_json(directory / "donate_validation.json")
        pooled[item["variant"]][item["seed"]].extend(saved["predictions"])
        history = read_json(directory / "history.json")
        runs[item["variant"]].append(
            {
                **item,
                "best_epoch": record["best_epoch"],
                "epochs_executed": record["epochs_executed"],
                "updates_executed": record["updates_executed"],
                "compute_seconds": record["compute_seconds"],
                "selected_encoder_finetuned": item["variant"] == "balanced_finetune"
                and record["best_epoch"] > protocol["training"]["warmup_epochs"],
                "train_metrics": read_json(directory / "donate_train_diagnostic.json")["metrics"],
                "exposures": {
                    key: value
                    for key, value in history["exposures"]["donate"].items()
                    if key in {"total", "labels"}
                },
                "unique_training_samples_exposed": len(history["exposures"]["donate"]["samples"]),
                "unique_training_groups_exposed": len(
                    history["exposures"]["donate"].get("groups", {})
                ),
            }
        )
    variants = {}
    for variant, seeds in pooled.items():
        for predictions in seeds.values():
            found = {r["sample_id"]: (r["group_id"], r["true_index"]) for r in predictions}
            if found != anchor or len(predictions) != len(anchor):
                raise ValueError("OOF predictions do not cover development exactly once.")
        scored = {str(seed): metrics_from_predictions(rows, labels) for seed, rows in seeds.items()}
        values = [metrics["macro_f1"] for metrics in scored.values()]
        variants[variant] = {
            "mean_macro_f1": statistics.mean(values),
            "seed_sample_std": statistics.stdev(values),
            "by_seed": scored,
            "mean_per_class": {
                label: {
                    key: statistics.mean(m["per_class"][label][key] for m in scored.values())
                    for key in ("precision", "recall", "f1")
                }
                for label in labels
            },
            "runs": runs[variant],
        }
    comparisons = {}
    for first, second in (
        ("balanced_head", "natural_head"),
        ("balanced_finetune", "balanced_head"),
    ):
        comparisons[f"{first}_minus_{second}"] = paired_bootstrap(
            pooled[first],
            pooled[second],
            labels,
            protocol["bootstrap_repetitions"],
            protocol["bootstrap_seed"],
        )
    report = {
        "status": "completed_development_comparison",
        "completed_utc": utc_now(),
        "development_count": len(development),
        "variants": variants,
        "comparisons": comparisons,
        "test_used": False,
        "enes_used": False,
        "release_ready": False,
        "calibration_status": "NOT_VALIDATED",
        "artifact_sha256": artifacts,
        "service_model_selected": False,
    }
    atomic_json(paths.output_root / "comparison.json", report)
    return report
