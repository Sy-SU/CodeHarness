from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from dashboard.models import ExperimentSummary, Metrics, TaskSummary


def aggregate(tasks: List[TaskSummary]) -> Metrics:
    unknown: Dict[str, int] = {}

    def total(field: str):
        values = [getattr(task, field) for task in tasks]
        unknown[field] = sum(value is None for value in values)
        # A partial sum is presented alongside missing counts; wholly unknown stays null.
        return sum(value for value in values if value is not None) if any(value is not None for value in values) or not values else None

    costs: Dict[str, float] = defaultdict(float)
    unknown_cost = 0
    verdicts = {key: 0 for key in ("AC", "WA", "CE", "RE", "TLE", "MLE", "OLE", "IE", "Unknown")}
    modes = {"code-only": 0, "harness-loop": 0}
    for task in tasks:
        if task.estimated_cost is not None and task.cost_status == "known" and task.cost_currency:
            costs[task.cost_currency] += task.estimated_cost
        elif task.cost_status != "not_applicable" or task.llm_call_count != 0:
            unknown_cost += 1
        verdicts[task.final_verdict if task.final_verdict in verdicts else "Unknown"] += 1
        mode = task.mode or "unknown"
        modes[mode] = modes.get(mode, 0) + 1
    solved = sum(task.solved for task in tasks)
    return Metrics(len(tasks), solved, len(tasks) - solved, solved / len(tasks) if tasks else 0,
                   total("llm_call_count"), total("submission_attempt_count"), total("submission_count"),
                   total("input_tokens"), total("output_tokens"), total("debug_iterations"), total("duration"),
                   dict(costs), unknown_cost, unknown, verdicts, modes)


def experiments(tasks: List[TaskSummary]) -> List[ExperimentSummary]:
    groups: Dict[tuple, List[TaskSummary]] = defaultdict(list)
    for task in tasks:
        # Matches summarize.py's mode:experiment_variant grouping; profile != mixed policy.
        policy = task.policy or "unrecorded"
        if task.configuration_fingerprint:
            policy = f"{task.experiment_strategy or policy} | {task.configuration_fingerprint} | {task.actual_feedback_mode or 'unknown'} | {task.feedback_mode_status or 'unobserved'}"
            if task.feedback_policy:
                policy += f" | effective={task.effective_feedback_mode or 'unknown'} | {task.feedback_policy}"
        groups[(task.mode or "unknown", policy)].append(task)
    for mode in ("code-only", "harness-loop"):
        for policy in ("standard", "strong"):
            groups.setdefault((mode, policy), [])
    return [ExperimentSummary(f"{mode}:{policy}", mode, policy, aggregate(rows),
                              sorted({task.profile for task in rows if task.profile}))
            for (mode, policy), rows in sorted(groups.items())]
