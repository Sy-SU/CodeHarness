"""Offline preparation only: no OJ client, model complete or task execution."""
from __future__ import annotations

from collections import Counter

from agent.execution import fingerprint
from .runner import ExperimentRunner


def prepare_experiment(config, *, service=None):
    config.validate_preparation()
    conditions = ExperimentRunner(service)._conditions(config) if service is not None else None
    tasks = [{"problem_id": problem, "contest_id": config.contest_id,
              "rating": config.problem_metadata.get(problem, {}).get("rating"),
              "condition": strategy.name, "mode": strategy.mode, "profile": strategy.profile,
              "roles": strategy.roles, "repetition": repetition + 1}
             for repetition in range(config.repetitions) for problem in config.problems
             for strategy in config.strategies]
    frozen = {"config": config.as_dict(), "conditions": conditions,
              "budget_allocation": "independent_tasks_v1"}
    return {"schema_version": "experiment_preparation_v1", "status": "prepared",
            "execution_authorized": False, "task_count": len(tasks), "tasks": tasks,
            "rating_distribution": dict(Counter(str(task["rating"]) for task in tasks[::len(config.strategies)])),
            "frozen": frozen, "configuration_fingerprint": fingerprint(frozen),
            "model_snapshot_status": "configured_unverified" if conditions else "unresolved",
            "official_performance": None, "official_performance_status": "not_applicable_task_level",
            "llm_calls": 0, "formal_submissions": 0, "custom_runs": 0}
