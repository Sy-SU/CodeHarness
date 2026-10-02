"""Guarded, model-only Phase 2 integration probe."""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Sequence

from dotenv import load_dotenv

from agent.workspace.task import TaskWorkspace

from .registry import ModelRegistry
from .router import ModelRouter
from .runtime import ModelCallFailed, ModelCallRuntime
from .types import AgentRole, ChatMessage, ModelProfile


PROBE_TEXT = "Reply with exactly CODEHARNESS_MODEL_OK and nothing else."
EXPECTED_TEXT = "CODEHARNESS_MODEL_OK"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded CodeHarness model API checks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser("probe", help="Make one short model call and persist evidence")
    probe.add_argument("--profile", choices=[item.value for item in ModelProfile], required=True)
    probe.add_argument("--model-config", type=Path)
    probe.add_argument("--workspace-root", type=Path, default=Path("workspace"))
    probe.add_argument("--task-id")
    probe.add_argument(
        "--confirm-call",
        action="store_true",
        help="Acknowledge that this command makes one real model API call",
    )
    return parser


def _result(workspace: TaskWorkspace, response=None, *, matched: bool = False):
    state = workspace.state
    value = {
        "task_id": state.task_id,
        "status": response.status.value if response is not None else "failed",
        "profile": state.current_model_profile,
        "provider": response.provider if response is not None else None,
        "model": response.model if response is not None else None,
        "request_id": response.request_id if response is not None else None,
        "usage": asdict(response.usage) if response is not None and response.usage else None,
        "usage_available": bool(response is not None and response.usage is not None),
        "latency_ms": response.latency_ms if response is not None else None,
        "matched_expected_text": matched,
        "cost_estimate_status": state.cost_estimate_status,
        "estimated_cost": state.estimated_cost,
        "cost_currency": state.cost_currency,
        "workspace": str(workspace.root),
    }
    if response is not None and response.error is not None:
        value["error"] = asdict(response.error)
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_dotenv(override=False)
    args = build_parser().parse_args(argv)
    if args.command == "probe" and not args.confirm_call:
        raise SystemExit("probe requires --confirm-call")
    config_path = args.model_config or Path(
        os.environ.get("MODEL_CONFIG") or "config/models.yaml"
    )
    registry = ModelRegistry.from_yaml(config_path)
    router = ModelRouter(registry)
    task_id = args.task_id or f"model_probe_{uuid.uuid4().hex[:12]}"
    workspace = TaskWorkspace.create(
        args.workspace_root,
        task_id,
        "model-probe",
        "phase2-model-probe",
        trace_schema_version="phase2-v1",
    )
    workspace.state.current_phase = "MODEL_PROBE"
    workspace.state.experiment_variant = args.profile
    workspace.save_state()
    workspace.trace.append(
        "STATE_CHANGE",
        {"from": "PLAN", "to": "MODEL_PROBE"},
    )
    runtime = ModelCallRuntime(router, workspace)
    started = time.monotonic()
    try:
        response = runtime.complete(
            AgentRole.CODE,
            ModelProfile(args.profile),
            [
                ChatMessage("system", "You are performing a minimal connectivity check."),
                ChatMessage("user", PROBE_TEXT),
            ],
        )
        matched = response.content.strip() == EXPECTED_TEXT
        value = _result(workspace, response, matched=matched)
        exit_code = 0 if matched else 3
    except ModelCallFailed as exc:
        response = exc.response
        value = _result(workspace, response)
        exit_code = 2
    workspace.state.current_phase = "DONE"
    workspace.state.wall_clock_seconds = round(time.monotonic() - started, 3)
    workspace.save_state()
    workspace.trace.append(
        "STATE_CHANGE",
        {"from": "MODEL_PROBE", "to": "DONE"},
    )
    workspace.write_json("artifacts/model-probe.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
