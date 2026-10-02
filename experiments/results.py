"""Per-task JSON/CSV rows and auditable, condition-separated summaries."""
from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter, defaultdict

from agent.execution import fingerprint
from agent.oj_client.feedback import VERDICT_ONLY_POLICY, compatible_feedback
from agent.core.metrics import workspace_metrics, trace_events


def audit_trace(workspace, state):
    warnings, events = [], []
    for line in workspace.read_text("events.jsonl").splitlines():
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                raise ValueError("Invalid event shape")
            events.append(event)
        except (ValueError, TypeError):
            warnings.append("incomplete_or_invalid_trace_line")
    calls = [e["payload"] for e in events if e.get("type") == "LLM_CALL"]
    responses = [e["payload"] for e in events if e.get("type") == "LLM_RESPONSE"]
    submissions = [e for e in events if e.get("type") == "SUBMISSION"]
    attempts = [e for e in events if e.get("type") == "TOOL_CALL" and e["payload"].get("tool") == "submit_solution"]
    checks = {"llm_call_count": len(calls), "llm_success_count": sum(p.get("status") == "succeeded" for p in responses),
        "llm_failure_count": sum(p.get("status") == "failed" for p in responses),
        "submission_attempt_count": len(attempts), "submission_count": len(submissions),
        "debug_iterations": sum(p.get("role") == "DEBUG" for p in calls),
        "input_tokens": sum((p.get("usage") or {}).get("input_tokens", 0) for p in responses),
        "output_tokens": sum((p.get("usage") or {}).get("output_tokens", 0) for p in responses)}
    for field, value in checks.items():
        if state.get(field) != value:
            warnings.append(f"{field}_mismatch")
    if dict(Counter(p.get("profile") for p in calls)) != state.get("calls_per_model_profile", {}):
        warnings.append("profile_call_counts_mismatch")
    reserved = sum(e["payload"].get("reserved_cny", 0) for e in events if e.get("type") == "BUDGET_RESERVATION")
    if not math.isclose(reserved, state.get("budget_committed_cny", 0), abs_tol=1e-9):
        warnings.append("budget_reservations_mismatch")
    if not state.get("terminal_status") or len(responses) != len(calls):
        warnings.append("unfinished_or_uncertain_model_call")
    return {"status": "consistent" if not warnings else "incomplete_or_inconsistent",
            "warnings": warnings, "trace_counts": checks}


