"""Command-line entry points for preparation-only supervised M2D checks."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .data import RuntimePaths
from .preflight import run_preflight
from .util import read_json

PACKAGE_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PACKAGE_PROJECT_ROOT / "config" / "experiment_v1.json"
DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="m2d-supervised",
        description="Validate frozen M2D supervised-learning inputs without training.",
    )
    commands = root.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser(
        "preflight",
        help="Verify sources, checkpoints, splits, features, resources and locks.",
    )
    preflight.add_argument("--research-root", required=True, type=_path)
    preflight.add_argument("--output-root", required=True, type=_path)
    preflight.add_argument("--m2d-source-root", required=True, type=_path)
    preflight.add_argument("--repository-root", type=_path, default=DEFAULT_REPOSITORY_ROOT)
    preflight.add_argument("--config", type=_path, default=DEFAULT_CONFIG)
    preflight.add_argument(
        "--bounded-check",
        action="store_true",
        help=(
            "Skip full feature hashes for a bounded development check. "
            "This never marks the preparation ready for training."
        ),
    )
    return root


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command != "preflight":
        raise AssertionError(f"Unhandled command: {arguments.command}")
    config = read_json(arguments.config)
    paths = RuntimePaths(
        research_root=arguments.research_root,
        output_root=arguments.output_root,
        m2d_source_root=arguments.m2d_source_root,
        repository_root=arguments.repository_root,
    )
    report = run_preflight(
        config,
        arguments.config,
        paths,
        full_feature_check=not arguments.bounded_check,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
