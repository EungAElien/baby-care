from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = (
    ROOT / "apps/api/src/baby_care_m2d/registry/m2d-supervised-v1.0.0-final-B.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify and copy only the pinned M2D inference source into a Docker context"
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.source_root.resolve(strict=True)
    output = args.output.resolve()
    if output == ROOT or output.is_relative_to(ROOT):
        raise RuntimeError(
            "Runtime source context must stay outside the Git repository"
        )
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("Refusing to overwrite a non-empty runtime source context")
    output.mkdir(parents=True, exist_ok=True)

    registry = read_json(REGISTRY)
    source_spec = registry["source"]
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if commit != source_spec["commit"]:
        raise RuntimeError("The source checkout commit differs from the registry")
    original_marker = source / ".babycry-stage2-source.json"
    if sha256_file(original_marker) != source_spec["original_marker_sha256"]:
        raise RuntimeError("The original M2D source marker differs from the registry")
    marker = read_json(original_marker)
    if marker.get("commit") != source_spec["commit"]:
        raise RuntimeError("The original M2D source marker commit differs")

    observed: dict[str, str] = {}
    for relative, identity in source_spec["files"].items():
        candidate = (source / relative).resolve(strict=True)
        candidate.relative_to(source)
        digest = sha256_file(candidate)
        if digest != identity["sha256"]:
            raise RuntimeError(f"Pinned source file differs: {relative}")
        generated = marker.get("generated_sha256", {}).get(relative)
        if generated is not None and generated != digest:
            raise RuntimeError(f"Generated source marker differs: {relative}")
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)
        observed[relative] = digest

    runtime_marker = {
        "schema_version": 1,
        "source_commit": source_spec["commit"],
        "original_marker_sha256": source_spec["original_marker_sha256"],
        "files": observed,
    }
    (output / "runtime-source.json").write_text(
        json.dumps(runtime_marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(runtime_marker, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
