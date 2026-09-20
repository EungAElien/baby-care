"""Isolated, resumable balanced v2 workflow with immutable execution snapshots."""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import torch

from .balanced_checks import resume_check, sanity_check
from .balanced_data import VARIANTS, DevelopmentStore, validate_development, variant_config
from .balanced_report import compare_completed
from .data import RuntimePaths, feature_integrity
from .model import verify_m2d_source
from .runtime import RuntimeGuard, training_locks
from .training import run_fold, runtime_versions
from .util import (
    atomic_json,
    free_bytes,
    git_commit,
    object_sha256,
    read_json,
    sha256_file,
    utc_now,
)


def fingerprint(base_path, protocol_path):
    files = {
        f"m2d_supervised/{p.name}": sha256_file(p)
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    files.update(
        {
            "config/experiment_v1.json": sha256_file(base_path),
            "config/balanced_v2.json": sha256_file(protocol_path),
        }
    )
    return {
        "code_sha256": object_sha256(files),
        "files": files,
        "runtime_versions": runtime_versions(),
    }


def load_protocol(base_path, protocol_path):
    base, protocol = read_json(base_path), read_json(protocol_path)
    if sha256_file(base_path) != protocol["base_config_sha256"]:
        raise ValueError("The pinned v1 data/model/preprocessing configuration changed.")
    if (
        protocol["variants"] != list(VARIANTS)
        or protocol["seeds"] != [42, 43, 44]
        or protocol["folds"] != [0, 1, 2]
    ):
        raise ValueError("Use the predeclared v2 development matrix.")
    if (
        protocol["test_access"] != "FORBIDDEN"
        or protocol["final_retraining"]
        or protocol["service_export"]
    ):
        raise ValueError(
            "The v2 task does not authorize test evaluation, final retraining or export."
        )
    return base, protocol


def pin_json(path, value):
    if path.exists() and read_json(path) != value:
        raise RuntimeError(f"Pinned experiment record changed: {path.name}")
    if not path.exists():
        atomic_json(path, value)


def verify_gate(path, identity):
    if not path.exists():
        return None
    report = read_json(path)
    if report["identity"] != identity or report["status"] != "passed":
        raise RuntimeError(
            "An existing prerequisite failed or has different provenance; do not silently retry."
        )
    for name, expected in report["artifact_sha256"].items():
        if sha256_file(path.parent / name) != expected:
            raise RuntimeError("A prerequisite artifact changed.")
    return report


def completed_workflow(paths, identity):
    path = paths.output_root / "completion.json"
    if not path.exists():
        return None
    report = read_json(path)
    if report["identity"] != identity or report["status"] != "completed":
        raise RuntimeError("Completed v2 workflow identity changed.")
    summary = paths.output_root / "comparison.json"
    if sha256_file(summary) != report["comparison_sha256"]:
        raise RuntimeError("Completed v2 comparison changed.")
    for relative, expected in read_json(summary)["artifact_sha256"].items():
        completion_path = paths.output_root / relative
        if sha256_file(completion_path) != expected:
            raise RuntimeError("Completed v2 run record changed.")
        for name, digest in read_json(completion_path)["artifact_sha256"].items():
            if sha256_file(completion_path.parent / name) != digest:
                raise RuntimeError("Completed v2 run artifact changed.")
    return report


def execute(base_path, protocol_path, paths):
    base, protocol = load_protocol(base_path, protocol_path)
    identity = fingerprint(base_path, protocol_path)
    status_path = paths.output_root / "state.json"
    guard = RuntimeGuard(paths, protocol["training"]["minimum_free_gib"])
    started = time.monotonic()
    report = {
        "status": "preflight",
        "identity": identity,
        "pid": os.getpid(),
        "started_utc": utc_now(),
        "completed": [],
        "completed_runs": 0,
        "total_runs": 27,
        "test_used": False,
        "enes_used": False,
        "release_ready": False,
    }
    handlers = {s: signal.signal(s, guard.signal_stop) for s in (signal.SIGINT, signal.SIGTERM)}

    def publish(current=None):
        if current is not None:
            report["current"] = current
        report.update(updated_utc=utc_now(), session_elapsed_seconds=time.monotonic() - started)
        atomic_json(status_path, report)

    journal_authorized = False
    try:
        with training_locks(paths):
            if status_path.exists():
                previous = read_json(status_path)
                if previous["identity"] != identity:
                    raise RuntimeError("The running/resumable v2 code or dependencies changed.")
                report["started_utc"] = previous["started_utc"]
            complete = completed_workflow(paths, identity)
            if complete is not None:
                return complete
            journal_authorized = True
            guard.check()
            torch.set_num_threads(1)
            if not torch.backends.mps.is_available():
                raise RuntimeError("MPS is required for this local experiment.")
            if runtime_versions() != {"torch": "2.14.0", "numpy": "2.4.6", "timm": "1.0.29"}:
                raise RuntimeError("Dependencies differ from the pinned training environment.")
            pin_json(paths.output_root / "protocol.json", protocol)
            store = DevelopmentStore(base, paths)
            split_report = validate_development(store)
            source = verify_m2d_source(paths.m2d_source_root, base["model"]["source_commit"])
            if sha256_file(paths.checkpoint(base, "B")) != base["model"]["registry"]["B"]["sha256"]:
                raise RuntimeError("Initial B encoder hash changed.")
            publish()
            features = feature_integrity(paths, base, "donate", store.load("donate", "development"))
            atomic_json(
                paths.output_root / "preflight.json",
                {
                    "status": "passed",
                    "identity": identity,
                    "split": split_report,
                    "features": features,
                    "source": source,
                    "test_used": False,
                    "free_gib": free_bytes(paths.output_root) / 1024**3,
                    "checked_utc": utc_now(),
                },
            )
            report["status"] = "sanity"
            publish()
            sanity = verify_gate(paths.output_root / "sanity/report.json", identity)
            if sanity is None:
                sanity = sanity_check(base, protocol, paths, identity, guard)
            report["sanity"] = {
                key: sanity[key] for key in ("status", "accuracy", "updates", "cross_entropy")
            }
            report["status"] = "mps_resume_pilot"
            publish()
            pilot = verify_gate(paths.output_root / "pilot/report.json", identity)
            if pilot is None:
                pilot = resume_check(base, protocol, paths, identity, guard)
            report["pilot"] = {
                "status": pilot["status"],
                "exact_weight_match": pilot["exact_weight_match"],
            }
            report["status"] = "running"
            publish()
            for seed in protocol["seeds"]:
                for fold in protocol["folds"]:
                    for variant in VARIANTS:
                        guard.check()
                        config = variant_config(base, protocol, variant)
                        config_path = paths.output_root / "configs" / f"{variant}.json"
                        pin_json(config_path, config)
                        variant_paths = replace(
                            paths, output_root=paths.output_root / "arms" / variant
                        )
                        before = time.monotonic()

                        def progress(value, variant=variant):
                            publish({"variant": variant, **value})

                        result = run_fold(
                            config,
                            config_path,
                            variant_paths,
                            "B",
                            fold,
                            seed,
                            interrupt=guard.check,
                            progress=progress,
                        )
                        if result["status"] != "completed":
                            report.update(status=result["status"], reason=result.get("reason"))
                            publish()
                            return report
                        report["completed"].append(
                            {
                                "variant": variant,
                                "seed": seed,
                                "fold": fold,
                                "run_id": result["run_id"],
                                "wall_seconds_this_session": time.monotonic() - before,
                            }
                        )
                        report["completed_runs"] = len(report["completed"])
                        publish()
            report["status"] = "comparing"
            publish()
            compare_completed(base, protocol, paths, report["completed"])
            report.update(
                status="completed",
                completed_utc=utc_now(),
                comparison_sha256=sha256_file(paths.output_root / "comparison.json"),
            )
            publish()
            atomic_json(paths.output_root / "completion.json", report)
            return report
    except InterruptedError as error:
        if journal_authorized:
            report.update(status="paused", reason=str(error))
            publish()
        return report
    except Exception as error:
        if journal_authorized:
            report.update(status="failed", reason=f"{type(error).__name__}: {error}")
            publish()
        raise
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def launch(base_path, protocol_path, paths):
    base, protocol = load_protocol(base_path, protocol_path)
    del base
    identity = fingerprint(base_path, protocol_path)
    with (paths.output_root / ".launcher.lock").open("a") as launcher_lock:
        fcntl.flock(launcher_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with training_locks(paths):
            complete = completed_workflow(paths, identity)
            if complete is not None:
                return complete
            RuntimeGuard(paths, protocol["training"]["minimum_free_gib"]).check()
            previous_launch = paths.output_root / "launch.json"
            if previous_launch.exists():
                previous = read_json(previous_launch)
                command = subprocess.run(
                    ["ps", "-p", str(previous["pid"]), "-o", "command="],
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout
                if "m2d_supervised.balanced_runtime" in command:
                    raise RuntimeError("The previous balanced workflow is still alive.")
            snapshot = paths.output_root / "code" / identity["code_sha256"]
            sources = {f"m2d_supervised/{p.name}": p for p in Path(__file__).parent.glob("*.py")}
            sources.update(
                {"config/experiment_v1.json": base_path, "config/balanced_v2.json": protocol_path}
            )
            for relative, source in sources.items():
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copy2(source, target)
                if sha256_file(target) != identity["files"][relative]:
                    raise RuntimeError("Execution snapshot hash mismatch.")
        command = [
            "/usr/bin/caffeinate",
            "-i",
            "-s",
            sys.executable,
            "-u",
            "-m",
            "m2d_supervised.balanced_runtime",
            "run",
            "--base-config",
            str(snapshot / "config/experiment_v1.json"),
            "--config",
            str(snapshot / "config/balanced_v2.json"),
            "--research-root",
            str(paths.research_root),
            "--output-root",
            str(paths.output_root),
            "--m2d-source-root",
            str(paths.m2d_source_root),
            "--repository-root",
            str(paths.repository_root),
        ]
        environment = dict(os.environ, PYTHONPATH=str(snapshot), PYTHONUNBUFFERED="1")
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            environment[name] = "1"
        with (paths.output_root / "training.log").open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=snapshot,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        record = {
            "pid": process.pid,
            "launched_utc": utc_now(),
            "identity": identity,
            "command": command,
            "repository_commit": git_commit(paths.repository_root),
        }
        atomic_json(previous_launch, record)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("launch", "run"))
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    for name in ("research-root", "output-root", "m2d-source-root", "repository-root"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    paths = RuntimePaths(
        args.research_root.resolve(),
        args.output_root.resolve(),
        args.m2d_source_root.resolve(),
        args.repository_root.resolve(),
    )
    if paths.output_root != paths.research_root / "runs/supervised_v2_balanced":
        parser.error("Use the isolated research runs/supervised_v2_balanced directory.")
    paths.output_root.mkdir(parents=True, exist_ok=True)
    result = (launch if args.command == "launch" else execute)(
        args.base_config.resolve(), args.config.resolve(), paths
    )
    print(
        {
            key: result[key]
            for key in ("status", "pid", "completed_runs", "reason")
            if key in result
        },
        flush=True,
    )
    if result.get("status") in {"paused", "failed"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
