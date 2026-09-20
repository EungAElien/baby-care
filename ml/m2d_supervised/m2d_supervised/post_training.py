"""Locked post-training state machine, fixed retraining, sealed tests and local export."""

from __future__ import annotations

import fcntl
import gc
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import torch

from .checkpoint import state_digest
from .comparison import (
    checked_artifacts,
    compare_development,
    freeze_json,
    grouped_metrics,
    paired_group_bootstrap,
)
from .data import ManifestStore, RuntimePaths
from .engine import evaluate, metrics_from_predictions
from .exporting import export_selected, load_final, prepare_probes
from .final_training import final_run
from .runtime import RuntimeGuard, training_locks
from .sealed_test import evaluate_once
from .util import atomic_json, code_hash, git_commit, object_sha256, read_json, sha256_file, utc_now


def verify_post_completion(post):
    report = read_json(post / "completion.json")
    checked_artifacts(post, report)
    references = {"export/selected": report["export_completion_sha256"]}
    references.update({f"final/{r}": h for r, h in report["final_completion_sha256"].items()})
    references.update({f"test/{r}": h for r, h in report["test_completion_sha256"].items()})
    for relative, expected in references.items():
        directory = post / relative
        if sha256_file(directory / "completion.json") != expected:
            raise RuntimeError("Post-training completion chain changed.")
        checked_artifacts(directory, read_json(directory / "completion.json"))
    return report


