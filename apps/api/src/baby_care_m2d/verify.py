from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import socket
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .registry import read_json, validate_verification_artifacts
from .runtime import M2DRuntime, load_runtime


def _scores(result: Any) -> np.ndarray:
    return np.asarray([item.score for item in result.scores], dtype=np.float64)


def _maximum_delta(left: torch.Tensor | np.ndarray, right: torch.Tensor | np.ndarray) -> float:
    left_array = left.detach().cpu().numpy() if isinstance(left, torch.Tensor) else left
    right_array = right.detach().cpu().numpy() if isinstance(right, torch.Tensor) else right
    return float(np.max(np.abs(np.asarray(left_array) - np.asarray(right_array))))


def _verify_tensor_probes(runtime: M2DRuntime) -> dict[str, Any]:
    registry = runtime.bundle.registry
    validate_verification_artifacts(runtime.bundle.path, registry)
    probes = torch.load(
        runtime.bundle.path / "probes.pt",
        map_location="cpu",
        weights_only=True,
    )
    inputs = probes["inputs"]
    expected_scores = np.asarray(probes["expected_scores"], dtype=np.float64)
    if len(inputs) != 5 or expected_scores.shape != (5, 5):
        raise RuntimeError("The development probe structure changed")
    observed: list[np.ndarray] = []
    latencies: list[float] = []
    for value in inputs:
        started = time.perf_counter()
        observed.append(_scores(runtime.infer_tensor(value.cpu().float())))
        latencies.append(time.perf_counter() - started)
    maximum = _maximum_delta(np.asarray(observed), expected_scores)
    per_probe = np.max(np.abs(np.asarray(observed) - expected_scores), axis=1).tolist()
    tolerance = float(registry["verification"]["maximum_absolute_tolerance"])
    return {
        "status": "passed" if maximum <= tolerance else "failed",
        "count": len(inputs),
        "maximum_score_delta": maximum,
        "per_probe_maximum_score_delta": per_probe,
        "tolerance": tolerance,
        "inference_seconds": latencies,
        "median_inference_seconds": statistics.median(latencies),
    }


def _verify_audio_set(
    runtime: M2DRuntime,
    manifest: dict[str, Any],
    *,
    root: Path,
    field: str,
    allow_compressed: bool,
) -> dict[str, Any]:
    probes = torch.load(
        runtime.bundle.path / "probes.pt",
        map_location="cpu",
        weights_only=True,
    )
    expected_inputs = probes["inputs"]
    expected_scores = np.asarray(probes["expected_scores"], dtype=np.float64)
    tolerance = float(runtime.bundle.registry["verification"]["maximum_absolute_tolerance"])
    results: list[dict[str, Any]] = []
    for index, item in enumerate(manifest["probes"]):
        try:
            preprocessed = runtime.preprocess(
                root / item[field],
                allow_compressed_for_verification=allow_compressed,
            )
            inference = runtime.infer_tensor(preprocessed.normalized)
            input_delta = _maximum_delta(preprocessed.normalized, expected_inputs[index])
            score_delta = _maximum_delta(_scores(inference), expected_scores[index])
            results.append(
                {
                    "sample_id": item["sample_id"],
                    "label": item["label"],
                    "decode": {
                        "format_name": preprocessed.decode.format_name,
                        "codec_name": preprocessed.decode.codec_name,
                        "sample_rate": preprocessed.decode.sample_rate,
                        "channels": preprocessed.decode.channels,
                        "samples": preprocessed.decode.samples,
                    },
                    "normalized_input_maximum_delta": input_delta,
                    "score_maximum_delta": score_delta,
                    "within_tolerance": input_delta <= tolerance and score_delta <= tolerance,
                }
            )
        except Exception as error:
            results.append(
                {
                    "sample_id": item["sample_id"],
                    "label": item["label"],
                    "within_tolerance": False,
                    "error_code": getattr(error, "code", type(error).__name__),
                }
            )
    passed = all(item["within_tolerance"] for item in results)
    return {
        "status": "passed" if passed else "failed",
        "supported": passed and not allow_compressed,
        "tolerance": tolerance,
        "results": results,
    }


def _memory_mib() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return float(value / 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the fixed V1 B Linux CPU runtime")
    parser.add_argument("--allowed-root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=Path("/usr/local/bin/ffmpeg"))
    parser.add_argument("--audio-probe-root", type=Path)
    parser.add_argument("--audio-probe-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    before_mib = _memory_mib()
    runtime = load_runtime(
        allowed_root=args.allowed_root,
        bundle_path=args.bundle,
        source_path=args.source,
        ffmpeg_path=args.ffmpeg,
    )
    after_load_mib = _memory_mib()
    try:
        tensor = _verify_tensor_probes(runtime)
        audio_manifest = (
            None if args.audio_probe_manifest is None else read_json(args.audio_probe_manifest)
        )
        pcm = None
        compressed = None
        if audio_manifest is not None:
            if args.audio_probe_root is None:
                raise RuntimeError("Audio probe root is required with its manifest")
            pcm = _verify_audio_set(
                runtime,
                audio_manifest,
                root=args.audio_probe_root,
                field="pcm_file",
                allow_compressed=False,
            )
            compressed = _verify_audio_set(
                runtime,
                audio_manifest,
                root=args.audio_probe_root,
                field="source_file",
                allow_compressed=True,
            )
            # Compressed inputs are evidence-only until every decoder result meets
            # the fixed tolerance; they are never enabled by this verifier.
            compressed["supported"] = False
        status = (
            "passed"
            if tensor["status"] == "passed" and (pcm is None or pcm["status"] == "passed")
            else "failed"
        )
        network_interfaces = [name for _, name in socket.if_nameindex()]
        report = {
            "schema_version": 1,
            "status": status,
            "scope": "B-02/B-06 V1 B runtime foundation only",
            "model_version": runtime.bundle.registry["model"]["model_version"],
            "preprocess_version": runtime.bundle.registry["preprocess_version"],
            "label_mapping_version": runtime.bundle.registry["label_mapping_version"],
            "calibration_status": "NOT_VALIDATED",
            "release_ready": False,
            "device": "cpu",
            "dtype": "float32",
            "load_count": runtime.load_count,
            "load_seconds": runtime.load_seconds,
            "maximum_rss_before_load_mib": before_mib,
            "maximum_rss_after_load_mib": after_load_mib,
            "runtime": {
                "python": platform.python_version(),
                "os": platform.system(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "cpu_count": os.cpu_count(),
                "packages": runtime.packages,
                "ffmpeg": runtime.ffmpeg,
                "torch_cpu_capability": torch.backends.cpu.get_cpu_capability(),
                "network_interfaces": network_interfaces,
            },
            "tensor_probes": tensor,
            "pcm_wav_probes": pcm,
            "compressed_decoder_measurement": compressed,
            "original_checkpoints_used": False,
            "training_dataset_required": False,
            "sealed_test_used": False,
            "network_isolation_observed": network_interfaces == ["lo"],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if status == "passed" else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