def task_row(workspace, planned):
    state = workspace.read_json("state.json")
    # Model Runtime can have a newer projection only while an operation is pending.
    if (workspace.root / "checkpoint.json").is_file():
        checkpoint = workspace.read_json("checkpoint.json")
        if not checkpoint.get("pending_operation"):
            state = checkpoint["state"]
    observation = state.get("actual_feedback_mode")
    policy = state.get("feedback_policy")
    effective = state.get("effective_feedback_mode") if policy else observation
    uncertain_calls = max(0, state.get("llm_call_count", 0)
        - state.get("llm_success_count", 0) - state.get("llm_failure_count", 0))
    usage_unknown = state.get("llm_usage_missing_count", 0) or uncertain_calls
    row = {**planned, "status": "completed" if state.get("terminal_status") else "interrupted",
        "terminal_status": state.get("terminal_status") or "result_unknown",
        "termination_reason": state.get("termination_reason") or "interrupted_task_not_reissued",
        "solved": state.get("solved", False), "final_verdict": state.get("last_verdict"),
        "llm_calls": state.get("llm_call_count", 0), "llm_successes": state.get("llm_success_count", 0),
        "llm_failures": state.get("llm_failure_count", 0),
        "calls_per_model_profile": state.get("calls_per_model_profile", {}),
        "input_tokens": state.get("input_tokens") if not usage_unknown else None,
        "output_tokens": state.get("output_tokens") if not usage_unknown else None,
        "usage_missing_count": state.get("llm_usage_missing_count", 0),
        "uncertain_llm_calls": uncertain_calls,
        "estimated_cost": None if uncertain_calls else state.get("estimated_cost"),
        "cost_currency": state.get("cost_currency"),
        "cost_estimate_status": "pending_usage" if uncertain_calls else state.get("cost_estimate_status"),
        "budget_committed_cny": state.get("budget_committed_cny", 0),
        "submission_attempts": state.get("submission_attempt_count", 0),
        "oj_submissions": state.get("submission_count", 0),
        "debug_iterations": state.get("debug_iterations", 0), "replan_count": state.get("replan_count", 0),
        "custom_run_count": state.get("custom_run_count", 0),
        "wall_clock_seconds": state.get("wall_clock_seconds"),
        "submission_id": state.get("last_submission_id"), "solution_version": state.get("solution_version"),
        "solution_sha256": state.get("solution_sha256"),
        "configuration_fingerprint": state.get("configuration_fingerprint"),
        "expected_feedback_mode": state.get("expected_feedback_mode"), "actual_feedback_mode": observation,
        "effective_feedback_mode": effective, "feedback_policy": policy,
        "feedback_mode_source": state.get("feedback_mode_source"),
        "feedback_mode_status": state.get("feedback_mode_status"),
        "conditions_verified": observation in {"full", "verdict_only"}
            and state.get("feedback_mode_status") == "confirmed"
            and compatible_feedback(state.get("expected_feedback_mode"), observation, policy)
            and (policy != VERDICT_ONLY_POLICY or effective == "verdict_only"),
        "workspace": str(workspace.root), "audit": audit_trace(workspace, state)}
    metrics = workspace_metrics(workspace, state)
    row.update({key: value for key, value in metrics.items() if key not in {"schema_version", "recovery_evidence"}})
    row.update(recovery_metrics=metrics, sample_checker=state.get("sample_checker"),
               sample_gate_status=state.get("sample_gate_status"),
               official_performance=None, official_performance_status="not_applicable_task_level",
               estimated_cost_cny=row["estimated_cost"] if row["cost_currency"] == "CNY" else None)
    if (workspace.root / "artifacts/execution-config.json").is_file():
        snapshot = workspace.read_json("artifacts/execution-config.json")
        row["actual_models"] = snapshot.get("routes", {})
        row["checker_policy"] = row.get("checker_policy") or (snapshot.get("sample_checking") or {}).get("version") or snapshot.get("sample_policy")
    events, invalid = trace_events(workspace.read_text("events.jsonl"))
    resolved = [event["payload"] for event in events if event.get("type") == "CHECKER_RESOLVED"]
    if resolved:
        row.update(checker_type=resolved[-1].get("kind"), checker_source=resolved[-1].get("source"))
    row.setdefault("checker_type", "unknown")
    row.setdefault("checker_source", "unknown")
    row.setdefault("checker_policy", "unknown")
    row["checker_applicability"] = "disabled" if row["mode"] == "code-only" else "public_samples"
    row["condition"] = row["strategy"]
    row["custom_runs"] = metrics["custom_run_count"]
    row["formal_submissions"] = metrics["formal_submission_count"]
    terminated = [event["payload"] for event in events if event.get("type") == "TASK_TERMINATED"]
    row["wall_time"] = terminated[-1].get("wall_clock_seconds") if terminated else None
    row["wall_time_source"] = "Trace:TASK_TERMINATED" if row["wall_time"] is not None else "unavailable_historical_trace"
    calls = [event["payload"] for event in events if event.get("type") == "LLM_CALL"]
    responses = [event["payload"] for event in events if event.get("type") == "LLM_RESPONSE"]
    row["llm_calls"] = len(calls)
    row["llm_call_count_status"] = "incomplete_trace" if invalid else "trace_derived"
    correlated = ({value.get("correlation_id") for value in calls} == {value.get("correlation_id") for value in responses}
        and len(calls) == len(responses) and len({value.get("correlation_id") for value in calls}) == len(calls))
    if not invalid and correlated:
        for field in ("input_tokens", "output_tokens"):
            row[field] = sum(value["usage"][field] for value in responses) if all(value.get("usage") for value in responses) else None
        costs = [value.get("cost_estimate") or {} for value in responses]
        row["estimated_cost_cny"] = sum(value["amount"] for value in costs) if all(
            value.get("known") and value.get("currency") == "CNY" for value in costs) else None
    else:
        row["input_tokens"] = row["output_tokens"] = row["estimated_cost_cny"] = None
    row["comparison_key"] = fingerprint({"strategy": row["strategy"],
        "configuration": row["configuration_fingerprint"], "actual_feedback_mode": observation,
        "effective_feedback_mode": effective, "feedback_policy": policy,
        "conditions_verified": row["conditions_verified"], "rating": row.get("rating"),
        "checker_type": row["checker_type"], "checker_policy": row["checker_policy"],
        "checker_stratum": row.get("checker_stratum")})
    return row


