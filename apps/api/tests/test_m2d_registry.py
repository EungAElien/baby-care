from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from baby_care_m2d.registry import (
    LABEL_MAPPING_VERSION,
    MODEL_VERSION,
    PREPROCESS_VERSION,
    ModelConfigurationError,
    ModelIntegrityError,
    load_registry,
    validate_bundle,
    validate_runtime_paths,
    validate_source,
)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    registry = copy.deepcopy(load_registry())
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    preprocessing = registry["preprocessing"]
    model_spec = registry["model"]
    metadata = {
        "model_version": model_spec["model_version"],
        "product_head": model_spec["product_head"],
        "trained_heads": model_spec["trained_heads"],
        "full_state_sha256": model_spec["full_state_sha256"],
        "m2d_source_commit": registry["source"]["commit"],
        "labels": {model_spec["product_head"]: model_spec["labels"]},
        "preprocessing": preprocessing,
        "runtime_versions": registry["runtime"]["export_packages"],
        "calibration_status": "NOT_VALIDATED",
        "release_ready": False,
        "artifact_sha256": {},
    }
    config = {
        "preprocessing": preprocessing,
        "model": {
            "architecture": model_spec["architecture"]["name"],
            "source_commit": registry["source"]["commit"],
            "input_size": model_spec["architecture"]["input_size"],
            "patch_size": model_spec["architecture"]["patch_size"],
            "classifier_feature_dimension": model_spec["architecture"][
                "classifier_feature_dimension"
            ],
        },
    }
    (bundle / "model.pt").write_bytes(b"synthetic-fixed-state")
    _write_json(bundle / "metadata.json", metadata)
    _write_json(bundle / "config.json", config)
    metadata["artifact_sha256"] = {
        "model.pt": _sha(bundle / "model.pt"),
        "config.json": _sha(bundle / "config.json"),
    }
    _write_json(bundle / "metadata.json", metadata)
    for name in registry["model"]["artifacts"]:
        path = bundle / name
        registry["model"]["artifacts"][name] = {
            "sha256": _sha(path),
            "bytes": path.stat().st_size,
        }
    return bundle, registry


def _refresh_identity(bundle: Path, registry: dict[str, Any], name: str) -> None:
    path = bundle / name
    registry["model"]["artifacts"][name] = {
        "sha256": _sha(path),
        "bytes": path.stat().st_size,
    }


def test_fixed_registry_names_versions_and_non_release_status() -> None:
    registry = load_registry()

    assert registry["model"]["model_version"] == MODEL_VERSION
    assert registry["preprocess_version"] == PREPROCESS_VERSION
    assert registry["label_mapping_version"] == LABEL_MAPPING_VERSION
    assert registry["provenance"]["calibration_status"] == "NOT_VALIDATED"
    assert registry["provenance"]["release_ready"] is False
    assert registry["model"]["trained_heads"] == ["donate"]
    assert registry["model"]["output_dimension"] == 5


def test_bundle_identity_accepts_matching_semantics(tmp_path: Path) -> None:
    bundle, registry = _bundle(tmp_path)

    validated = validate_bundle(bundle, registry)

    assert validated.metadata["model_version"] == MODEL_VERSION


@pytest.mark.parametrize("missing", ["model.pt", "metadata.json", "config.json"])
def test_missing_required_bundle_file_is_rejected(tmp_path: Path, missing: str) -> None:
    bundle, registry = _bundle(tmp_path)
    (bundle / missing).unlink()

    with pytest.raises(ModelIntegrityError):
        validate_bundle(bundle, registry)


def test_model_hash_mismatch_is_rejected_before_loading(tmp_path: Path) -> None:
    bundle, registry = _bundle(tmp_path)
    (bundle / "model.pt").write_bytes(b"changed")

    with pytest.raises(ModelIntegrityError, match=r"SHA-256|size"):
        validate_bundle(bundle, registry)


