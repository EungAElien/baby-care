"""Local full-weight export and independent-process fidelity verification."""

from __future__ import annotations

import argparse
import gc
import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch

from .checkpoint import atomic_torch, frozen_digest, load_partial, state_digest
from .comparison import checked_artifacts, freeze_json
from .data import ManifestStore, load_feature
from .inference import audio_logmel, center_input, load_bundle
from .training import create_model, runtime_versions
from .util import atomic_json, object_sha256, read_json, sha256_file, utc_now


def load_final(config, paths, recipe, device):
    run = paths.output_root / "post_training" / "final" / recipe
    report = read_json(run / "completion.json")
    checked_artifacts(run, report)
    if report["identity"]["config_sha256"] != object_sha256(config):
        raise RuntimeError("Final model/config identity differs.")
    model = create_model(config, paths, recipe, report["identity"]["seed"])
    payload = torch.load(run / "model.pt", map_location="cpu", weights_only=True)
    load_partial(model, config, payload, report["identity"], frozen_digest(model, config))
    return model.to(device).eval()


def prepare_probes(config, paths, interrupt=lambda: None):
    """One development recording per Donate label, never a sealed test probe."""
    rows = ManifestStore(config, paths).load("donate", "development")
    chosen = [
        next(r for r in rows if r["label"] == label)
        for label in config["datasets"]["donate"]["labels"]
    ]
    inputs, evidence = [], []
    root = paths.dataset_root(config, "donate")
    for row in chosen:
        interrupt()
        source = root / row["selected_audio"]
        if sha256_file(source) != row["selected_file_sha256"]:
            raise RuntimeError("Development probe source audio hash differs.")
        derived = audio_logmel(source, config)
        stored = np.load(root / row["file_name"], allow_pickle=False)
        if derived.shape != stored.shape:
            raise RuntimeError("Waveform/inference log-mel shape differs from preparation.")
        delta = float(np.max(np.abs(derived - stored)))
        training = load_feature(
            paths, config, "donate", row, seed=42, phase="validation", epoch=0, occurrence=0
        )
        inference = center_input(derived, config)
        input_delta = float(torch.max(torch.abs(inference - training)))
        if delta > 1e-6 or input_delta > 1e-6:
            raise RuntimeError(f"Waveform preparation parity failed: {delta}, {input_delta}")
        inputs.append(inference)
        evidence.append(
            {
                "sample_id": row["sample_id"],
                "split": "development",
                "source_sha256": row["selected_file_sha256"],
                "feature_sha256": row["spec_sha256"],
                "logmel_max_abs_delta": delta,
                "normalized_max_abs_delta": input_delta,
                "input_shape": list(inference.shape),
            }
        )
    return inputs, evidence


def export_selected(
    config,
    paths,
    plan,
    evaluation_summary,
    inputs,
    evidence,
    *,
    device="mps",
    interrupt=lambda: None,
):
    post = paths.output_root / "post_training"
    bundle = post / "export" / "selected"
    bundle.mkdir(parents=True, exist_ok=True)
    if (bundle / "completion.json").exists():
        report = read_json(bundle / "completion.json")
        if report["selection_plan_sha256"] != object_sha256(plan):
            raise RuntimeError("Export selection differs.")
        checked_artifacts(bundle, report)
        return report
    interrupt()
    model = load_final(config, paths, plan["selected"], device)
    with torch.inference_mode():
        expected = [
            model(value.unsqueeze(0).to(device), "donate").softmax(-1).cpu().tolist()[0]
            for value in inputs
        ]
    # All tensors, including the frozen base encoder, are materialized in this artifact.
    full_digest = state_digest(model.state_dict())
    atomic_torch(
        bundle / "model.pt",
        {"full_state_dict": model.state_dict()},
        config["training"]["minimum_free_gib"],
    )
    atomic_torch(
        bundle / "probes.pt",
        {"inputs": inputs, "expected_scores": expected},
        config["training"]["minimum_free_gib"],
    )
    freeze_json(bundle / "config.json", config)
    freeze_json(bundle / "evaluation.json", evaluation_summary)
    freeze_json(bundle / "preprocessing_verification.json", {"probes": evidence})
    versions = {
        **runtime_versions(),
        **{name: importlib.metadata.version(name) for name in ("nnAudio", "soundfile", "soxr")},
    }
    metadata = {
        "schema_version": 1,
        "model_version": config["experiment_version"] + "-final-" + plan["selected"],
        "selected_recipe": plan["selected"],
        "selection_plan_sha256": object_sha256(plan),
        "final_completion_sha256": sha256_file(
            post / "final" / plan["selected"] / "completion.json"
        ),
        "full_state_sha256": full_digest,
        "trained_heads": list(model.trained_heads),
        "labels": model.labels,
        "product_head": "donate",
        "preprocessing": config["preprocessing"],
        "runtime_versions": versions,
        "m2d_source_commit": config["model"]["source_commit"],
        "runtime_requirement": "Installed pinned M2D architecture and this inference package; "
        "no initial/partial checkpoints or training data required.",
        "distribution": "LOCAL_PRIVATE_ONLY; no upstream source or raw audio redistributed",
        "calibration_status": "NOT_VALIDATED",
        "release_ready": False,
        "artifact_sha256": {
            name: sha256_file(bundle / name)
            for name in (
                "model.pt",
                "probes.pt",
                "config.json",
                "evaluation.json",
                "preprocessing_verification.json",
            )
        },
    }
    atomic_json(bundle / "metadata.json", metadata)
    model = None
    gc.collect()
    if device == "mps":
        torch.mps.empty_cache()
    interrupt()
    verification = bundle / "fresh_process_verification.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "m2d_supervised.exporting",
            "--bundle",
            str(bundle),
            "--m2d-source-root",
            str(paths.m2d_source_root),
            "--device",
            device,
            "--result",
            str(verification),
        ],
        check=True,
        timeout=180,
    )
    report = {
        "status": "completed",
        "completed_utc": utc_now(),
        "selection_plan_sha256": object_sha256(plan),
        "calibration_status": "NOT_VALIDATED",
        "release_ready": False,
        "artifact_sha256": {
            name: sha256_file(bundle / name)
            for name in (
                *metadata["artifact_sha256"],
                "metadata.json",
                "fresh_process_verification.json",
            )
        },
    }
    atomic_json(bundle / "completion.json", report)
    return report


def verify_fresh(bundle, source_root, device, result):
    torch.set_num_threads(1)
    model, _, metadata = load_bundle(bundle, source_root, device)
    probes = torch.load(bundle / "probes.pt", map_location="cpu", weights_only=True)
    with torch.inference_mode():
        observed = [
            model(x.unsqueeze(0).to(device), "donate").softmax(-1).cpu().tolist()[0]
            for x in probes["inputs"]
        ]
    delta = float(np.max(np.abs(np.asarray(observed) - np.asarray(probes["expected_scores"]))))
    if delta > 1e-6 or state_digest(model.state_dict()) != metadata["full_state_sha256"]:
        raise RuntimeError("Independent full model does not reproduce the in-memory model.")
    atomic_json(
        result,
        {
            "status": "passed",
            "pid": os.getpid(),
            "device": device,
            "probes": len(observed),
            "maximum_score_delta": delta,
            "original_checkpoints_used": False,
            "test_inputs_used": False,
            "full_state_sha256": metadata["full_state_sha256"],
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--m2d-source-root", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    verify_fresh(args.bundle, args.m2d_source_root, args.device, args.result)
