"""Command-line entry point for CodeHarness experiments on the Mac."""

from __future__ import annotations

import argparse
import json
import math
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv

from agent.config import ClientSettings
from agent.core.agent import CodingAgent
from agent.core.context import ContextBuilder
from agent.core.harness import HarnessPolicy
from agent.core.policy import ModelPolicy
from agent.models.registry import ModelRegistry
from agent.models.router import ModelRouter
from agent.models.types import ModelProfile, AgentRole
from agent.oj_client.client import OJClient
from agent.tools.runtime import build_default_tools
from agent.workspace.task import TaskWorkspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CodeHarness single-agent experiments")
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve = subparsers.add_parser(
        "solve", help="Run one bounded, persisted solve task"
    )
    solve.add_argument("problem_id")
    solve.add_argument(
        "--mode",
        choices=("code-only", "harness-loop"),
        required=True,
        help="One-shot generation or the resumable fixed state machine",
    )
    solve.add_argument(
        "--profile",
        choices=[item.value for item in ModelProfile],
        required=False,
    )
    solve.add_argument("--task-id")
    solve.add_argument("--workspace-root", type=Path, default=Path("workspace"))
    solve.add_argument("--model-config")
    solve.add_argument("--harness-config", type=Path)
    solve.add_argument("--sample-config", type=Path, help="Explicit sample checker/gate policy YAML")
    solve.add_argument("--max-cost-cny", type=float,
                       help="Per-task cost limit in CNY (default: policy setting, otherwise 1)")
    solve.add_argument("--send-custom-run-code-alias", action="store_true")
    solve.add_argument("--http-timeout", type=float, required=True)
    solve.add_argument("--poll-interval", type=float, required=True)
    solve.add_argument("--deadline", type=float, required=True)
    solve.add_argument(
        "--confirm-model-call",
        action="store_true",
        help="Acknowledge model calls within the selected mode's budget",
    )
    solve.add_argument(
        "--confirm-submit",
        action="store_true",
        help="Acknowledge formal submissions within the selected mode's budget",
    )

    resume = subparsers.add_parser("resume", help="Resume one saved harness-loop task")
    resume.add_argument("task_id")
    resume.add_argument("--workspace-root", type=Path, default=Path("workspace"))
    resume.add_argument("--model-config")
    resume.add_argument("--harness-config", type=Path)
    resume.add_argument("--sample-config", type=Path, help="Must match the saved checker policy")
    resume.add_argument("--max-cost-cny", type=float,
                        help="Must match the saved task budget; does not grant a fresh allowance")
    resume.add_argument("--send-custom-run-code-alias", action="store_true")
    resume.add_argument("--http-timeout", type=float, required=True)
    resume.add_argument("--poll-interval", type=float, required=True)
    resume.add_argument("--deadline", type=float, required=True)
    resume.add_argument("--confirm-model-call", action="store_true")
    resume.add_argument("--confirm-submit", action="store_true")

    # Compatibility aliases retained for the pre-Phase-3 prototype. New runs
    # should use `solve`, whose remote effects and time limits are explicit.
    for mode in ("code-only", "harness-loop"):
        command = subparsers.add_parser(
            mode,
            help=f"Compatibility entry for the pre-Phase-3 {mode} command",
        )
        command.add_argument("problem_id")
        command.add_argument("--task-id")
        command.add_argument("--workspace-root", default="workspace")
        command.add_argument("--model-config")
        command.add_argument("--max-cost-cny", type=float,
                             help="Per-task cost limit in CNY (default: 1)")
        command.add_argument("--judge-timeout", type=float, default=300)
        if mode == "code-only":
            command.add_argument(
                "--profile", choices=[item.value for item in ModelProfile], default="standard"
            )
        else:
            command.add_argument("--max-attempts", type=int, default=10)
    return parser


def _validate_solve_arguments(args: argparse.Namespace) -> None:
    if not args.confirm_model_call:
        raise SystemExit("solve requires --confirm-model-call")
    if not args.confirm_submit:
        raise SystemExit("solve requires --confirm-submit")
    if getattr(args, "mode", None) == "code-only" and args.profile is None:
        raise SystemExit("solve --mode code-only requires --profile")
    if not math.isfinite(args.http_timeout) or args.http_timeout <= 0:
        raise SystemExit("--http-timeout must be finite and positive")
    if (
        not math.isfinite(args.poll_interval)
        or not math.isfinite(args.deadline)
        or args.poll_interval < 0
        or args.deadline < 0
    ):
        raise SystemExit("--poll-interval and --deadline must be finite and non-negative")


