"""Code-only baseline and iterative single-agent harness."""

from __future__ import annotations

import enum
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from agent.models.router import ModelRouter
from agent.models.runtime import ModelCallFailed, ModelCallRuntime
from agent.models.types import AgentRole, LLMResponse, ModelProfile
from agent.oj_client.client import OJClientError
from agent.oj_client.types import AgentProblem, SubmissionRecord
from agent.tools.runtime import ToolRuntime
from agent.workspace.task import TaskWorkspace

from .context import ContextBuilder
from .policy import ModelPolicy
from .checker import SampleGatePolicy


CODE_BLOCK = re.compile(r"```(?:cpp|c\+\+|cc)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
DEFAULT_SAMPLE_CHECKING = SampleGatePolicy()


class AgentTerminalStatus(str, enum.Enum):
    ACCEPTED = "accepted"
    USER_PROGRAM_FAILURE = "user_program_failure"
    REMOTE_INFRASTRUCTURE_FAILURE = "remote_infrastructure_failure"
    MODEL_FAILURE = "model_failure"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    CLIENT_FAILURE = "client_failure"
    RESULT_UNKNOWN = "result_unknown"
    INTERNAL_FAILURE = "internal_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BUDGET_UNVERIFIABLE = "budget_unverifiable"
    CONTEXT_LIMIT = "context_limit"
    CONDITION_MISMATCH = "condition_mismatch"
    SAMPLE_CHECK_UNVERIFIABLE = "sample_check_unverifiable"


@dataclass(frozen=True)
class AgentResult:
    task_id: str
    problem_id: str
    mode: str
    solved: bool
    final_verdict: Optional[str]
    submissions: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost: Optional[float]
    wall_clock_seconds: float
    workspace: str
    terminal_status: str
    termination_reason: str
    error_kind: Optional[str]
    submission_id: Optional[str]
    solution_version: Optional[str]
    solution_sha256: Optional[str]
    recovery_metrics: dict = field(default_factory=dict)
    sample_gate_status: Optional[str] = None
    sample_checker: dict = field(default_factory=dict)


def extract_cpp(response: str) -> str:
    """Extract one complete fenced C++ program with a plain-text fallback."""

    match = CODE_BLOCK.search(response)
    code = match.group(1).strip() if match else response.strip()
    if "#include" not in code or "main" not in code:
        raise ValueError("Model response did not contain a complete C++20 program")
    return code + "\n"


def problem_markdown(problem: AgentProblem) -> str:
    value = problem.as_dict()
    lines = [f"# {problem.title}", "", problem.statement, "", "## Input", problem.input_specification]
    lines += ["", "## Output", problem.output_specification, "", "## Limits"]
    lines += [f"- Time: {problem.limits.time_ms} ms", f"- Memory: {problem.limits.memory_mb} MB"]
    for index, sample in enumerate(value["samples"], 1):
        lines += ["", f"## Sample {index}", "", "```text", sample["input"], "```", "```text", sample["output"], "```"]
    return "\n".join(lines) + "\n"


class CodingAgent:
    """One sequential agent; no sub-agent or hidden execution access."""

    def __init__(
        self,
        router: ModelRouter,
        policy: ModelPolicy,
        context_builder: ContextBuilder,
        tools: ToolRuntime,
        workspace: TaskWorkspace,
        *,
        poll_seconds: float = 1.0,
        judge_timeout_seconds: float = 300,
        sample_checking=DEFAULT_SAMPLE_CHECKING,
    ):
        self.router = router
        self.model_runtime = ModelCallRuntime(router, workspace)
        self.policy = policy
        self.context_builder = context_builder
        self.tools = tools
        self.workspace = workspace
        self.poll_seconds = poll_seconds
        self.judge_timeout_seconds = judge_timeout_seconds
        self.started = time.monotonic()
        self.sample_checking = (None if sample_checking is None else sample_checking
            if isinstance(sample_checking, SampleGatePolicy) else SampleGatePolicy.from_dict(sample_checking))
        self.sample_checking_explicit = sample_checking is not DEFAULT_SAMPLE_CHECKING

    def _phase(self, phase: str) -> None:
        previous = self.workspace.state.current_phase
        self.workspace.state.current_phase = phase
        self.workspace.save_state()
        self.workspace.trace.append("STATE_CHANGE", {"from": previous, "to": phase})

    def _model_call(
        self,
        role: AgentRole,
        messages,
        *,
        profile_override: Optional[ModelProfile] = None,
        prior_failures: int = 0,
        failure_kind: Optional[str] = None,
    ) -> LLMResponse:
        profile = profile_override or self.policy.choose(
            role,
            prior_failures=prior_failures,
            failure_kind=failure_kind,
        )
        prior_profile = self.workspace.state.current_model_profile
        if prior_profile and prior_profile != profile.value and role is AgentRole.DEBUG:
            self.workspace.trace.append(
                "MODEL_ESCALATION", {"from": prior_profile, "to": profile.value, "role": role.value}
            )
        return self.model_runtime.complete(role, profile, messages)

    def _submit_and_wait(self, problem_id: str, code: str) -> SubmissionRecord:
        state = self.workspace.state
        state.submission_attempt_count += 1
        self.workspace.save_state()
        created = self.tools.call("submit_solution", problem_id=problem_id, code=code)
        submission_id = created.submission_id
        state.submission_count += 1
        state.last_submission_id = submission_id
        state.last_submission_solution_version = state.solution_version
        state.last_submission_code_sha256 = state.solution_sha256
        self.workspace.write_json("artifacts/submission-created.json", created.as_dict())
        self.workspace.trace.append(
            "SUBMISSION",
            {
                "submission_id": submission_id,
                "status": created.status,
                "solution_version": state.solution_version,
                "code_sha256": state.solution_sha256,
                "model_call_id": state.solution_model_call_id,
            },
            correlation_id=submission_id,
        )
        self.workspace.save_state()
        self._phase("WAIT_FOR_JUDGE")
        value = self.tools.call(
            "wait_for_submission",
            submission_id=submission_id,
            timeout_seconds=self.judge_timeout_seconds,
            poll_interval_seconds=self.poll_seconds,
        )
        state.last_verdict = value.verdict
        state.last_outcome_kind = (
            value.outcome_kind.value if value.outcome_kind is not None else None
        )
        self.workspace.write_json("artifacts/submission-final.json", value.as_dict())
        self.workspace.trace.append(
            "JUDGE_RESULT",
            {
                "submission_id": submission_id,
                "verdict": value.verdict,
                "outcome_kind": state.last_outcome_kind,
                "solution_version": state.solution_version,
                "code_sha256": state.solution_sha256,
                "model_call_id": state.solution_model_call_id,
            },
            correlation_id=submission_id,
        )
        self.workspace.save_state()
        return value

    def _record_solution(self, code: str) -> None:
        state = self.workspace.state
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        self.tools.call("write_file", relative="solution.cpp", content=code)
        state.attempt_count = 1
        state.solution_version = "solution-v1"
        state.solution_sha256 = digest
        state.solution_model_call_id = state.last_model_call_id
        self.workspace.write_json(
            "artifacts/code-version.json",
            {
                "solution_version": state.solution_version,
                "code_sha256": digest,
                "model_call_id": state.solution_model_call_id,
            },
        )
        self.workspace.trace.append(
            "CODE_VERSION",
            {
                "solution_version": state.solution_version,
                "code_sha256": digest,
                "model_call_id": state.solution_model_call_id,
            },
            correlation_id=state.last_model_call_id,
        )
        self.workspace.save_state()

    def _finish(
        self,
        terminal_status: Optional[AgentTerminalStatus] = None,
        termination_reason: Optional[str] = None,
        *,
        error_kind: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> AgentResult:
        state = self.workspace.state
        if terminal_status is None:
            if state.last_verdict == "AC":
                terminal_status = AgentTerminalStatus.ACCEPTED
                termination_reason = termination_reason or "judge_accepted"
            elif state.last_verdict == "IE":
                terminal_status = AgentTerminalStatus.REMOTE_INFRASTRUCTURE_FAILURE
                termination_reason = termination_reason or "judge_ie"
            else:
                terminal_status = AgentTerminalStatus.USER_PROGRAM_FAILURE
                termination_reason = termination_reason or "judge_rejected"
        state.terminal_status = terminal_status.value
        state.termination_reason = termination_reason or terminal_status.value
        state.error_kind = error_kind
        state.wall_clock_seconds = round(time.monotonic() - self.started, 3)
        state.solved = terminal_status is AgentTerminalStatus.ACCEPTED
        self._phase("DONE")
        self.workspace.save_state()
        self.workspace.trace.append(
            "TASK_TERMINATED",
            {
                "terminal_status": state.terminal_status,
                "termination_reason": state.termination_reason,
                "error_kind": state.error_kind,
                "solved": state.solved,
                "verdict": state.last_verdict,
                "wall_clock_seconds": state.wall_clock_seconds,
            },
        )
        from .metrics import workspace_metrics
        from dataclasses import asdict
        state.recovery_metrics = workspace_metrics(self.workspace, asdict(state))
        self.workspace.save_state()
        self.workspace.trace.append("RECOVERY_METRICS", state.recovery_metrics)
        result = AgentResult(
            task_id=state.task_id,
            problem_id=state.problem_id,
            mode=state.mode,
            solved=state.solved,
            final_verdict=state.last_verdict,
            submissions=state.submission_count,
            llm_calls=state.llm_call_count,
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
            estimated_cost=state.estimated_cost,
            wall_clock_seconds=state.wall_clock_seconds,
            workspace=str(self.workspace.root),
            terminal_status=state.terminal_status,
            termination_reason=state.termination_reason,
            error_kind=state.error_kind,
            submission_id=state.last_submission_id,
            solution_version=state.solution_version,
            solution_sha256=state.solution_sha256,
            recovery_metrics=state.recovery_metrics, sample_gate_status=state.sample_gate_status,
            sample_checker=state.sample_checker,
        )
        self.workspace.write_json(
            "artifacts/result.json",
            {
                "task_id": result.task_id,
                "problem_id": result.problem_id,
                "mode": result.mode,
                "terminal_status": result.terminal_status,
                "termination_reason": result.termination_reason,
                "error_kind": result.error_kind,
                "error_message": error_message,
                "solved": result.solved,
                "verdict": result.final_verdict,
                "llm_calls": result.llm_calls,
                "submission_attempts": state.submission_attempt_count,
                "submissions": result.submissions,
                "submission_id": result.submission_id,
                "solution_version": result.solution_version,
                "solution_sha256": result.solution_sha256,
                "model_call_id": state.solution_model_call_id,
                "recovery_metrics": state.recovery_metrics,
                "sample_gate_status": state.sample_gate_status,
            },
        )
        return result

    def _finish_client_failure(self, stage: str, exc: Exception) -> AgentResult:
        if isinstance(exc, OJClientError):
            status = (
                AgentTerminalStatus.RESULT_UNKNOWN
                if exc.kind.value == "result_unknown"
                else AgentTerminalStatus.CLIENT_FAILURE
            )
            return self._finish(
                status,
                f"{stage}_{exc.kind.value}",
                error_kind=exc.kind.value,
                error_message=str(exc),
            )
        return self._finish(
            AgentTerminalStatus.INTERNAL_FAILURE,
            f"{stage}_unexpected_error",
            error_kind=type(exc).__name__,
            error_message=f"Unexpected {stage} failure: {type(exc).__name__}",
        )

    def run_code_only(self, profile: ModelProfile, *, budget_policy=None) -> AgentResult:
        """Exactly one generation and one submission, with no feedback or retry."""

        self.workspace.state.experiment_variant = profile.value
        self._phase("FETCH_PROBLEM")
        try:
            problem = self.tools.call(
                "get_problem", problem_id=self.workspace.state.problem_id
            )
            self.workspace.write_json("problem.json", problem.as_dict())
            self.workspace.write_text("problem.md", problem_markdown(problem))
        except Exception as exc:
            return self._finish_client_failure("problem_fetch", exc)

        self._phase("CODE")
        messages = self.context_builder.build(problem=problem.as_dict(), role=AgentRole.CODE)
        from .budget import BudgetStopped, reserve_model_cost, validate_usage
        try:
            if budget_policy is not None:
                input_limit, output_limit, reserved = reserve_model_cost(
                    self.router.route(profile), messages, self.workspace.state,
                    budget_policy.max_cost_cny)
                self.workspace.save_state()
                self.workspace.trace.append("BUDGET_RESERVATION", {
                    "role": "CODE", "profile": profile.value, "reserved_cny": reserved,
                    "committed_cny": self.workspace.state.budget_committed_cny,
                    "max_cost_cny": budget_policy.max_cost_cny,
                })
            response = self._model_call(AgentRole.CODE, messages, profile_override=profile)
            if budget_policy is not None:
                validate_usage(response, input_limit, output_limit)
        except BudgetStopped as exc:
            return self._finish(AgentTerminalStatus(exc.status), exc.reason, error_kind=exc.status)
        except ModelCallFailed as exc:
            error = exc.response.error
            error_kind = error.kind.value if error is not None else "provider"
            return self._finish(
                AgentTerminalStatus.MODEL_FAILURE,
                f"model_{error_kind}",
                error_kind=error_kind,
                error_message=str(exc),
            )
        except Exception as exc:
            return self._finish(
                AgentTerminalStatus.MODEL_FAILURE,
                "model_routing_or_runtime_failure",
                error_kind=type(exc).__name__,
                error_message=f"Unexpected model failure: {type(exc).__name__}",
            )

        self.workspace.write_json(
            "artifacts/model-response.json",
            {
                "model_call_id": self.workspace.state.last_model_call_id,
                "provider": response.provider,
                "model": response.model,
                "profile": response.profile.value,
                "request_id": response.request_id,
                "finish_reason": response.finish_reason,
                "content": response.content,
            },
        )
        try:
            code = extract_cpp(response.content)
        except ValueError as exc:
            return self._finish(
                AgentTerminalStatus.INVALID_MODEL_OUTPUT,
                "code_extraction_failed",
                error_kind="invalid_model_output",
                error_message=str(exc),
            )
        try:
            self._record_solution(code)
        except Exception as exc:
            return self._finish_client_failure("solution_persist", exc)

        self._phase("SUBMIT")
        try:
            judged = self._submit_and_wait(problem.problem_id, code)
        except Exception as exc:
            stage = (
                "judge_poll"
                if self.workspace.state.last_submission_id is not None
                else "submission_creation"
            )
            return self._finish_client_failure(stage, exc)

        if judged.verdict == "AC":
            return self._finish(AgentTerminalStatus.ACCEPTED, "judge_accepted")
        if judged.verdict == "IE":
            return self._finish(
                AgentTerminalStatus.REMOTE_INFRASTRUCTURE_FAILURE,
                "judge_ie",
                error_kind="remote_infrastructure",
            )
        return self._finish(
            AgentTerminalStatus.USER_PROGRAM_FAILURE,
            f"judge_{(judged.verdict or 'unknown').lower()}",
            error_kind="user_program",
        )

    def run_harness_loop(self, max_attempts=None, *, harness_policy=None, resume=False) -> AgentResult:
        from .harness import HarnessLoop, HarnessPolicy

        policy = harness_policy or HarnessPolicy(
            max_submissions=10 if max_attempts is None else max_attempts
        )
        return HarnessLoop(self, policy).run(resume=resume)
