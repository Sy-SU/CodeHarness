from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Any, Dict, List, Optional

from dashboard.models import CodeVersionView, ModelCallView, ProblemView, SubmissionView, TaskDetail, TaskSummary, ToolCallView
from dashboard.repository.trace import TraceReader
from dashboard.repository.workspace import WorkspaceIndex, WorkspaceRepository, integer, number, string


def elapsed_ms(start: Optional[str], end: Optional[str]) -> Optional[float]:
    try:
        delta = (datetime.fromisoformat(end.replace("Z", "+00:00")) -
                 datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
        return round(delta * 1000, 3) if delta >= 0 else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


class TaskService:
    def __init__(self, repository: WorkspaceRepository, index: WorkspaceIndex, traces: TraceReader):
        self.repository, self.index, self.traces = repository, index, traces

    def list(self, *, q: str = "", task_id: str = "", problem_id: str = "", mode: str = "",
             profile: str = "", status: str = "", verdict: str = "", sort: str = "updated") -> List[TaskSummary]:
        tasks = self.index.tasks()
        result = [task for task in tasks
                  if (not q or q.lower() in f"{task.task_id} {task.problem_id or ''}".lower())
                  and (not task_id or task_id.lower() in task.task_id.lower())
                  and (not problem_id or problem_id.lower() in (task.problem_id or "").lower())
                  and (not mode or (task.mode or "unknown") == mode)
                  and (not profile or (task.profile or "unknown") == profile)
                  and (not status or task.status == status)
                  and (not verdict or (task.final_verdict or "Unknown") == verdict)]
        field = {"updated": "updated_at", "created": "created_at", "duration": "duration"}.get(sort, "updated_at")
        return sorted(result, key=lambda task: (getattr(task, field) is not None,
                      getattr(task, field) if getattr(task, field) is not None else (0 if field == "duration" else "")), reverse=True)

    @staticmethod
    def _submission(target: SubmissionView, value: Dict[str, Any]) -> None:
        target.status = string(value.get("status")) or target.status
        target.verdict = string(value.get("verdict")) or target.verdict
        target.created_at = string(value.get("created_at")) or target.created_at
        target.finished_at = string(value.get("finished_at")) or target.finished_at
        resources, tests = mapping(value.get("resources")), mapping(value.get("tests"))
        target.time_ms = number(resources.get("time_ms")) if "time_ms" in resources else target.time_ms
        target.memory_kb = number(resources.get("memory_kb")) if "memory_kb" in resources else target.memory_kb
        if integer(tests.get("total")) is not None and integer(tests.get("passed")) is not None:
            target.test_summary = f"{tests['passed']} / {tests['total']} passed"

    def detail(self, task_id: str) -> TaskDetail:
        summary = self.index.task(task_id)
        state, state_warning = self.repository.read_json(task_id, "state.json")
        problem, _ = self.repository.read_json(task_id, "problem.json")
        snapshot = self.traces.read(task_id)
        calls: Dict[str, ModelCallView] = {}
        tools: Dict[str, ToolCallView] = {}
        versions: Dict[str, CodeVersionView] = {}
        submissions: Dict[str, SubmissionView] = {}
        warnings = [*summary.warnings, *snapshot.warnings]
        if state_warning and state_warning not in warnings:
            warnings.append(state_warning)
        for position, event in enumerate(snapshot.events):
            data, kind = event.metadata, event.event_type
            call_id = event.correlation_id or f"unlinked-{position}"
            if kind in {"LLM_CALL", "LLM_RESPONSE"}:
                call = calls.setdefault(call_id, ModelCallView(call_id))
                call.timestamp = call.timestamp or event.timestamp
                call.phase = call.phase or event.phase
                for name in ("role", "profile", "provider", "model", "request_id", "finish_reason"):
                    if string(data.get(name)):
                        setattr(call, name, data[name])
                if kind == "LLM_RESPONSE":
                    call.status = string(data.get("status")) or "unknown"
                    usage = mapping(data.get("usage"))
                    call.input_tokens, call.output_tokens = integer(usage.get("input_tokens")), integer(usage.get("output_tokens"))
                    call.latency_ms = number(data.get("latency_ms"))
                    cost = mapping(data.get("cost_estimate"))
                    call.cost_currency = string(cost.get("currency"))
                    call.estimated_cost = number(cost.get("amount")) if cost.get("known") is True and call.cost_currency else None
                    call.cost_status = "known" if call.estimated_cost is not None else string(cost.get("reason")) or "unknown"
            elif kind in {"TOOL_CALL", "TOOL_RESULT"}:
                tool = tools.setdefault(call_id, ToolCallView(call_id))
                tool.name = string(data.get("tool")) or tool.name
                tool.timestamp, tool.phase = tool.timestamp or event.timestamp, tool.phase or event.phase
                tool.metadata[kind] = data
                if kind == "TOOL_RESULT":
                    tool.status = "succeeded" if data.get("ok") is True else "failed" if data.get("ok") is False else "unknown"
                    tool.duration_ms = elapsed_ms(tool.timestamp, event.timestamp)
                    value = mapping(data.get("result"))
                    sid = value.get("submission_id")
                    if sid is not None and isinstance(sid, (str, int)) and not isinstance(sid, bool):
                        sid = str(sid)
                        target = submissions.setdefault(sid, SubmissionView(sid))
                        self._submission(target, value)
            elif kind == "CODE_VERSION":
                version = string(data.get("solution_version")) or string(data.get("version"))
                if version:
                    versions[version] = CodeVersionView(version, event.phase, event.timestamp,
                        string(data.get("model_call_id")), string(data.get("code_sha256")))
            elif kind in {"SUBMISSION", "JUDGE_RESULT"}:
                sid = data.get("submission_id")
                if isinstance(sid, (str, int)) and not isinstance(sid, bool):
                    sid = str(sid)
                    target = submissions.setdefault(sid, SubmissionView(sid))
                    target.code_version = string(data.get("solution_version")) or target.code_version
                    target.code_sha256 = string(data.get("code_sha256")) or target.code_sha256
                    target.model_call_id = string(data.get("model_call_id")) or target.model_call_id
                    self._submission(target, data)
                    if kind == "SUBMISSION":
                        target.created_at = target.created_at or event.timestamp
                    else:
                        target.status, target.finished_at = "FINISHED", target.finished_at or event.timestamp
        # Older Phase 1 and partial tasks may have artifacts without association events.
        for name in ("artifacts/submission-created.json", "artifacts/submission-final.json", "artifacts/submission.json"):
            if self.repository.fingerprint(task_id, name) is None:
                continue
            value, warning = self.repository.read_json(task_id, name)
            if warning:
                warnings.append(warning)
            sid = value.get("submission_id")
            if isinstance(sid, (str, int)) and not isinstance(sid, bool):
                sid = str(sid)
                self._submission(submissions.setdefault(sid, SubmissionView(sid)), value)
        current_version = string(state.get("solution_version"))
        if current_version and current_version not in versions:
            versions[current_version] = CodeVersionView(current_version,
                model_call_id=string(state.get("solution_model_call_id")),
                sha256=string(state.get("solution_sha256")))
        for submission in submissions.values():
            version = versions.get(submission.code_version)
            if version is not None:
                # Do not imply the verdict belongs to a differently hashed candidate.
                if submission.code_sha256 and version.sha256 and submission.code_sha256 != version.sha256:
                    warnings.append(f"Submission {submission.submission_id}: code hash mismatch")
                    continue
                version.submission_ids.append(submission.submission_id)
                version.verdicts.append(submission.verdict or "Unknown")
        solution_digest = None
        try:
            source = self.repository.read_bytes(task_id, "solution.cpp")
            solution = self.repository.sanitizer.text(source.decode("utf-8"))
            solution_digest = hashlib.sha256(source).hexdigest()
            recorded = string(state.get("solution_sha256"))
            if recorded and re.fullmatch(r"[a-fA-F0-9]{64}", recorded) and recorded.lower() != solution_digest:
                warnings.append("Current solution.cpp differs from the recorded candidate hash; verdicts remain tied to submission records")
        except (OSError, ValueError, UnicodeError):
            solution = None
        from agent.core.metrics import recovery_metrics
        metrics = recovery_metrics([{"type": event.event_type, "payload": event.metadata}
            for event in snapshot.events], state, invalid_lines=len(snapshot.warnings))
        return TaskDetail(summary, ProblemView(string(problem.get("problem_id")) or summary.problem_id,
                string(problem.get("title")), string(problem.get("statement"))), state, solution,
                list(versions.values()), list(calls.values()), list(tools.values()), list(submissions.values()),
                [event for event in snapshot.events if event.event_type == "JUDGE_RESULT"],
                [event for event in snapshot.events if event.event_type == "STATE_CHANGE"],
                self.repository.artifacts(task_id), snapshot.events, snapshot.generation, warnings, solution_digest, metrics)
