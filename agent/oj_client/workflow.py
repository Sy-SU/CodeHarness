"""Model-free Phase 1 workflow used by integration checks and tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from agent.tools.runtime import ToolRuntime, build_default_tools
from agent.workspace.task import TaskWorkspace

from .client import OJClient, OJProtocolError
from .types import ClientErrorKind, JudgeFeedback, SubmissionRecord, Verdict


@dataclass(frozen=True)
class FixedSolutionResult:
    task_id: str
    problem_id: str
    submission_id: str
    verdict: str
    feedback: Optional[JudgeFeedback]
    workspace: str


def _problem_markdown(problem) -> str:
    lines = [
        f"# {problem.title}",
        "",
        problem.statement,
        "",
        "## Input",
        problem.input_specification,
        "",
        "## Output",
        problem.output_specification,
        "",
        "## Limits",
        f"- Time: {problem.limits.time_ms} ms",
        f"- Memory: {problem.limits.memory_mb} MB",
    ]
    for index, sample in enumerate(problem.samples, 1):
        lines.extend(
            [
                "",
                f"## Sample {index}",
                "",
                "```text",
                sample.input,
                "```",
                "```text",
                sample.output,
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def _set_phase(workspace: TaskWorkspace, phase: str) -> None:
    previous = workspace.state.current_phase
    workspace.state.current_phase = phase
    workspace.save_state()
    workspace.trace.append("STATE_CHANGE", {"from": previous, "to": phase})


def _require_feedback_matches(
    feedback: JudgeFeedback,
    submission: SubmissionRecord,
) -> None:
    if feedback.known_verdict is None:
        raise OJProtocolError(
            f"Unknown MiniOJ feedback verdict: {feedback.verdict}",
            kind=ClientErrorKind.PROTOCOL,
            method="GET",
            path=f"/api/v1/agent/submissions/{submission.submission_id}/feedback",
        )
    if feedback.verdict != submission.verdict:
        raise OJProtocolError(
            "MiniOJ feedback verdict does not match the final submission verdict",
            kind=ClientErrorKind.PROTOCOL,
            method="GET",
            path=f"/api/v1/agent/submissions/{submission.submission_id}/feedback",
        )


def run_fixed_solution(
    client: OJClient,
    workspace: TaskWorkspace,
    source_code: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    fetch_feedback: bool = True,
    tools: Optional[ToolRuntime] = None,
) -> FixedSolutionResult:
    """Submit fixed code through HTTP only, poll, and persist typed evidence."""

    runtime = tools or build_default_tools(client, workspace)
    _set_phase(workspace, "FETCH_PROBLEM")
    problem = runtime.call("get_problem", problem_id=workspace.state.problem_id)
    workspace.write_json("problem.json", problem.as_dict())
    workspace.write_text("problem.md", _problem_markdown(problem))
    runtime.call("write_file", relative="solution.cpp", content=source_code)

    _set_phase(workspace, "SUBMIT")
    created = runtime.call(
        "submit_solution",
        problem_id=problem.problem_id,
        code=source_code,
    )
    workspace.state.submission_count += 1
    workspace.state.last_submission_id = created.submission_id
    workspace.save_state()
    workspace.write_json("artifacts/submission-created.json", created.as_dict())
    workspace.trace.append(
        "SUBMISSION",
        {"submission_id": created.submission_id, "status": created.status},
        correlation_id=created.submission_id,
    )

    _set_phase(workspace, "WAIT_FOR_JUDGE")
    final = runtime.call(
        "wait_for_submission",
        submission_id=created.submission_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    workspace.state.last_verdict = final.verdict
    workspace.state.last_outcome_kind = (
        final.outcome_kind.value if final.outcome_kind is not None else None
    )
    workspace.state.solved = final.known_verdict is Verdict.AC
    workspace.write_json("artifacts/submission-final.json", final.as_dict())
    workspace.trace.append(
        "JUDGE_RESULT",
        {
            "submission_id": final.submission_id,
            "verdict": final.verdict,
            "outcome_kind": workspace.state.last_outcome_kind,
        },
        correlation_id=final.submission_id,
    )

    feedback: Optional[JudgeFeedback] = None
    if fetch_feedback:
        _set_phase(workspace, "FETCH_FEEDBACK")
        feedback = runtime.call(
            "get_feedback",
            submission_id=final.submission_id,
        )
        _require_feedback_matches(feedback, final)
        workspace.write_json("artifacts/feedback.json", feedback.as_dict())

    _set_phase(workspace, "DONE")
    workspace.save_state()
    # Keep an explicit, stable result artifact separate from mutable State.
    workspace.write_text(
        "artifacts/result.json",
        json.dumps(
            {
                "task_id": workspace.state.task_id,
                "problem_id": problem.problem_id,
                "submission_id": final.submission_id,
                "verdict": final.verdict,
                "outcome_kind": workspace.state.last_outcome_kind,
                "feedback_recorded": feedback is not None,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return FixedSolutionResult(
        task_id=workspace.state.task_id,
        problem_id=problem.problem_id,
        submission_id=final.submission_id,
        verdict=final.verdict or "",
        feedback=feedback,
        workspace=str(workspace.root),
    )
