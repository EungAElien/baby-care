from __future__ import annotations

import argparse
from pathlib import Path

from baby_care_api.llm_eval.runner import run_live_smoke, run_offline, write_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the synthetic B-02 LLM evaluation")
    parser.add_argument(
        "--mode",
        choices=("offline", "live-smoke"),
        default="offline",
        help="offline never makes provider calls; live-smoke is bounded and synthetic-only",
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--allow-provider-calls",
        action="store_true",
        help="required in addition to --mode live-smoke",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = (
        run_offline()
        if args.mode == "offline"
        else run_live_smoke(allow_provider_calls=args.allow_provider_calls)
    )
    write_report(report, args.report)
    decision = report.get("summary", {}).get("decision", "UNKNOWN")
    print(
        f"mode={args.mode} decision={decision} "
        f"provider_requests={report.get('provider_request_count', 0)} "
        f"actual_model_executed={report.get('actual_model_executed', False)}"
    )
    successful_decisions = {
        "AUTOMATED_PASS_HUMAN_REVIEW_PENDING",
        "SMOKE_AUTOMATED_PASS_HUMAN_REVIEW_PENDING",
    }
    return 0 if decision in successful_decisions else 1


if __name__ == "__main__":
    raise SystemExit(main())