def summarize_rows(rows):
    groups = defaultdict(list)
    for row in rows:
        key = row.get("comparison_key") or fingerprint({"strategy": row["strategy"],
            "mode": row["mode"], "status": row["status"], "conditions_verified": False,
            "rating": row.get("rating"), "actual_feedback_mode": row.get("actual_feedback_mode"),
            "effective_feedback_mode": row.get("effective_feedback_mode"), "feedback_policy": row.get("feedback_policy"),
            "checker_type": row.get("checker_type"), "checker_policy": row.get("checker_policy"),
            "checker_stratum": row.get("checker_stratum")})
        groups[key].append(row)
    result = []
    for key, tasks in sorted(groups.items()):
        costs = [t.get("estimated_cost") for t in tasks]
        profiles = Counter()
        for task in tasks:
            profiles.update(task.get("calls_per_model_profile", {}))
        def known_sum(field):
            values = [t.get(field) for t in tasks]
            return sum(values) if all(value is not None for value in values) else None
        result.append({"comparison_key": key, "strategy": tasks[0]["strategy"],
            "condition": tasks[0]["strategy"], "checker_type": tasks[0].get("checker_type"),
            "checker_policy": tasks[0].get("checker_policy"), "checker_stratum": tasks[0].get("checker_stratum"),
            "checker_sources": sorted({t.get("checker_source", "unknown") for t in tasks}),
            "profile": tasks[0].get("profile"), "policy": tasks[0].get("policy"),
            "rating": tasks[0].get("rating"),
            "mode": tasks[0]["mode"], "tasks": len(tasks), "solved": sum(bool(t.get("solved")) for t in tasks),
            "solve_rate": sum(bool(t.get("solved")) for t in tasks) / len(tasks),
            "actual_feedback_mode": tasks[0].get("actual_feedback_mode"),
            "effective_feedback_mode": tasks[0].get("effective_feedback_mode"),
            "feedback_policy": tasks[0].get("feedback_policy"),
            "conditions_verified": all(t.get("conditions_verified", False) for t in tasks),
            "final_verdicts": dict(Counter(t.get("final_verdict") or "none" for t in tasks)),
            "terminal_statuses": dict(Counter(t.get("terminal_status") or "not_started" for t in tasks)),
            "llm_calls": sum(t.get("llm_calls", 0) for t in tasks),
            "llm_successes": sum(t.get("llm_successes", 0) for t in tasks),
            "llm_failures": sum(t.get("llm_failures", 0) for t in tasks),
            "calls_per_model_profile": dict(profiles),
            "input_tokens": known_sum("input_tokens"), "output_tokens": known_sum("output_tokens"),
            "unknown_usage_tasks": sum(t.get("input_tokens") is None or t.get("output_tokens") is None for t in tasks),
            "usage_missing_count": sum(t.get("usage_missing_count", 0) for t in tasks),
            "uncertain_llm_calls": sum(t.get("uncertain_llm_calls", 0) for t in tasks),
            "submission_attempts": sum(t.get("submission_attempts", 0) for t in tasks),
            "oj_submissions": sum(t.get("oj_submissions", 0) for t in tasks),
            "debug_iterations": sum(t.get("debug_iterations", 0) for t in tasks),
            "replan_count": sum(t.get("replan_count", 0) for t in tasks),
            "custom_run_count": sum(t.get("custom_run_count", 0) for t in tasks),
            "wall_clock_seconds": known_sum("wall_clock_seconds"),
            "budget_committed_cny": sum(t.get("budget_committed_cny", 0) for t in tasks),
            "estimated_cost": sum(costs) if all(c is not None for c in costs) else None,
            "cost_currency": tasks[0].get("cost_currency"),
            "unknown_cost_tasks": sum(c is None for c in costs)})
        for metric in ("first_try_ac", "recovered_to_ac", "recovered_after_sample_failure",
                       "recovered_after_formal_failure", "formal_recovery_to_ac"):
            result[-1][metric + "_count"] = sum(t.get(metric) is True for t in tasks)
            result[-1][metric + "_unknown_count"] = sum(t.get(metric) is None for t in tasks)
        for metric in ("successful_debug_count", "sample_gate_reject_count", "sample_check_unverifiable_count",
                       "invalid_model_output_count", "formal_submission_count", "candidate_version_count"):
            result[-1][metric] = known_sum(metric)
        result[-1]["recovery_types"] = dict(Counter(kind for t in tasks for kind in t.get("recovery_type", [])))
        result[-1]["official_performance"] = None
        result[-1]["official_performance_status"] = "not_applicable_task_level"
    return result


def csv_text(rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        values = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            elif value is None:
                value = ""  # paired explicit status columns preserve unknown, not zero
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                value = "'" + value  # spreadsheet formula injection
            values[key] = value
        writer.writerow(values)
    return output.getvalue()