def _exit_code(result) -> int:
    if result.solved:
        return 0
    if result.terminal_status == "user_program_failure":
        return 1
    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv(override=False)
    args = build_parser().parse_args(argv)
    if args.max_cost_cny is not None:
        try:
            HarnessPolicy(max_cost_cny=args.max_cost_cny)
        except ValueError as exc:
            raise SystemExit(str(exc))
    explicit = args.command in {"solve", "resume"}
    if explicit:
        _validate_solve_arguments(args)
    settings = ClientSettings.from_environment(model_config_override=args.model_config)
    task_id = args.task_id or f"task_{uuid.uuid4().hex[:12]}"
    mode = "harness-loop" if args.command == "resume" else (
        args.mode if args.command == "solve" else args.command
    )
    trace_schema_version = "phase3-v1" if mode == "code-only" else "phase4-v1"
    registry = ModelRegistry.from_yaml(settings.model_config)
    model_policy = ModelPolicy.from_yaml(settings.model_config)
    if args.command == "solve" and mode == "harness-loop" and args.profile:
        model_policy = ModelPolicy({role: ModelProfile(args.profile) for role in AgentRole})
    if args.command == "resume":
        workspace = TaskWorkspace.load(Path(args.workspace_root), task_id)
        checkpoint = workspace.read_json("checkpoint.json")
        if not getattr(args, "harness_config", None):
            harness_policy = HarnessPolicy(**checkpoint["config"]["policy"])
        else:
            harness_policy = HarnessPolicy.from_yaml(args.harness_config)
        if args.max_cost_cny is not None:
            harness_policy = replace(harness_policy, max_cost_cny=args.max_cost_cny)
        if asdict(harness_policy) != checkpoint["config"]["policy"]:
            raise SystemExit("Resume budget/policy differs from the saved task; start a new task instead")
        # Restore the saved base roles, then verify actual models/prices against config.
        saved_roles = checkpoint["config"]["routes"]
        model_policy = ModelPolicy({
            role: ModelProfile(saved_roles[role.value]["profile"])
            if role.value in saved_roles else model_policy.mapping[role]
            for role in AgentRole
        })
    else:
        harness_policy = HarnessPolicy.from_yaml(getattr(args, "harness_config", None))
        if args.command == "harness-loop":
            harness_policy = HarnessPolicy(max_submissions=args.max_attempts)
        if args.max_cost_cny is not None:
            harness_policy = replace(harness_policy, max_cost_cny=args.max_cost_cny)
        workspace = TaskWorkspace.create(
            Path(args.workspace_root),
            task_id,
            args.problem_id,
            mode,
            trace_schema_version=trace_schema_version,
        )
    http_timeout = args.http_timeout if explicit else 120
    poll_interval = args.poll_interval if explicit else 1
    judge_timeout = args.deadline if explicit else args.judge_timeout
    with OJClient(
        settings.oj_base_url,
        settings.oj_api_token,
        timeout_seconds=http_timeout,
        send_custom_run_code_alias=getattr(args, "send_custom_run_code_alias", False),
    ) as client:
        tools = build_default_tools(client, workspace)
        from agent.core.checker import SampleGatePolicy
        import yaml
        sample_path = getattr(args, "sample_config", None)
        if sample_path:
            sample_policy = SampleGatePolicy.from_dict(yaml.safe_load(sample_path.read_text()))
        elif args.command == "resume":
            value = checkpoint["config"].get("sample_checking")
            sample_policy = SampleGatePolicy.from_dict(value) if value is not None else None
        else:
            sample_policy = SampleGatePolicy()
        agent = CodingAgent(
            ModelRouter(registry),
            model_policy,
            ContextBuilder(),
            tools,
            workspace,
            poll_seconds=poll_interval,
            judge_timeout_seconds=judge_timeout,
            sample_checking=sample_policy,
        )
        if mode == "code-only":
            result = agent.run_code_only(ModelProfile(args.profile), budget_policy=harness_policy)
        else:
            result = agent.run_harness_loop(harness_policy=harness_policy, resume=args.command == "resume")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return _exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
