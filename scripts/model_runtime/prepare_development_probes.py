from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import unicodedata
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


def resolve_portable(root: Path, relative: str) -> Path:
    direct = root / relative
    if direct.exists():
        return direct
    current = root
    for part in Path(relative).parts:
        normalized = unicodedata.normalize("NFC", part)
        matches = [
            child
            for child in current.iterdir()
            if unicodedata.normalize("NFC", child.name) == normalized
        ]
        if len(matches) != 1:
            raise FileNotFoundError(
                f"Could not resolve a configured dataset component: {part}"
            )
        current = matches[0]
    return current


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare five non-sealed development probes outside the repository"
    )
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--afconvert", type=Path, default=Path("/usr/bin/afconvert"))
    args = parser.parse_args()

    research_root = args.research_root.resolve(strict=True)
    bundle = args.bundle.resolve(strict=True)
    output = args.output.resolve()
    if output == ROOT or output.is_relative_to(ROOT):
        raise RuntimeError("Audio probes must stay outside the Git repository")
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("Refusing to overwrite a non-empty audio probe directory")
    (output / "compressed").mkdir(parents=True, exist_ok=True)
    (output / "pcm").mkdir(parents=True, exist_ok=True)

    registry = read_json(REGISTRY)
    config = read_json(bundle / "config.json")
    if (
        sha256_file(bundle / "config.json")
        != registry["model"]["artifacts"]["config.json"]["sha256"]
    ):
        raise RuntimeError("The probe bundle config differs from the registry")
    donate = config["datasets"]["donate"]
    dataset_root = resolve_portable(research_root, donate["root"])
    manifest_path = dataset_root / "manifests/development.csv"
    if sha256_file(manifest_path) != donate["manifest_sha256"]["development"]:
        raise RuntimeError("The fixed Donate development manifest changed")
    with manifest_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    probes: list[dict[str, Any]] = []
    for index, label in enumerate(registry["model"]["labels"]):
        row = next(item for item in rows if item["label"] == label)
        source = (dataset_root / row["selected_audio"]).resolve(strict=True)
        source.relative_to(dataset_root)
        if sha256_file(source) != row["selected_file_sha256"]:
            raise RuntimeError(f"Development probe source changed: {row['sample_id']}")
        compressed_relative = (
            Path("compressed") / f"probe-{index}{source.suffix.lower()}"
        )
        pcm_relative = Path("pcm") / f"probe-{index}.wav"
        compressed = output / compressed_relative
        pcm = output / pcm_relative
        shutil.copy2(source, compressed)
        subprocess.run(
            [
                str(args.afconvert),
                "-f",
                "WAVE",
                "-d",
                "LEF32",
                str(source),
                str(pcm),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        probes.append(
            {
                "index": index,
                "label": label,
                "sample_id": row["sample_id"],
                "source_file": compressed_relative.as_posix(),
                "source_sha256": row["selected_file_sha256"],
                "pcm_file": pcm_relative.as_posix(),
                "pcm_sha256": sha256_file(pcm),
            }
        )

    manifest = {
        "schema_version": 1,
        "split": "development",
        "sealed_test_used": False,
        "probe_count": len(probes),
        "labels": registry["model"]["labels"],
        "probes": probes,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