def run_post_training(config, config_path, paths, *, resume=False):
    post = paths.output_root / "post_training"
    post.mkdir(parents=True, exist_ok=True)
    state_path = post / "state.json"
    digest = code_hash(Path(__file__).parent, config_path)[0]
    guard = RuntimeGuard(paths, config["training"]["minimum_free_gib"])
    report = {
        "status": "starting",
        "pid": os.getpid(),
        "started_utc": utc_now(),
        "code_sha256": digest,
        "completed_development_runs": 27,
        "test_used": False,
        "calibration_status": "NOT_VALIDATED",
        "release_ready": False,
    }
    started = time.monotonic()

    def publish(status, **extra):
        report.update(
            status=status,
            updated_utc=utc_now(),
            session_elapsed_seconds=time.monotonic() - started,
            **extra,
        )
        atomic_json(state_path, report)
        print(f"Post-training: {status}", flush=True)

    with training_locks(paths):
        if state_path.exists():
            prior = read_json(state_path)
            if prior["code_sha256"] != digest:
                raise RuntimeError("Use the original immutable post-training code to resume.")
            if prior["status"] in {"failed", "paused"} and not resume:
                raise RuntimeError(
                    "Stopped post-training requires incident review and explicit --resume."
                )
            if prior["status"] == "completed":
                verify_post_completion(post)
                return prior
            report["started_utc"] = prior["started_utc"]
            report["test_used"] = prior["test_used"]
        old_handlers = {
            s: signal.signal(s, guard.signal_stop) for s in (signal.SIGTERM, signal.SIGINT)
        }
        try:
            guard.check()
            torch.set_num_threads(1)
            publish("auditing_and_comparing")
            plan = compare_development(config, config_path, paths)
            report.update(
                selected=plan["selected"],
                baseline=plan["baseline"],
                selection_plan_sha256=object_sha256(plan),
            )
            publish("preprocessing_parity_check")
            inputs, evidence = prepare_probes(config, paths, guard.check)
            freeze_json(post / "preprocessing_verification.json", {"probes": evidence})
            # Bounded MPS resume equivalence in isolated directories, never final results.
            smoke_path = post / "smoke.json"
            if smoke_path.exists():
                smoke = read_json(smoke_path)
                if smoke["code_sha256"] != digest or smoke["status"] != "passed":
                    raise RuntimeError("Post-training MPS smoke provenance differs.")
            else:
                publish("isolated_mps_smoke")
                isolated = RuntimePaths(
                    paths.research_root,
                    post / "smoke",
                    paths.m2d_source_root,
                    paths.repository_root,
                )
                smoke = final_run(
                    config,
                    config_path,
                    isolated,
                    plan,
                    plan["selected"],
                    interrupt=guard.check,
                    max_additional_updates=1,
                )
                if smoke["status"] != "paused" or smoke["global_step"] != 1:
                    raise RuntimeError("Bounded MPS smoke did not save exactly one update.")
                resumed = final_run(
                    config,
                    config_path,
                    isolated,
                    plan,
                    plan["selected"],
                    interrupt=guard.check,
                    max_additional_updates=1,
                )
                continuous_paths = RuntimePaths(
                    paths.research_root,
                    post / "smoke_continuous",
                    paths.m2d_source_root,
                    paths.repository_root,
                )
                continuous = final_run(
                    config,
                    config_path,
                    continuous_paths,
                    plan,
                    plan["selected"],
                    interrupt=guard.check,
                    max_additional_updates=2,
                )
                if any(
                    r["status"] != "paused" or r["global_step"] != 2 for r in (resumed, continuous)
                ):
                    raise RuntimeError("MPS two-update smoke did not stop at the expected step.")
                digests = []
                for probe_paths in (isolated, continuous_paths):
                    checkpoint = (
                        probe_paths.output_root
                        / "post_training/final"
                        / plan["selected"]
                        / "last.pt"
                    )
                    saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
                    digests.append(state_digest(saved["partial_model"]))
                    del saved
                if digests[0] != digests[1]:
                    raise RuntimeError("MPS resumed and continuous final-training weights differ.")
                atomic_json(
                    smoke_path,
                    {
                        "status": "passed",
                        "code_sha256": digest,
                        "optimizer_updates_per_branch": 2,
                        "resume_equals_continuous": True,
                        "partial_state_sha256": digests[0],
                        "test_used": False,
                    },
                )
            completions = {}
            for item in plan["final_runs"]:
                recipe = item["recipe"]
                publish("final_retraining", current_recipe=recipe, fixed_epochs=item["epochs"])
                result = final_run(
                    config,
                    config_path,
                    paths,
                    plan,
                    recipe,
                    interrupt=guard.check,
                    progress=lambda current: publish("final_retraining", current=current),
                )
                if result["status"] != "completed":
                    raise InterruptedError(result.get("reason", "Final training paused."))
                completions[recipe] = sha256_file(post / "final" / recipe / "completion.json")
                report["completed_final_recipes"] = list(completions)
            # Final models, selection and every permitted evaluation are frozen before test access.
            evaluation_plan = freeze_json(
                post / "evaluation_plan.json",
                {
                    "selection_plan_sha256": sha256_file(post / "selection_plan.json"),
                    "final_completion_sha256": completions,
                    "evaluations": plan["evaluation"],
                    "config_sha256": object_sha256(config),
                    "code_sha256": digest,
                },
            )
            results = {}
            store = ManifestStore(config, paths)
            for item in plan["evaluation"]:
                guard.check()
                recipe, dataset = item["recipe"], item["dataset"]
                key = f"{recipe}-{dataset}"
                publish("sealed_test_evaluation", current_evaluation=key, test_used=True)

                def operation(recipe=recipe, dataset=dataset):
                    model = load_final(config, paths, recipe, "mps")
                    try:
                        rows = store.load(dataset, "test")
                        if len(rows) != config["datasets"][dataset]["counts"]["test"]:
                            raise RuntimeError("Sealed test count differs.")
                        if dataset == "enes" and len({r["baby_id"] for r in rows}) != 5:
                            raise RuntimeError(
                                "Enes test must contain exactly five held-out babies."
                            )
                        metrics, predictions = evaluate(
                            model, paths, config, dataset, rows, "mps", guard.check
                        )
                        result = {
                            "metrics": metrics,
                            "predictions": predictions,
                            "test_used": True,
                            "inference_passes": 1,
                        }
                        if dataset == "enes":
                            result["grouped_metrics"] = grouped_metrics(
                                predictions, model.labels[dataset]
                            )
                        return result
                    finally:
                        model = None
                        gc.collect()
                        torch.mps.empty_cache()

                results[key] = evaluate_once(post / "test" / key, evaluation_plan, item, operation)
            labels = config["datasets"]["donate"]["labels"]
            donate_results = {
                r: [results[f"{r}-donate"]["predictions"]]
                for r in (plan["selected"], plan["baseline"])
            }
            development = store.load("donate", "development")
            counts = [sum(r["label"] == label for r in development) for label in labels]
            majority = max(range(len(labels)), key=lambda i: counts[i])
            baseline_rows = [
                {"true_index": r["true_index"], "predicted_index": majority}
                for r in donate_results[plan["selected"]][0]
            ]
            summary = {
                "selected": plan["selected"],
                "baseline": plan["baseline"],
                "metrics": {k: v["metrics"] for k, v in results.items()},
                "majority_development_baseline": metrics_from_predictions(baseline_rows, labels),
                "bootstrap": paired_group_bootstrap(
                    donate_results,
                    labels,
                    repetitions=config["training"]["bootstrap_repetitions"],
                    seed=config["selection"]["final_seed"],
                ),
                "test_used": True,
                "selection_changed_after_test": False,
                "calibration_status": "NOT_VALIDATED",
                "release_ready": False,
            }
            freeze_json(post / "evaluation_summary.json", summary)
            publish("export_and_fresh_process_verification")
            export_selected(config, paths, plan, summary, inputs, evidence, interrupt=guard.check)
            completion = {
                "status": "completed",
                "completed_utc": utc_now(),
                "artifact_sha256": {
                    name: sha256_file(post / name)
                    for name in (
                        "selection_plan.json",
                        "comparison.json",
                        "evaluation_plan.json",
                        "evaluation_summary.json",
                        "preprocessing_verification.json",
                    )
                },
                "selected": plan["selected"],
                "baseline": plan["baseline"],
                "export_completion_sha256": sha256_file(post / "export/selected/completion.json"),
                "final_completion_sha256": completions,
                "test_completion_sha256": {
                    key: sha256_file(post / "test" / key / "completion.json") for key in results
                },
            }
            atomic_json(post / "completion.json", completion)
            verify_post_completion(post)
            publish("completed", export_directory=str(post / "export/selected"))
            return report
        except InterruptedError as error:
            publish("paused", error=str(error))
            return report
        except Exception as error:
            publish("failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)


def launch_post_training(config, config_path, paths, *, resume=False):
    post = paths.output_root / "post_training"
    post.mkdir(parents=True, exist_ok=True)
    with (post / ".launch.lock").open("a") as launch_lock:
        fcntl.flock(launch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        launch_path = post / "launch.json"
        if launch_path.exists():
            prior = read_json(launch_path)
            try:
                os.kill(prior["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError("Prior post-training launcher still alive.")
        source = Path(__file__).parent
        digest = code_hash(source, config_path)[0]
        snapshot = post / "code" / digest
        package = snapshot / "m2d_supervised"
        target_config = snapshot / "config/experiment_v1.json"
        with training_locks(paths):
            RuntimeGuard(paths, config["training"]["minimum_free_gib"]).check()
            package.mkdir(parents=True, exist_ok=True)
            target_config.parent.mkdir(parents=True, exist_ok=True)
            for original in [*source.glob("*.py"), config_path]:
                target = target_config if original == config_path else package / original.name
                if target.exists() and sha256_file(target) != sha256_file(original):
                    raise RuntimeError("Post-training snapshot was changed.")
                if not target.exists():
                    shutil.copy2(original, target)
            if code_hash(package, target_config)[0] != digest:
                raise RuntimeError("Snapshot hash differs from tested source.")
        command = [
            "/usr/bin/caffeinate",
            "-i",
            "-s",
            sys.executable,
            "-u",
            "-m",
            "m2d_supervised",
            "post-training",
            "--research-root",
            str(paths.research_root),
            "--output-root",
            str(paths.output_root),
            "--m2d-source-root",
            str(paths.m2d_source_root),
            "--repository-root",
            str(paths.repository_root),
            "--config",
            str(target_config),
        ]
        if resume:
            command.append("--resume")
        environment = dict(
            os.environ,
            PYTHONPATH=str(snapshot),
            PYTHONUNBUFFERED="1",
            OMP_NUM_THREADS="1",
            OPENBLAS_NUM_THREADS="1",
            VECLIB_MAXIMUM_THREADS="1",
        )
        with (post / "training.log").open("ab") as log:
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
        }
        atomic_json(launch_path, record)
        return record
