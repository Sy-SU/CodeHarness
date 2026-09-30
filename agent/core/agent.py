"""Code-only baseline and iterative single-agent harness."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from agent.models.router import ModelRouter
from agent.models.types import AgentRole, LLMResponse, ModelProfile
from agent.oj_client.types import AgentProblem, JudgeFeedback, SubmissionRecord
from agent.tools.runtime import ToolRuntime
from agent.workspace.task import TaskWorkspace

from .context import ContextBuilder
from .policy import ModelPolicy


CODE_BLOCK = re.compile(r"```(?:cpp|c\+\+|cc)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


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
    estimated_cost: float
    wall_clock_seconds: float
    workspace: str


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
    ):
        self.router = router
        self.policy = policy
        self.context_builder = context_builder
        self.tools = tools
        self.workspace = workspace
        self.poll_seconds = poll_seconds
        self.judge_timeout_seconds = judge_timeout_seconds
        self.started = time.monotonic()

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
    ) -> LLMResponse:
        profile = profile_override or self.policy.choose(role, prior_failures=prior_failures)
        prior_profile = self.workspace.state.current_model_profile
        if prior_profile and prior_profile != profile.value and role is AgentRole.DEBUG:
            self.workspace.trace.append(
                "MODEL_ESCALATION", {"from": prior_profile, "to": profile.value, "role": role.value}
            )
        self.workspace.state.current_model_profile = profile.value
        self.workspace.trace.append(
            "LLM_CALL",
            {"role": role.value, "profile": profile.value, "messages": [asdict(item) for item in messages]},
        )
        response = self.router.complete(profile, messages)
        state = self.workspace.state
        state.llm_call_count += 1
        state.input_tokens += response.usage.input_tokens
        state.output_tokens += response.usage.output_tokens
        state.estimated_cost += self.router.estimate_cost(response)
        state.calls_per_model_profile[profile.value] = state.calls_per_model_profile.get(profile.value, 0) + 1
        self.workspace.trace.append(
            "LLM_RESPONSE",
            {
                "role": role.value,
                "profile": profile.value,
                "provider": response.provider,
                "model": response.model,
                "usage": asdict(response.usage),
                "request_id": response.request_id,
                "content": response.content,
            },
        )
        self.workspace.save_state()
        return response

    def _submit_and_wait(self, problem_id: str, code: str) -> SubmissionRecord:
        created = self.tools.call("submit_solution", problem_id=problem_id, code=code)
        submission_id = created.submission_id
        self.workspace.state.submission_count += 1
        self.workspace.state.last_submission_id = submission_id
        self.workspace.trace.append("SUBMISSION", {"submission_id": submission_id})
        value = self.tools.call(
            "wait_for_submission",
            submission_id=submission_id,
            timeout_seconds=self.judge_timeout_seconds,
            poll_interval_seconds=self.poll_seconds,
        )
        self.workspace.state.last_verdict = value.verdict
        self.workspace.state.last_outcome_kind = (
            value.outcome_kind.value if value.outcome_kind is not None else None
        )
        self.workspace.trace.append(
            "JUDGE_RESULT",
            {
                "submission_id": submission_id,
                "verdict": value.verdict,
                "outcome_kind": self.workspace.state.last_outcome_kind,
            },
        )
        self.workspace.save_state()
        return value

    def _finish(self) -> AgentResult:
        self._phase("DONE")
        state = self.workspace.state
        state.wall_clock_seconds = round(time.monotonic() - self.started, 3)
        state.solved = state.last_verdict == "AC"
        self.workspace.save_state()
        return AgentResult(
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
        )

    def run_code_only(self, profile: ModelProfile) -> AgentResult:
        """Exactly one generation and one submission, with no feedback or retry."""

        self.workspace.state.experiment_variant = profile.value
        problem = self.tools.call("get_problem", problem_id=self.workspace.state.problem_id)
        self.workspace.write_text("problem.md", problem_markdown(problem))
        self._phase("CODE")
        messages = self.context_builder.build(problem=problem.as_dict(), role=AgentRole.CODE)
        response = self._model_call(AgentRole.CODE, messages, profile_override=profile)
        code = extract_cpp(response.content)
        self.tools.call("write_file", relative="solution.cpp", content=code)
        self.workspace.state.attempt_count = 1
        self._submit_and_wait(problem.problem_id, code)
        return self._finish()

    def _run_samples(self, problem: AgentProblem, code: str) -> List[str]:
        outcomes: List[str] = []
        for index, sample in enumerate(problem.samples, 1):
            result = self.tools.call("run_code", code=code, stdin=sample.input)
            outcomes.append(f"Sample {index}: {result.status}")
        return outcomes

    def run_harness_loop(self, max_attempts: int = 4) -> AgentResult:
        self.workspace.state.experiment_variant = "default-policy"
        problem = self.tools.call("get_problem", problem_id=self.workspace.state.problem_id)
        problem_dict = problem.as_dict()
        self.workspace.write_text("problem.md", problem_markdown(problem))

        self._phase("PLAN")
        plan_response = self._model_call(
            AgentRole.PLAN,
            self.context_builder.build(problem=problem_dict, role=AgentRole.PLAN),
        )
        plan = plan_response.content
        self.workspace.write_text("artifacts/plan.md", plan)

        self._phase("CODE")
        code_response = self._model_call(
            AgentRole.CODE,
            self.context_builder.build(problem=problem_dict, role=AgentRole.CODE, plan=plan),
        )
        code = extract_cpp(code_response.content)
        self.tools.call("write_file", relative="solution.cpp", content=code)
        feedback: Optional[JudgeFeedback] = None
        recent: List[str] = []

        for attempt in range(1, max_attempts + 1):
            self.workspace.state.attempt_count = attempt
            self._phase("TEST")
            recent = self._run_samples(problem, code)
            judged = self._submit_and_wait(problem.problem_id, code)
            if judged.verdict == "AC":
                self._phase("REVIEW")
                review = self._model_call(
                    AgentRole.REVIEW,
                    self.context_builder.build(
                        problem=problem_dict,
                        role=AgentRole.REVIEW,
                        current_solution=code,
                        recent_history=recent,
                    ),
                )
                self.workspace.write_text("artifacts/review.md", review.content)
                return self._finish()
            submission_id = judged.submission_id
            feedback = self.tools.call("get_feedback", submission_id=submission_id)
            if attempt == max_attempts:
                break
            self._phase("DEBUG")
            self.workspace.state.debug_iterations += 1
            debug_response = self._model_call(
                AgentRole.DEBUG,
                self.context_builder.build(
                    problem=problem_dict,
                    role=AgentRole.DEBUG,
                    current_solution=code,
                    plan=plan,
                    feedback=feedback.as_dict() if feedback is not None else None,
                    recent_history=recent,
                ),
                prior_failures=attempt,
            )
            code = extract_cpp(debug_response.content)
            self._phase("CODE")
            self.tools.call(
                "write_file",
                relative="solution.cpp",
                content=code,
                overwrite=True,
            )

        self._phase("REVIEW")
        review = self._model_call(
            AgentRole.REVIEW,
            self.context_builder.build(
                problem=problem_dict,
                role=AgentRole.REVIEW,
                current_solution=code,
                feedback=feedback.as_dict() if feedback is not None else None,
                recent_history=recent,
            ),
        )
        self.workspace.write_text("artifacts/review.md", review.content)
        return self._finish()
