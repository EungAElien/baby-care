"""Exclusive MPS execution, detached launch, resource guards and matrix progress."""

from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import torch

from .data import RuntimePaths
from .preflight import _planned_development_runs, run_preflight
from .training import run_fold, runtime_versions
from .util import (
    ac_power,
    atomic_json,
    code_hash,
    git_commit,
    read_json,
    require_free_space,
    sha256_file,
    utc_now,
)


@contextmanager
def training_locks(paths: RuntimePaths):
    candidates = [
        paths.research_root / ".stage5_training.lock",
        paths.research_root / "runs/babycry_full/.training.lock",
        paths.output_root / ".mps-training.lock",
    ]
    handles = []
    try:
        for path in candidates:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a")
            handles.append(handle)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(f"Another training process holds {path.name}.") from error
        yield
    finally:
        for handle in reversed(handles):
            handle.close()


class RuntimeGuard:
    def __init__(self, paths: RuntimePaths, minimum_free_gib: float):
        self.paths, self.minimum_free_gib = paths, minimum_free_gib
        self.stopping = False
        self.next_check = 0.0

    def signal_stop(self, signum: int, frame: Any) -> None:
        self.stopping = True

    def check(self) -> None:
        if self.stopping or (self.paths.output_root / "STOP_TRAINING").exists():
            raise InterruptedError("Stop requested; save and resume at the next complete update.")
        if time.monotonic() < self.next_check:
            return
        self.next_check = time.monotonic() + 5
        if not ac_power():
            raise InterruptedError("AC power disconnected.")
        try:
            require_free_space(self.paths.output_root, self.minimum_free_gib, 512 * 1024**2)
        except RuntimeError as error:
            raise InterruptedError(
                "Storage reserve reached; stopping before another update."
            ) from error


def train_matrix(config: dict[str, Any], config_path: Path, paths: RuntimePaths) -> dict[str, Any]:
    paths.output_root.mkdir(parents=True, exist_ok=True)
    status_path = paths.output_root / "matrix_progress.json"
    guard = RuntimeGuard(paths, config["training"]["minimum_free_gib"])
    digest, _ = code_hash(Path(__file__).parent, config_path)
    planned = _planned_development_runs(config)
    report: dict[str, Any] = {
        "status": "preflight",
        "pid": os.getpid(),
        "started_utc": utc_now(),
        "code_sha256": digest,
        "config_sha256": sha256_file(config_path),
        "total_runs": len(planned),
        "completed_runs": 0,
        "completed": [],
        "test_used": False,
        "after_matrix": "STOP_FOR_COMPARISON_AND_FINAL_RETRAIN_TASK",
    }
    if status_path.exists():
        previous = read_json(status_path)
        if (
            previous["code_sha256"] != digest
            or previous["config_sha256"] != report["config_sha256"]
        ):
            raise RuntimeError(
                "Matrix provenance changed; use a separately versioned output directory."
            )
    started = time.monotonic()
    lock_acquired = False
    old_handlers = {s: signal.signal(s, guard.signal_stop) for s in (signal.SIGTERM, signal.SIGINT)}

    def publish(current: dict[str, Any] | None = None) -> None:
        if current is not None:
            report["current"] = current
        report["updated_utc"] = utc_now()
        report["session_elapsed_seconds"] = time.monotonic() - started
        if "pilot_estimate" in report:
            fraction = report["completed_runs"] / report["total_runs"]
            report["estimated_remaining_seconds"] = {
                scenario: seconds * (1 - fraction)
                for scenario, seconds in report["pilot_estimate"].items()
            }
            report["remaining_estimate_method"] = "Pilot scenarios scaled by unfinished runs."
        atomic_json(status_path, report)

    try:
        with training_locks(paths):
            lock_acquired = True
            guard.check()
            torch.set_num_threads(1)
            publish()
            run_preflight(config, config_path, paths, locks_held=True)
            pilot = read_json(paths.output_root / "pilot" / "report.json")
            if pilot["status"] != "passed" or pilot["code_sha256"] != digest:
                raise RuntimeError("A passing MPS pilot for these exact source files is required.")
            if pilot["runtime_versions"] != runtime_versions():
                raise RuntimeError("Dependencies changed after the MPS pilot.")
            report["pilot_estimate"] = pilot["estimated_matrix_seconds"]
            report["status"] = "running"
            for item in planned:
                guard.check()
                result = run_fold(
                    config,
                    config_path,
                    paths,
                    item["experiment"],
                    item["fold"],
                    item["seed"],
                    interrupt=guard.check,
                    progress=publish,
                )
                if result["status"] != "completed":
                    report["status"] = result["status"]
                    publish(result)
                    return report
                report["completed"].append(result["run_id"])
                report["completed_runs"] += 1
                publish()
            report["status"] = "completed_development_training"
            report["completed_utc"] = utc_now()
            publish()
            return report
    except InterruptedError as error:
        report.update(status="paused", reason=str(error))
        publish()
        return report
    except Exception as error:
        report.update(status="failed", reason=f"{type(error).__name__}: {error}")
        if lock_acquired:
            publish()
        raise
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


def launch(config: dict[str, Any], config_path: Path, paths: RuntimePaths) -> dict[str, Any]:
    paths.output_root.mkdir(parents=True, exist_ok=True)
    with (paths.output_root / ".launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _launch(config, config_path, paths)


def _launch(config: dict[str, Any], config_path: Path, paths: RuntimePaths) -> dict[str, Any]:
    """Copy this package into a hashed snapshot and detach a single sequential worker."""
    source = Path(__file__).parent
    digest, _ = code_hash(source, config_path)
    with training_locks(paths):
        RuntimeGuard(paths, config["training"]["minimum_free_gib"]).check()
        launch_path = paths.output_root / "launch.json"
        if launch_path.exists():
            previous = read_json(launch_path)
            try:
                os.kill(previous["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError("The previous launcher process is still alive.")
        snapshot = paths.output_root / "code" / digest
        snapshot_package = snapshot / "m2d_supervised"
        snapshot_config = snapshot / "config" / "experiment_v1.json"
        snapshot_package.mkdir(parents=True, exist_ok=True)
        snapshot_config.parent.mkdir(parents=True, exist_ok=True)
        for path in source.glob("*.py"):
            target = snapshot_package / path.name
            if target.exists() and sha256_file(target) != sha256_file(path):
                raise RuntimeError("A previous code snapshot was changed.")
            if not target.exists():
                shutil.copy2(path, target)
        if not snapshot_config.exists():
            shutil.copy2(config_path, snapshot_config)
        if code_hash(snapshot_package, snapshot_config)[0] != digest:
            raise RuntimeError("Code snapshot does not match the tested package.")
        command = [
            "/usr/bin/caffeinate",
            "-i",
            "-s",
            sys.executable,
            "-u",
            "-m",
            "m2d_supervised",
            "train-matrix",
            "--research-root",
            str(paths.research_root),
            "--output-root",
            str(paths.output_root),
            "--m2d-source-root",
            str(paths.m2d_source_root),
            "--repository-root",
            str(paths.repository_root),
            "--config",
            str(snapshot_config),
        ]
        environment = dict(os.environ, PYTHONPATH=str(snapshot), PYTHONUNBUFFERED="1")
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            environment[name] = "1"
    # Child acquires the exclusive locks again before preflight and keeps them until exit.
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
        "code_sha256": digest,
        "repository_commit": git_commit(paths.repository_root),
        "command": command,
        "working_directory": str(snapshot),
        "log": str(paths.output_root / "training.log"),
    }
    atomic_json(launch_path, record)
    return record
