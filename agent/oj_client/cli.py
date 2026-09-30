"""Explicit model-free MiniOJ client commands for Phase 1 verification."""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv

from agent.config import ClientSettings
from agent.workspace.task import TaskWorkspace

from .client import OJClient, OJClientError
from .workflow import run_fixed_solution


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Model-free CodeHarness HTTP checks against MiniOJ"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_problem = subparsers.add_parser(
        "inspect-problem", help="Read one sanitized problem without submitting"
    )
    inspect_problem.add_argument("problem_id")
    inspect_problem.add_argument("--http-timeout", type=float, required=True)

    submit = subparsers.add_parser(
        "submit-fixed", help="Submit a fixed source file and persist HTTP evidence"
    )
    submit.add_argument("problem_id")
    submit.add_argument("source", type=Path)
    submit.add_argument("--workspace-root", type=Path, default=Path("workspace"))
    submit.add_argument("--task-id")
    submit.add_argument("--http-timeout", type=float, required=True)
    submit.add_argument("--poll-interval", type=float, required=True)
    submit.add_argument("--deadline", type=float, required=True)
    submit.add_argument("--skip-feedback", action="store_true")
    submit.add_argument(
        "--confirm-submit",
        action="store_true",
        help="Required acknowledgement that this creates a remote formal submission",
    )
    return parser


def _safe_error(exc: OJClientError) -> str:
    return json.dumps(
        {
            "ok": False,
            "kind": exc.kind.value,
            "message": str(exc),
            "http_status": exc.http_status,
            "request_id": exc.request_id,
            "submission_state_unknown": exc.submission_state_unknown,
            "automatic_retry_allowed": exc.automatic_retry_allowed,
        },
        ensure_ascii=False,
        indent=2,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv(override=False)
    args = build_parser().parse_args(argv)
    settings = ClientSettings.from_environment()
    if not math.isfinite(args.http_timeout) or args.http_timeout <= 0:
        raise SystemExit("--http-timeout must be positive")

    if args.command == "submit-fixed" and not args.confirm_submit:
        raise SystemExit("submit-fixed requires --confirm-submit")
    if args.command == "submit-fixed" and (
        not math.isfinite(args.poll_interval)
        or not math.isfinite(args.deadline)
        or args.poll_interval < 0
        or args.deadline < 0
    ):
        raise SystemExit("--poll-interval and --deadline must be finite and non-negative")

    try:
        with OJClient(
            settings.oj_base_url,
            settings.oj_api_token,
            timeout_seconds=args.http_timeout,
        ) as client:
            if args.command == "inspect-problem":
                problem = client.get_problem(args.problem_id)
                print(json.dumps(problem.as_dict(), ensure_ascii=False, indent=2))
                return 0

            source_code = args.source.read_text(encoding="utf-8")
            task_id = args.task_id or f"http_{uuid.uuid4().hex[:12]}"
            workspace = TaskWorkspace.create(
                args.workspace_root,
                task_id,
                args.problem_id,
                "phase1-http",
            )
            result = run_fixed_solution(
                client,
                workspace,
                source_code,
                timeout_seconds=args.deadline,
                poll_interval_seconds=args.poll_interval,
                fetch_feedback=not args.skip_feedback,
            )
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
            return 0
    except OJClientError as exc:
        print(_safe_error(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