def test_changed_model_version_is_rejected_even_with_updated_file_hash(tmp_path: Path) -> None:
    bundle, registry = _bundle(tmp_path)
    metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
    metadata["model_version"] = "m2d-supervised-untrusted"
    _write_json(bundle / "metadata.json", metadata)
    _refresh_identity(bundle, registry, "metadata.json")

    with pytest.raises(ModelIntegrityError, match="model version"):
        validate_bundle(bundle, registry)


def test_changed_label_order_is_rejected_even_with_updated_file_hash(tmp_path: Path) -> None:
    bundle, registry = _bundle(tmp_path)
    metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
    metadata["labels"]["donate"] = list(reversed(metadata["labels"]["donate"]))
    _write_json(bundle / "metadata.json", metadata)
    _refresh_identity(bundle, registry, "metadata.json")

    with pytest.raises(ModelIntegrityError, match="label order"):
        validate_bundle(bundle, registry)


def test_changed_preprocessing_is_rejected_even_with_updated_file_hash(tmp_path: Path) -> None:
    bundle, registry = _bundle(tmp_path)
    config = json.loads((bundle / "config.json").read_text(encoding="utf-8"))
    config["preprocessing"]["hop_length"] = 161
    _write_json(bundle / "config.json", config)
    _refresh_identity(bundle, registry, "config.json")

    with pytest.raises(ModelIntegrityError, match="config preprocessing"):
        validate_bundle(bundle, registry)


def test_changed_architecture_is_rejected_even_with_updated_file_hash(tmp_path: Path) -> None:
    bundle, registry = _bundle(tmp_path)
    config = json.loads((bundle / "config.json").read_text(encoding="utf-8"))
    config["model"]["architecture"] = "different_model"
    _write_json(bundle / "config.json", config)
    _refresh_identity(bundle, registry, "config.json")

    with pytest.raises(ModelIntegrityError, match="architecture"):
        validate_bundle(bundle, registry)


def test_symlink_outside_allowed_root_is_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    source = allowed / "source"
    allowed.mkdir()
    outside.mkdir()
    source.mkdir()
    (allowed / "bundle").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ModelConfigurationError, match="escapes"):
        validate_runtime_paths(
            allowed_root=allowed,
            bundle_path=allowed / "bundle",
            source_path=source,
        )


def test_relative_server_path_is_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    with pytest.raises(ModelConfigurationError, match="absolute"):
        validate_runtime_paths(
            allowed_root=allowed,
            bundle_path=Path("relative-model"),
            source_path=allowed,
        )


def test_source_file_hash_and_prepared_marker_are_both_required(tmp_path: Path) -> None:
    registry = copy.deepcopy(load_registry())
    source = tmp_path / "source"
    source.mkdir()
    observed: dict[str, str] = {}
    for index, relative in enumerate(registry["source"]["files"]):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"source-{index}", encoding="utf-8")
        digest = _sha(path)
        registry["source"]["files"][relative]["sha256"] = digest
        observed[relative] = digest
    _write_json(
        source / "runtime-source.json",
        {
            "source_commit": registry["source"]["commit"],
            "original_marker_sha256": registry["source"]["original_marker_sha256"],
            "files": observed,
        },
    )

    assert validate_source(source, registry) == observed
    first = source / next(iter(observed))
    first.write_text("changed", encoding="utf-8")
    with pytest.raises(ModelIntegrityError, match="M2D source"):
        validate_source(source, registry)


def test_source_marker_symlink_outside_source_root_is_rejected(tmp_path: Path) -> None:
    registry = copy.deepcopy(load_registry())
    source = tmp_path / "source"
    outside = tmp_path / "outside.json"
    source.mkdir()
    _write_json(outside, {})
    (source / "runtime-source.json").symlink_to(outside)

    with pytest.raises(ModelIntegrityError, match="marker escapes"):
        validate_source(source, registry)
