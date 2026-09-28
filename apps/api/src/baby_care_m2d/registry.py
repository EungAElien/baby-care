from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODEL_VERSION = "m2d-supervised-v1.0.0-final-B"
PREPROCESS_VERSION = "m2d-logmel-v1.0.0"
LABEL_MAPPING_VERSION = "donate-cause-labels-v1.0.0"
REGISTRY_PATH = Path(__file__).with_name("registry") / "m2d-supervised-v1.0.0-final-B.json"


class ModelRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ModelConfigurationError(ModelRuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__("MODEL_CONFIGURATION_INVALID", message)


class ModelIntegrityError(ModelRuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__("MODEL_INTEGRITY_FAILED", message)


@dataclass(frozen=True)
class RuntimePaths:
    allowed_root: Path
    bundle: Path
    source: Path


@dataclass(frozen=True)
class ValidatedBundle:
    path: Path
    metadata: dict[str, Any]
    config: dict[str, Any]
    registry: dict[str, Any]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelIntegrityError("A trusted JSON artifact contains a duplicate key")
        result[key] = value
    return result


def read_json(path: Path, *, maximum_bytes: int = 1024 * 1024) -> dict[str, Any]:
    try:
        if path.stat().st_size > maximum_bytes:
            raise ModelIntegrityError("A trusted JSON artifact exceeds its size limit")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ModelRuntimeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ModelIntegrityError("A trusted JSON artifact cannot be read") from error
    if not isinstance(value, dict):
        raise ModelIntegrityError("A trusted JSON artifact is not an object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(block)
    except (OSError, RuntimeError) as error:
        raise ModelIntegrityError("A trusted runtime file cannot be read") from error
    return digest.hexdigest()


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    registry = read_json(path)
    if registry.get("schema_version") != 1:
        raise ModelConfigurationError("Unsupported model registry schema")
    return registry


def _resolve_inside(path: Path, root: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ModelConfigurationError(f"{label} must be an absolute server path")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ModelConfigurationError(f"{label} is unavailable") from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ModelConfigurationError(f"{label} escapes the allowed runtime root") from error
    return resolved


def validate_runtime_paths(
    *, allowed_root: Path, bundle_path: Path, source_path: Path
) -> RuntimePaths:
    if not allowed_root.is_absolute():
        raise ModelConfigurationError("The allowed runtime root must be absolute")
    try:
        root = allowed_root.resolve(strict=True)
    except OSError as error:
        raise ModelConfigurationError("The allowed runtime root is unavailable") from error
    if not root.is_dir():
        raise ModelConfigurationError("The allowed runtime root is not a directory")
    bundle = _resolve_inside(bundle_path, root, label="The model bundle")
    source = _resolve_inside(source_path, root, label="The M2D source")
    if not bundle.is_dir() or not source.is_dir():
        raise ModelConfigurationError("A configured runtime path is not a directory")
    return RuntimePaths(allowed_root=root, bundle=bundle, source=source)


def _trusted_file(directory: Path, relative: str) -> Path:
    if Path(relative).name != relative:
        raise ModelConfigurationError("The registry contains a nested artifact name")
    path = directory / relative
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(directory)
    except (OSError, RuntimeError, ValueError) as error:
        raise ModelIntegrityError("A bundle artifact escapes the trusted bundle") from error
    if not resolved.is_file():
        raise ModelIntegrityError("A required bundle artifact is not a regular file")
    return resolved


def _expect_equal(observed: Any, expected: Any, label: str) -> None:
    if observed != expected:
        raise ModelIntegrityError(f"{label} does not match the trusted registry")


def validate_bundle(path: Path, registry: dict[str, Any]) -> ValidatedBundle:
    model_spec = registry["model"]
    artifacts = model_spec["artifacts"]
    resolved: dict[str, Path] = {}
    for name, identity in artifacts.items():
        artifact = _trusted_file(path, name)
        resolved[name] = artifact
        _expect_equal(artifact.stat().st_size, identity["bytes"], f"{name} size")
        _expect_equal(sha256_file(artifact), identity["sha256"], f"{name} SHA-256")

    metadata = read_json(resolved["metadata.json"])
    config = read_json(resolved["config.json"])
    _expect_equal(metadata.get("model_version"), model_spec["model_version"], "model version")
    _expect_equal(metadata.get("product_head"), model_spec["product_head"], "product head")
    _expect_equal(metadata.get("trained_heads"), model_spec["trained_heads"], "trained heads")
    _expect_equal(
        metadata.get("full_state_sha256"),
        model_spec["full_state_sha256"],
        "full state identity",
    )
    _expect_equal(
        metadata.get("m2d_source_commit"),
        registry["source"]["commit"],
        "metadata M2D source commit",
    )
    labels = metadata.get("labels")
    if not isinstance(labels, dict):
        raise ModelIntegrityError("metadata labels are not an object")
    _expect_equal(
        labels.get(model_spec["product_head"]),
        model_spec["labels"],
        "label order",
    )
    _expect_equal(len(model_spec["labels"]), model_spec["output_dimension"], "output dimension")
    _expect_equal(
        metadata.get("preprocessing"), registry["preprocessing"], "metadata preprocessing"
    )
    _expect_equal(config.get("preprocessing"), registry["preprocessing"], "config preprocessing")
    config_model = config.get("model")
    if not isinstance(config_model, dict):
        raise ModelIntegrityError("config model is not an object")
    _expect_equal(
        config_model.get("architecture"),
        model_spec["architecture"]["name"],
        "architecture",
    )
    _expect_equal(
        config_model.get("source_commit"),
        registry["source"]["commit"],
        "M2D source commit",
    )
    _expect_equal(
        config_model.get("input_size"),
        model_spec["architecture"]["input_size"],
        "architecture input size",
    )
    _expect_equal(
        config_model.get("patch_size"),
        model_spec["architecture"]["patch_size"],
        "architecture patch size",
    )
    _expect_equal(
        config_model.get("classifier_feature_dimension"),
        model_spec["architecture"]["classifier_feature_dimension"],
        "classifier feature dimension",
    )
    _expect_equal(metadata.get("calibration_status"), "NOT_VALIDATED", "calibration status")
    _expect_equal(metadata.get("release_ready"), False, "release readiness")
    _expect_equal(
        metadata.get("runtime_versions"),
        registry["runtime"]["export_packages"],
        "export runtime versions",
    )
    reported_hashes = metadata.get("artifact_sha256")
    if not isinstance(reported_hashes, dict):
        raise ModelIntegrityError("metadata artifact hashes are not an object")
    for name, identity in artifacts.items():
        if name in reported_hashes:
            _expect_equal(reported_hashes[name], identity["sha256"], f"{name} self hash")
    return ValidatedBundle(path=path, metadata=metadata, config=config, registry=registry)


def validate_source(path: Path, registry: dict[str, Any]) -> dict[str, str]:
    source_spec = registry["source"]
    marker_path = path / "runtime-source.json"
    try:
        resolved_marker = marker_path.resolve(strict=True)
        resolved_marker.relative_to(path)
    except (OSError, RuntimeError, ValueError) as error:
        raise ModelIntegrityError("The prepared source marker escapes its source root") from error
    if not resolved_marker.is_file():
        raise ModelIntegrityError("The prepared source marker is unavailable")
    marker = read_json(resolved_marker)
    _expect_equal(marker.get("source_commit"), source_spec["commit"], "source marker commit")
    _expect_equal(
        marker.get("original_marker_sha256"),
        source_spec["original_marker_sha256"],
        "source marker identity",
    )
    observed: dict[str, str] = {}
    for relative, expected in source_spec["files"].items():
        candidate = path / relative
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(path)
        except (OSError, RuntimeError, ValueError) as error:
            raise ModelIntegrityError(
                "An M2D source file escapes the trusted source root"
            ) from error
        if not resolved.is_file():
            raise ModelIntegrityError("A required M2D source file is unavailable")
        digest = sha256_file(resolved)
        _expect_equal(digest, expected["sha256"], f"M2D source {relative}")
        observed[relative] = digest
    _expect_equal(marker.get("files"), observed, "prepared source marker")
    return observed


def validate_verification_artifacts(bundle: Path, registry: dict[str, Any]) -> None:
    for name, identity in registry["verification"]["artifacts"].items():
        artifact = _trusted_file(bundle, name)
        _expect_equal(artifact.stat().st_size, identity["bytes"], f"{name} size")
        _expect_equal(sha256_file(artifact), identity["sha256"], f"{name} SHA-256")
