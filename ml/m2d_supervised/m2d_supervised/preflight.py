"""Read-only data/model checks required before any supervised optimizer step."""

from __future__ import annotations

import fcntl
import gc
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

import torch

from .data import ManifestStore, RuntimePaths, feature_integrity, resolve_portable
from .model import load_encoder, verify_m2d_source
from .util import (
    ac_power,
    atomic_json,
    code_hash,
    free_bytes,
    git_commit,
    object_sha256,
    read_json,
    require_free_space,
    sha256_file,
    utc_now,
)


def _sets(rows: list[dict[str, str]], key: str) -> set[str]:
    return {row[key] for row in rows}


def _planned_development_runs(config: dict[str, Any]) -> list[dict[str, int | str]]:
    experiments = config["training"]["execution_order"]
    if experiments != ["B", "C", "A"]:
        raise RuntimeError("Development execution order must keep paired B/C before A.")
    registry = config["model"]["registry"]
    if set(experiments) != set(registry):
        raise RuntimeError("Execution order and model registry differ.")
    runs = [
        {
            "sequence": sequence,
            "run_id": f"{experiment}-s{seed}-f{fold}",
            "experiment": experiment,
            "seed": seed,
            "fold": fold,
        }
        for sequence, (seed, fold, experiment) in enumerate(
            (
                (seed, fold, experiment)
                for seed in config["training"]["seeds"]
                for fold in config["training"]["folds"]
                for experiment in experiments
            ),
            start=1,
        )
    ]
    if len(runs) != 27 or len({run["run_id"] for run in runs}) != 27:
        raise RuntimeError("The frozen development matrix must contain 27 unique runs.")
    return runs


def _validate_dataset_splits(
    dataset: str,
    store: ManifestStore,
    config: dict[str, Any],
) -> dict[str, Any]:
    all_rows = store.load(dataset, "all")
    development = store.load(dataset, "development")
    test = store.load(dataset, "test")
    expected = config["datasets"][dataset]["counts"]
    observed_counts = {
        "all": len(all_rows),
        "development": len(development),
        "test": len(test),
    }
    if observed_counts != expected:
        raise RuntimeError(f"Frozen {dataset} counts changed: {observed_counts}")
    all_ids = _sets(all_rows, "sample_id")
    development_ids = _sets(development, "sample_id")
    test_ids = _sets(test, "sample_id")
    if development_ids & test_ids or development_ids | test_ids != all_ids:
        raise RuntimeError(f"{dataset} development/test sample partition changed.")
    group_field = config["datasets"][dataset]["group_field"]
    if _sets(development, group_field) & _sets(test, group_field):
        raise RuntimeError(f"{dataset} group leakage between development and test.")

    validation_coverage: Counter[str] = Counter()
    folds: dict[str, Any] = {}
    for fold in config["training"]["folds"]:
        train = store.load(dataset, f"fold{fold}_train")
        validation = store.load(dataset, f"fold{fold}_val")
        train_ids = _sets(train, "sample_id")
        validation_ids = _sets(validation, "sample_id")
        if train_ids & validation_ids or train_ids | validation_ids != development_ids:
            raise RuntimeError(f"{dataset} fold {fold} is not a partition of development.")
        if _sets(train, group_field) & _sets(validation, group_field):
            raise RuntimeError(f"{dataset} fold {fold} leaks {group_field}.")
        validation_coverage.update(validation_ids)
        fold_record: dict[str, Any] = {
            "train_files": len(train),
            "validation_files": len(validation),
            "train_groups": len(_sets(train, group_field)),
            "validation_groups": len(_sets(validation, group_field)),
            "train_labels": dict(Counter(row["label"] for row in train)),
            "validation_labels": dict(Counter(row["label"] for row in validation)),
        }
        if dataset == "enes":
            if _sets(train, "bout_id") & _sets(validation, "bout_id"):
                raise RuntimeError(f"Enes fold {fold} leaks recording bouts.")
            fold_record.update(
                train_babies=len(_sets(train, "baby_id")),
                validation_babies=len(_sets(validation, "baby_id")),
                train_bouts=len(_sets(train, "bout_id")),
                validation_bouts=len(_sets(validation, "bout_id")),
            )
        folds[str(fold)] = fold_record
    if set(validation_coverage) != development_ids or set(validation_coverage.values()) != {1}:
        raise RuntimeError(f"{dataset} folds do not provide exactly-once validation coverage.")

    return {
        "counts": observed_counts,
        "all_groups": len(_sets(all_rows, group_field)),
        "development_groups": len(_sets(development, group_field)),
        "test_groups": len(_sets(test, group_field)),
        "label_counts": dict(Counter(row["label"] for row in all_rows)),
        "folds": folds,
        "exactly_once_development_validation_coverage": True,
        "group_leakage": 0,
    }


