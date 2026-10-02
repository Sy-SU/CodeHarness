"""Shared bounded single-task entry for experiments and local Dashboard jobs."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

from agent.config import ClientSettings
from agent.core.agent import CodingAgent, AgentTerminalStatus
from agent.core.context import ContextBuilder
from agent.core.checker import SampleGatePolicy
from agent.core.harness import HarnessPolicy
from agent.core.policy import ModelPolicy
from agent.core.formal_dedup import FORMAL_DEDUP_POLICY
from agent.models.registry import ModelRegistry
from agent.models.router import ModelRouter
from agent.models.types import AgentRole, ModelProfile
from agent.models.evidence import model_evidence
from agent.oj_client.client import OJClient, OJClientError
from agent.oj_client.feedback import VERDICT_ONLY_POLICY, compatible_feedback
from agent.tools.runtime import build_default_tools
from agent.workspace.task import TaskWorkspace


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class RunRequest:
    problem_id: str
    mode: str = "harness-loop"
    profile: Optional[str] = None
    roles: Dict[str, str] = field(default_factory=dict)
    policy: HarnessPolicy = field(default_factory=HarnessPolicy)
    http_timeout: float = 15
    poll_interval: float = 1
    deadline: float = 120
    expected_feedback_mode: Optional[str] = "verdict_only"
    require_feedback_mode: bool = True
    send_custom_run_code_alias: bool = False
    contest_id: Optional[str] = None
    sample_checking: Optional[dict] = field(default_factory=lambda: SampleGatePolicy().as_dict())

    def __post_init__(self):
        if self.sample_checking is not None:
            SampleGatePolicy.from_dict(self.sample_checking)
        if self.contest_id is not None:
            from agent.oj_client.contests import contest_identifier
            object.__setattr__(self, "contest_id", contest_identifier(self.contest_id))
        if (not isinstance(self.problem_id, str) or not self.problem_id.strip()
                or len(self.problem_id) > 160 or any(ord(c) < 32 for c in self.problem_id)):
            raise ValueError("Invalid problem ID")
        object.__setattr__(self, "problem_id", self.problem_id.strip())
        if self.mode not in {"code-only", "harness-loop"}:
            raise ValueError("Invalid mode")
        if self.profile is not None:
            ModelProfile(self.profile)
        if self.mode == "code-only" and (self.profile is None or self.roles):
            raise ValueError("code-only requires one Profile and no Role overrides")
        if self.profile is not None and self.roles:
            raise ValueError("Choose a fixed Profile or explicit mixed Roles, not both")
        if not isinstance(self.roles, dict):
            raise ValueError("roles must be a mapping")
        for role, profile in self.roles.items():
            AgentRole(role)
            ModelProfile(profile)
        for value, positive in ((self.http_timeout, True), (self.poll_interval, False), (self.deadline, False)):
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or value < 0 or (positive and value == 0)):
                raise ValueError("HTTP/polling limits must be finite and nonnegative; HTTP timeout positive")
        if self.expected_feedback_mode not in {None, "full", "verdict_only"}:
            raise ValueError("Expected feedback mode must be full, verdict_only or null")
        if not isinstance(self.require_feedback_mode, bool) or not isinstance(self.send_custom_run_code_alias, bool):
            raise ValueError("Feedback/compatibility flags must be booleans")


class ExecutionService:
    def __init__(self, settings: ClientSettings, workspace_root: Path, *, registry=None,
                 base_policy=None, client_factory=None):
        self.settings, self.workspace_root = settings, Path(workspace_root)
        self.registry = registry or ModelRegistry.from_yaml(settings.model_config)
        self.base_policy = base_policy or ModelPolicy.from_yaml(settings.model_config)
        self.router = ModelRouter(self.registry)
        self.client_factory = client_factory or OJClient

    def model_policy(self, request):
        mapping = dict(self.base_policy.mapping)
        if request.profile:
            mapping = {role: ModelProfile(request.profile) for role in AgentRole}
        mapping.update({AgentRole(role): ModelProfile(profile) for role, profile in request.roles.items()})
        return ModelPolicy(mapping)  # escalation deliberately disabled

    def snapshot(self, request):
        policy = self.model_policy(request)
        roles = (AgentRole.CODE,) if request.mode == "code-only" else (
            AgentRole.PLAN, AgentRole.CODE, AgentRole.DEBUG)
        routes = {}
        for role in roles:
            profile = policy.mapping[role]
            definition = self.router.route(profile)
            provider = self.registry.provider(definition.provider)
            routes[role.value] = {"profile": profile.value, **asdict(definition),
                "provider_endpoint_sha256": fingerprint(getattr(provider, "base_url", "test-double")),
                "provider_timeout_seconds": getattr(provider, "timeout_seconds", None)}
            if hasattr(provider, "transport_metadata"):
                routes[role.value]["provider_transport"] = provider.transport_metadata
        snapshot = {"schema_version": "phase5-v1", "mode": request.mode, "routes": routes,
            "role_mapping": {r.value: p.value for r, p in policy.mapping.items()},
            "model_escalation": False, "test_generation": False,
            "sample_policy": "disabled" if request.mode == "code-only" else "all_public_whitespace_tokens",
            "review": "disabled" if request.mode == "code-only" else "local_read_only_after_ac",
            "budget": asdict(request.policy), "http_timeout": request.http_timeout,
            "poll_interval": request.poll_interval, "deadline": request.deadline,
            "expected_feedback_mode": request.expected_feedback_mode,
            "require_feedback_mode": request.require_feedback_mode,
            "custom_run_code_alias": request.send_custom_run_code_alias,
            "oj_endpoint_sha256": fingerprint(self.settings.oj_base_url)}
        # Version this semantic change. Old verdict-only checkpoints must not
        # silently gain compatibility/filtering; explicit full stays unchanged.
        if request.expected_feedback_mode == "verdict_only":
            snapshot["feedback_policy"] = VERDICT_ONLY_POLICY
        if request.mode == "harness-loop":
            snapshot["formal_submission_dedup"] = dict(FORMAL_DEDUP_POLICY)
        if request.contest_id is not None:
            snapshot["contest_id"] = request.contest_id
        if request.sample_checking is not None:
            snapshot["sample_checking"] = request.sample_checking
            if request.mode == "harness-loop":
                snapshot["sample_policy"] = "sample_check_v1"
            snapshot["model_configuration_evidence"] = {
                role: {**model_evidence(self.router.route(policy.mapping[AgentRole(role)])),
                       "endpoint_fingerprint": route["provider_endpoint_sha256"]}
                for role, route in routes.items()}
        return snapshot

    def create_workspace(self, request, task_id, *, experiment_id=None, strategy=None):
        snapshot = self.snapshot(request)
        workspace = TaskWorkspace.create(self.workspace_root, task_id, request.problem_id,
            request.mode, trace_schema_version="phase5-v1")
        workspace.state.current_phase = "QUEUED"
        workspace.state.configuration_fingerprint = fingerprint(snapshot)
        workspace.state.experiment_id, workspace.state.experiment_strategy = experiment_id, strategy
        workspace.state.expected_feedback_mode = request.expected_feedback_mode
        workspace.state.feedback_policy = snapshot.get("feedback_policy")
        workspace.save_state()
        workspace.write_json("artifacts/execution-config.json", snapshot)
        return workspace

    def run(self, request, task_id, *, workspace=None, resume=False, experiment_id=None, strategy=None):
        workspace = workspace or (TaskWorkspace.load(self.workspace_root, task_id) if resume
            else self.create_workspace(request, task_id, experiment_id=experiment_id, strategy=strategy))
        scope = {"contest_id": request.contest_id} if request.contest_id is not None else {}
        with self.client_factory(self.settings.oj_base_url, self.settings.oj_api_token,
                timeout_seconds=request.http_timeout,
                send_custom_run_code_alias=request.send_custom_run_code_alias, **scope) as client:
            agent = CodingAgent(self.router, self.model_policy(request), ContextBuilder(),
                build_default_tools(client, workspace), workspace,
                poll_seconds=request.poll_interval, judge_timeout_seconds=request.deadline,
                sample_checking=request.sample_checking)
            snapshot = self.snapshot(request)
            if (workspace.state.configuration_fingerprint != fingerprint(snapshot)
                    or workspace.state.feedback_policy != snapshot.get("feedback_policy")):
                raise ValueError("Execution configuration differs from the saved task")
            if resume and workspace.state.feedback_policy == VERDICT_ONLY_POLICY:
                if (workspace.state.actual_feedback_mode not in {"full", "verdict_only"}
                        or workspace.state.effective_feedback_mode != "verdict_only"
                        or workspace.state.feedback_mode_status != "confirmed"):
                    raise ValueError("Saved effective feedback mode differs from the frozen policy")
            blocked = False
            if not resume:
                try:
                    observation = client.get_feedback_mode()
                except OJClientError as exc:
                    observation = {"mode": None, "source": "GET /api/v1/me", "status": exc.kind.value}
                workspace.state.actual_feedback_mode = observation["mode"]
                workspace.state.feedback_mode_source = observation["source"]
                workspace.state.feedback_mode_status = observation["status"]
                policy = workspace.state.feedback_policy
                mismatch = not compatible_feedback(request.expected_feedback_mode, observation["mode"], policy)
                workspace.state.effective_feedback_mode = None if mismatch else (
                    "verdict_only" if policy == VERDICT_ONLY_POLICY else observation["mode"])
                if observation["mode"] is None:
                    workspace.state.effective_feedback_mode = None
                workspace.save_state()
                workspace.trace.append("FEEDBACK_MODE_OBSERVED", observation)
                workspace.trace.append("FEEDBACK_MODE_EFFECTIVE", {
                    "actual": observation["mode"], "effective": workspace.state.effective_feedback_mode,
                    "expected": request.expected_feedback_mode, "policy": policy,
                    "downgraded": observation["mode"] == "full"
                        and workspace.state.effective_feedback_mode == "verdict_only"})
                blocked = mismatch or (request.require_feedback_mode and observation["mode"] is None)
            if blocked:
                result = agent._finish(AgentTerminalStatus.CONDITION_MISMATCH,
                                       "feedback_mode_unverified_or_mismatched")
            elif request.mode == "code-only":
                if resume:
                    raise ValueError("Interrupted code-only tasks cannot be regenerated; start an explicit new run")
                with workspace.exclusive_run():
                    result = agent.run_code_only(ModelProfile(request.profile), budget_policy=request.policy)
            else:
                result = agent.run_harness_loop(harness_policy=request.policy, resume=resume)
            state = workspace.state
            uncertain_calls = max(0, state.llm_call_count - state.llm_success_count - state.llm_failure_count)
            unknown_usage = state.llm_usage_missing_count or uncertain_calls
            workspace.write_json("artifacts/result.json", {**asdict(result),
                "input_tokens": None if unknown_usage else result.input_tokens,
                "output_tokens": None if unknown_usage else result.output_tokens,
                "usage_missing_count": state.llm_usage_missing_count,
                "uncertain_llm_calls": uncertain_calls,
                "verdict": result.final_verdict, "submission_attempts": workspace.state.submission_attempt_count,
                "budget_committed_cny": workspace.state.budget_committed_cny,
                "cost_estimate_status": workspace.state.cost_estimate_status,
                "configuration_fingerprint": workspace.state.configuration_fingerprint,
                "expected_feedback_mode": workspace.state.expected_feedback_mode,
                "actual_feedback_mode": workspace.state.actual_feedback_mode,
                "effective_feedback_mode": workspace.state.effective_feedback_mode,
                "feedback_policy": workspace.state.feedback_policy,
                "feedback_mode_status": workspace.state.feedback_mode_status,
                "recovery_metrics": state.recovery_metrics, "sample_checker": state.sample_checker,
                "sample_gate_status": state.sample_gate_status})
            return result

    def close(self):
        for provider in self.registry.providers.values():
            client = getattr(provider, "client", None)
            if client is not None:
                client.close()
