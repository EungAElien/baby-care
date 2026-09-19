"""Preflight, isolated pilot, resumable training and detached launch commands."""

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
        description="Prepare and train the fixed M2D development experiments.",
    )
    commands = root.add_subparsers(dest="command", required=True)
    options = {}
    for command, help_text in (
        ("preflight", "Verify frozen inputs without an optimizer step."),
        ("pilot", "Run an isolated MPS timing and checkpoint-resume verification."),
        ("train-matrix", "Run or resume the 27 development experiments sequentially."),
        ("launch", "Detach a caffeinate-protected training worker from a code snapshot."),
    ):
        sub = commands.add_parser(command, help=help_text)
        sub.add_argument("--research-root", required=True, type=_path)
        sub.add_argument("--output-root", required=True, type=_path)
        sub.add_argument("--m2d-source-root", required=True, type=_path)
        sub.add_argument("--repository-root", type=_path, default=DEFAULT_REPOSITORY_ROOT)
        sub.add_argument("--config", type=_path, default=DEFAULT_CONFIG)
        options[command] = sub
    preflight = options["preflight"]
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
    config = read_json(arguments.config)
    paths = RuntimePaths(
        research_root=arguments.research_root,
        output_root=arguments.output_root,
        m2d_source_root=arguments.m2d_source_root,
        repository_root=arguments.repository_root,
    )
    if arguments.command == "preflight":
        report = run_preflight(
            config, arguments.config, paths, full_feature_check=not arguments.bounded_check
        )
    elif arguments.command == "pilot":
        from .pilot import run_pilot

        report = run_pilot(config, arguments.config, paths)
    else:
        from .runtime import launch, train_matrix

        operation = launch if arguments.command == "launch" else train_matrix
        report = operation(config, arguments.config, paths)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