def _validate_preprocessing(paths: RuntimePaths, config: dict[str, Any]) -> dict[str, Any]:
    expected = config["preprocessing"]
    babycry = read_json(
        resolve_portable(paths.research_root, "data/babycry_m2d/preprocessing.json")
    )
    fft = babycry["fft"]
    pairs = {
        "sample_rate": fft["sr"],
        "n_fft": fft["n_fft"],
        "win_length": fft["win_length"],
        "hop_length": fft["hop_length"],
        "n_mels": fft["n_mels"],
        "f_min": fft["fmin"],
        "f_max": fft["fmax"],
        "center": fft["center"],
        "power": fft["power"],
        "window": fft["window"],
        "pad_mode": fft["pad_mode"],
        "htk": fft["htk"],
        "mel_norm": fft["norm"],
    }
    mismatches = {
        key: {"expected": expected[key], "observed": value}
        for key, value in pairs.items()
        if expected[key] != value
    }
    if mismatches:
        raise RuntimeError(f"Preprocessing differs from BABYCRY preparation: {mismatches}")
    for report_relative in (
        "학습데이터셋/Donate-a-Cry/preparation_report.json",
        "reports/stage5_dual_preparation.json",
    ):
        report_path = resolve_portable(paths.research_root, report_relative)
        report = read_json(report_path)
        observed = report.get("preprocessing")
        if observed is not None:
            for config_key, report_key in (
                ("sample_rate", "sr"),
                ("n_fft", "n_fft"),
                ("win_length", "win_length"),
                ("hop_length", "hop_length"),
                ("n_mels", "n_mels"),
                ("f_min", "fmin"),
                ("f_max", "fmax"),
                ("center", "center"),
                ("power", "power"),
                ("window", "window"),
                ("pad_mode", "pad_mode"),
                ("htk", "htk"),
                ("mel_norm", "norm"),
            ):
                if expected[config_key] != observed[report_key]:
                    raise RuntimeError(f"Preprocessing differs in {report_relative}: {config_key}")
    return {
        "matched": True,
        "storage": "unnormalized float32 natural-log Mel arrays",
        "model_normalization": [
            expected["normalization_mean"],
            expected["normalization_std"],
        ],
        "short_input_policy": "floor to a 16-frame multiple; no padding or repetition",
    }


def _babycry_hashes(paths: RuntimePaths, config: dict[str, Any]) -> tuple[dict[str, set[str]], int]:
    entry = config["datasets"]["babycry_pretraining"]
    root = resolve_portable(paths.research_root, entry["root"])
    database = root / entry["progress_database"]
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    hashes = {"spec": set(), "pcm": set(), "source": set()}
    ok_records = 0
    try:
        for (payload,) in connection.execute("SELECT record FROM files"):
            record = json.loads(payload)
            if record.get("status") != "ok":
                continue
            ok_records += 1
            hashes["spec"].add(record["spec_sha256"])
            hashes["pcm"].add(record["pcm_sha256"])
            hashes["source"].add(record["source_id"])
    finally:
        connection.close()
    return hashes, ok_records


