from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

POLICY_FIELDS = {"max_cost_cny", "max_llm_calls", "max_submissions"}
TIME_FIELDS = {"http_timeout", "poll_interval", "deadline"}


@dataclass(frozen=True)
class DashboardSettings:
    workspace_root: Path = Path("workspace")
    host: str = "127.0.0.1"
    port: int = 8765
    model_config: Optional[Path] = None
    env_file: Optional[Path] = Path(".env")
    enable_launch: bool = False
    harness_config: Optional[Path] = Path("config/harness.yaml")
    http_timeout: float = 15
    poll_interval: float = 1
    deadline: float = 120
    max_cost_cny: Optional[float] = None
    expected_feedback_mode: str = "verdict_only"
    sample_config: Optional[Path] = None

    def __post_init__(self):
        try:
            local = self.host == "localhost" or ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError("Dashboard V1 supports loopback hosts only")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        import math
        for value, positive in ((self.http_timeout, True), (self.poll_interval, False), (self.deadline, False)):
            if isinstance(value, bool) or not math.isfinite(value) or value < 0 or (positive and value == 0):
                raise ValueError("Invalid Dashboard execution time limit")
        if self.max_cost_cny is not None:
            from agent.core.harness import HarnessPolicy
            HarnessPolicy(max_cost_cny=self.max_cost_cny)
        if self.expected_feedback_mode not in {"full", "verdict_only"}:
            raise ValueError("Expected feedback mode must be full or verdict_only")

    def launch_policy(self):
        from agent.core.harness import HarnessPolicy
        policy = HarnessPolicy.from_yaml(self.harness_config)
        return replace(policy, max_cost_cny=self.max_cost_cny) if self.max_cost_cny is not None else policy

    def task_defaults(self):
        """Local display defaults; a broken policy still fails at launch."""
        from agent.core.harness import HarnessPolicy
        try:
            policy = self.launch_policy()
        except (OSError, ValueError, TypeError, OverflowError):
            policy = HarnessPolicy(max_cost_cny=self.max_cost_cny or 1.0)
        return {**{name: getattr(policy, name) for name in POLICY_FIELDS},
                **{name: getattr(self, name) for name in TIME_FIELDS}}

    def task_request(self, payload):
        """Apply only allowlisted new-task settings, never server config paths."""
        from agent.execution import RunRequest
        overrides = payload.get("task_config", {})
        if not isinstance(overrides, dict) or set(overrides) - (POLICY_FIELDS | TIME_FIELDS):
            raise ValueError("Unsupported task configuration")
        if "max_cost_cny" in payload and "task_config" in payload:
            raise ValueError("Choose task_config or the legacy cost field, not both")
        overrides = dict(overrides)
        if "max_cost_cny" in payload:
            overrides["max_cost_cny"] = payload["max_cost_cny"]
        policy = replace(self.launch_policy(), **{name: value for name, value in overrides.items()
                                                  if name in POLICY_FIELDS})
        mode = payload.get("mode", "harness-loop")
        if mode == "code-only":
            if any(name in overrides and overrides[name] != 1
                   for name in ("max_llm_calls", "max_submissions")):
                raise ValueError("code-only permits one model call and at most one submission")
            policy = replace(policy, max_llm_calls=1, max_submissions=1)
        times = {name: overrides.get(name, getattr(self, name)) for name in TIME_FIELDS}
        from agent.core.checker import SampleGatePolicy
        import yaml
        sample_policy = (SampleGatePolicy.from_dict(yaml.safe_load(self.sample_config.read_text()))
                         if self.sample_config else SampleGatePolicy())
        return RunRequest(payload.get("problem_id"), mode, payload.get("profile"),
                          policy=policy, **times, expected_feedback_mode=self.expected_feedback_mode,
                          require_feedback_mode=True, sample_checking=sample_policy.as_dict())

    def contest_request(self, payload):
        from experiments.contest import ContestRequest
        # Reuse the same allowlisted task settings. Browser-supplied problem lists,
        # provider paths, scoring policies and Role overrides are not accepted.
        run = self.task_request({**payload, "problem_id": "contest"})
        return ContestRequest(payload.get("contest_id"), run, payload.get("total_cost_cny", 1.0))

    @classmethod
    def load(cls, workspace_root: Path, host: str, port: int, model_config: Optional[Path],
             env_file: Optional[Path] = Path(".env")):
        values = {}
        if env_file is not None:
            try:
                values = dotenv_values(env_file, interpolate=False)
            except (OSError, ValueError):
                pass
        selected = model_config or os.environ.get("MODEL_CONFIG") or values.get("MODEL_CONFIG")
        return cls(workspace_root, host, port, Path(selected) if selected else None, env_file)
