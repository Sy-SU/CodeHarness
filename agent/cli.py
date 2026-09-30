"""Command-line entry point for CodeHarness experiments on the Mac."""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv

from agent.config import ClientSettings
from agent.core.agent import CodingAgent
from agent.core.context import ContextBuilder
from agent.core.policy import ModelPolicy
from agent.models.registry import ModelRegistry
from agent.models.router import ModelRouter
from agent.models.types import ModelProfile
from agent.oj_client.client import OJClient
from agent.tools.runtime import build_default_tools
from agent.workspace.task import TaskWorkspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CodeHarness single-agent experiments")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("code-only", "harness-loop"):
        command = subparsers.add_parser(mode)
        command.add_argument("problem_id")
        command.add_argument("--task-id")
        command.add_argument("--workspace-root", default="workspace")
        command.add_argument("--model-config")
        command.add_argument("--judge-timeout", type=float, default=300)
        if mode == "code-only":
            command.add_argument(
                "--profile", choices=[item.value for item in ModelProfile], default="standard"
            )
        else:
            command.add_argument("--max-attempts", type=int, default=4)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv(override=False)
    args = build_parser().parse_args(argv)
    settings = ClientSettings.from_environment(model_config_override=args.model_config)
    task_id = args.task_id or f"task_{uuid.uuid4().hex[:12]}"
    workspace = TaskWorkspace.create(
        Path(args.workspace_root), task_id, args.problem_id, args.mode
    )
    client = OJClient(settings.oj_base_url, settings.oj_api_token)
    tools = build_default_tools(client, workspace)
    router = ModelRouter(ModelRegistry.from_yaml(settings.model_config))
    agent = CodingAgent(
        router,
        ModelPolicy(),
        ContextBuilder(),
        tools,
        workspace,
        judge_timeout_seconds=args.judge_timeout,
    )
    if args.mode == "code-only":
        result = agent.run_code_only(ModelProfile(args.profile))
    else:
        result = agent.run_harness_loop(max_attempts=max(1, args.max_attempts))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