def _cross_dataset_duplicates(
    paths: RuntimePaths,
    config: dict[str, Any],
    store: ManifestStore,
) -> dict[str, Any]:
    rows = {dataset: store.load(dataset, "all") for dataset in ("donate", "enes")}
    supervised = {
        "donate": {
            "spec": {row["spec_sha256"] for row in rows["donate"]},
            "pcm": {row["selected_pcm_sha256"] for row in rows["donate"]},
            "source": {row["selected_file_sha256"] for row in rows["donate"]},
        },
        "enes": {
            "spec": {row["spec_sha256"] for row in rows["enes"]},
            "pcm": {row["canonical_pcm_sha256"] for row in rows["enes"]},
            "source": {row["source_file_sha256"] for row in rows["enes"]},
        },
    }
    babycry, babycry_ok_records = _babycry_hashes(paths, config)
    report: dict[str, Any] = {
        "babycry_ok_records": babycry_ok_records,
        "babycry_unique_hashes": {name: len(values) for name, values in babycry.items()},
        "checks": {},
        "transformed_or_cropped_identity_check": "NOT_PERFORMED",
        "identity_separation": (
            "NOT_VERIFIED; source and exact-content hashes are not infant identity"
        ),
    }
    for dataset in ("donate", "enes"):
        report["checks"][f"{dataset}_vs_babycry"] = {
            key: len(supervised[dataset][key] & babycry[key]) for key in ("spec", "pcm", "source")
        }
    report["checks"]["donate_vs_enes"] = {
        key: len(supervised["donate"][key] & supervised["enes"][key])
        for key in ("spec", "pcm", "source")
    }
    if any(value for values in report["checks"].values() for value in values.values()):
        raise RuntimeError(f"Exact cross-dataset duplicate detected: {report['checks']}")
    return report


def _validate_checkpoints(paths: RuntimePaths, config: dict[str, Any]) -> dict[str, Any]:
    registry = config["model"]["registry"]
    expected_norm_stats = (
        float(config["preprocessing"]["normalization_mean"]),
        float(config["preprocessing"]["normalization_std"]),
    )
    observed: dict[str, Any] = {}
    for experiment in ("A", "B"):
        entry = registry[experiment]
        checkpoint = paths.checkpoint(config, experiment)
        digest = sha256_file(checkpoint)
        if digest != entry["sha256"]:
            raise RuntimeError(f"Checkpoint {experiment} hash changed.")
        encoder, norm_stats_origin = load_encoder(
            paths.m2d_source_root,
            checkpoint,
            expected_sha256=entry["sha256"],
            expected_norm_stats=expected_norm_stats,
            expected_norm_stats_origin=entry["norm_stats_origin"],
        )
        observed[experiment] = {
            "initialization_id": entry["initialization_id"],
            "sha256": digest,
            "bytes": checkpoint.stat().st_size,
            "online_encoder_keys": len(encoder.state_dict()),
            "online_encoder_parameters": sum(
                value.numel() for value in encoder.state_dict().values()
            ),
            "normalization_stats": list(expected_norm_stats),
            "normalization_stats_origin": norm_stats_origin,
            "required_weights_randomly_substituted": False,
        }
        del encoder
        gc.collect()

    stage4_report = read_json(
        resolve_portable(
            paths.research_root,
            config["datasets"]["babycry_pretraining"]["stage4_completion_report"],
        )
    )
    required = {
        "status": "completed",
        "verified": True,
        "ready_for_supervised_stage": True,
        "final_artifact_verification_pending": False,
    }
    if any(stage4_report.get(key) != value for key, value in required.items()):
        raise RuntimeError("BABYCRY SSL completion evidence is not valid.")
    if stage4_report.get("config", {}).get("initial_checkpoint_sha256") != registry["A"]["sha256"]:
        raise RuntimeError("BABYCRY SSL provenance does not reference the pinned M2D checkpoint.")
    if stage4_report.get("best_files_hashes_verified") is not True:
        raise RuntimeError("BABYCRY SSL best-file verification is missing.")
    return {
        "registry": observed,
        "stage4_completion_verified": True,
        "stage4_selected_epoch": stage4_report["progress"]["best_epoch"],
        "stage4_downstream_cause_performance_claimed": False,
    }


