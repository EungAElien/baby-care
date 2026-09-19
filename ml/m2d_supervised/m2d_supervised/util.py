"""Small deterministic, hashing, atomic-write and runtime helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GIB = 1024**3


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def object_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def seed_for(*values: Any) -> int:
    payload = canonical_json(values).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "little") % (2**63)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def free_bytes(path: Path) -> int:
    probe = path if path.exists() else path.parent
    return shutil.disk_usage(probe).free


def require_free_space(path: Path, minimum_free_gib: float, temporary_bytes: int = 0) -> int:
    available = free_bytes(path)
    minimum = int(minimum_free_gib * GIB)
    if available - temporary_bytes < minimum:
        raise RuntimeError(
            "Insufficient storage: an atomic write would leave less than "
            f"{minimum_free_gib:g} GiB free."
        )
    return available


def ac_power() -> bool:
    result = subprocess.run(
        ["/usr/bin/pmset", "-g", "batt"],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0 and "AC Power" in result.stdout


def git_commit(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def code_hash(package_root: Path, config_path: Path) -> tuple[str, dict[str, str]]:
    hashes = {
        f"m2d_supervised/{path.name}": sha256_file(path)
        for path in sorted(package_root.glob("*.py"))
    }
    hashes["config/experiment_v1.json"] = sha256_file(config_path)
    return object_sha256(hashes), hashes


def ensure_within(root: Path, path: Path) -> Path:
    root = root.resolve()
    path = path.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"Path escapes the configured root: {path}")
    return path