def _lock_availability(paths: RuntimePaths) -> dict[str, bool]:
    paths.output_root.mkdir(parents=True, exist_ok=True)
    candidates = {
        "legacy_supervised": paths.research_root / ".stage5_training.lock",
        "babycry_ssl": paths.research_root / "runs/babycry_full/.training.lock",
        "supervised_v1": paths.output_root / ".mps-training.lock",
    }
    result: dict[str, bool] = {}
    handles = []
    try:
        for name, path in candidates.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a")
            handles.append(handle)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result[name] = True
            except BlockingIOError:
                result[name] = False
    finally:
        for handle in handles:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()
    if not all(result.values()):
        raise RuntimeError(f"Another MPS training process owns a common lock: {result}")
    return result


def run_preflight(
    config: dict[str, Any],
    config_path: Path,
    paths: RuntimePaths,
    *,
    full_feature_check: bool = True,
) -> dict[str, Any]:
    started = time.monotonic()
    paths.output_root.mkdir(parents=True, exist_ok=True)
    package_root = Path(__file__).resolve().parent
    current_code_hash, code_files = code_hash(package_root, config_path)
    report: dict[str, Any] = {
        "schema_version": 1,
        "checked_utc": utc_now(),
        "experiment_version": config["experiment_version"],
        "scope": "PREPARATION_ONLY",
        "status": "checking",
        "ready_for_training": False,
        "trainer_implemented": False,
        "optimizer_step_executed": False,
        "training_or_evaluation_executed": False,
        "test_used_for_model_fitting_or_selection": False,
        "config_sha256": sha256_file(config_path),
        "canonical_config_sha256": object_sha256(config),
        "code_sha256": current_code_hash,
        "code_files": code_files,
        "repository_commit": git_commit(paths.repository_root),
        "rights": config["rights"],
    }
    try:
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS is not available on this machine.")
        if not ac_power():
            raise RuntimeError("Connect AC power before MPS training.")
        required_temporary = 768 * 1024**2
        available = require_free_space(
            paths.output_root,
            float(config["training"]["minimum_free_gib"]),
            required_temporary,
        )
        report["resources"] = {
            "mps_available": True,
            "ac_power": True,
            "free_bytes_before": available,
            "minimum_free_gib": config["training"]["minimum_free_gib"],
            "atomic_write_reserve_bytes": required_temporary,
        }
        report["locks_available"] = _lock_availability(paths)
        report["planned_development_runs"] = _planned_development_runs(config)
        report["m2d_source"] = verify_m2d_source(
            paths.m2d_source_root, config["model"]["source_commit"]
        )
        report["checkpoints"] = _validate_checkpoints(paths, config)
        report["preprocessing"] = _validate_preprocessing(paths, config)
        store = ManifestStore(config, paths)
        report["splits"] = {
            dataset: _validate_dataset_splits(dataset, store, config)
            for dataset in ("donate", "enes")
        }
        report["cross_dataset_duplicates"] = _cross_dataset_duplicates(paths, config, store)
        if full_feature_check:
            report["feature_integrity"] = {
                dataset: feature_integrity(paths, config, dataset, store.load(dataset, "all"))
                for dataset in ("donate", "enes")
            }
        else:
            report["feature_integrity"] = "SKIPPED_FOR_BOUNDED_TEST_ONLY"
        if full_feature_check:
            report["status"] = "passed"
            report["ready_for_training"] = True
        else:
            report["status"] = "passed_bounded_check_only"
    except Exception as error:
        report["status"] = "failed"
        report["blocking_error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["free_bytes_after"] = free_bytes(paths.output_root)
        atomic_json(paths.output_root / "preflight" / "preflight.json", report)
    return report
