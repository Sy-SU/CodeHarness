"""Sequential, journaled batches reusing the bounded single-task execution service."""
from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from agent.core.harness import HarnessPolicy
from agent.execution import RunRequest, fingerprint
from agent.workspace.task import TaskState, TaskWorkspace
from .config import ExperimentConfig, IDENTIFIER
from .results import csv_text, summarize_rows, task_row
from .preflight import PreflightError


class ExperimentRunner:
    def __init__(self, service, *, on_export=None):
        self.service = service
        self.on_export = on_export

    def _store(self, experiment_id, *, create):
        if not isinstance(experiment_id, str) or not IDENTIFIER.fullmatch(experiment_id):
            raise ValueError("Invalid experiment ID")
        base = self.service.workspace_root.resolve() / ".experiments"
        if base.is_symlink():
            raise ValueError("Experiment storage must not be a symlink")
        if create:
            return TaskWorkspace.create(base, experiment_id, "batch", "experiment", trace_schema_version="phase5-v1")
        root = base / experiment_id
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Experiment unavailable")
        return TaskWorkspace(root, TaskState(experiment_id, "batch", mode="experiment"),
                             trace_schema_version="phase5-v1")

    def _conditions(self, config):
        return {strategy.name: self.service.snapshot(config.request(config.problems[0], strategy))
                for strategy in config.strategies}

    def _plan(self, config, experiment_id):
        return [{"task_id": f"{experiment_id}-s{s + 1}-p{p + 1}-r{r + 1}",
            "problem_id": problem, "strategy": strategy.name, "mode": strategy.mode,
            "repetition": r + 1, "status": "pending", "profile": strategy.profile,
            "policy": "fixed" if strategy.profile else "mixed", "roles": strategy.roles,
            "contest_id": config.contest_id,
            "rating": config.problem_metadata.get(problem, {}).get("rating"),
            "rating_source": config.problem_metadata.get(problem, {}).get("rating_source")}
            for r in range(config.repetitions) for p, problem in enumerate(config.problems)
            for s, strategy in enumerate(config.strategies)]

    @staticmethod
    def _skip(row, reason):
        row.update(status="not_started", terminal_status="budget_exhausted", termination_reason=reason,
            solved=False, estimated_cost=0, cost_estimate_status="not_applicable",
            budget_committed_cny=0, conditions_verified=False)

    def _export(self, store, manifest):
        rows = manifest["tasks"]
        manifest["budget_committed_cny"] = float(sum(
            (Decimal(str(row.get("budget_committed_cny", 0))) for row in rows), Decimal(0)))
        store.write_json("manifest.json", manifest)
        store.write_json("tasks.json", rows)
        store.write_text("tasks.csv", csv_text(rows))
        summaries = summarize_rows(rows)
        store.write_json("summary.json", summaries)
        store.write_text("summary.csv", csv_text(summaries))
        if self.on_export:
            self.on_export(manifest)

    def run(self, config, experiment_id, *, resume=False, preflight_id=None):
        # Check before creating a batch workspace or exporting any projections.
        # Existing unguarded runs retain their historical allocation/order.
        saved = None
        if resume:
            saved = self._store(experiment_id, create=False).read_json("manifest.json")
            frozen_id = (saved or {}).get("preflight_id")
            if frozen_id and preflight_id not in {None, frozen_id}:
                raise PreflightError("Cannot replace an experiment's frozen preflight ID")
            preflight_id = frozen_id or preflight_id
        if config.preparation.get("five_conditions") and not preflight_id:
            raise PreflightError("This prepared experiment requires --preflight-id before execution")
        preflight = None
        if preflight_id:
            from .preflight import check_frozen_preflight
            preflight = check_frozen_preflight(config, self.service, preflight_id,
                expected_report_hash=(saved or {}).get("preflight_report_hash"))
        store = self._store(experiment_id, create=not resume)
        with store.exclusive_run():
            conditions = self._conditions(config)
            # Freeze allocation semantics too: old shared-per-problem runs must
            # not silently acquire a fresh per-task allowance on resume.
            allocation = "independent_tasks_v1"
            signature = fingerprint({"config": config.as_dict(), "conditions": conditions,
                                     "budget_allocation": allocation})
            if resume:
                manifest = store.read_json("manifest.json")
                if manifest.get("preflight_id") != (saved or {}).get("preflight_id"):
                    raise ValueError("Frozen preflight association changed while acquiring the run lock")
                if preflight and manifest.get("preflight_report_hash") != preflight["preflight_report_hash"]:
                    raise ValueError("Frozen preflight report differs from the saved run")
                if manifest["configuration_fingerprint"] != signature:
                    raise ValueError("Experiment config/provider/routes/prices differ from the saved run")
                if manifest["status"] == "completed":
                    self._export(store, manifest)  # refresh projections only; no remote actions
                    return manifest
                if manifest.get("stop_reason") == "provider_token_bound_violated":
                    raise ValueError("Cannot resume after the provider violated the configured token bound")
            else:
                manifest = {"schema_version": "phase5-v1", "experiment_id": experiment_id,
                    "config": config.as_dict(), "conditions": conditions,
                    "budget_allocation": allocation,
                    "configuration_fingerprint": signature, "status": "running",
                    "tasks": self._plan(config, experiment_id), "budget_committed_cny": 0}
                if preflight:
                    manifest.update(preflight_id=preflight_id,
                        preflight_report_hash=preflight["preflight_report_hash"],
                        experiment_fingerprint=preflight["fingerprint"], execution=preflight["execution"])
                    planned = {(row["problem_id"], row["strategy"], row["repetition"]): row for row in manifest["tasks"]}
                    checkers = {row["problem_id"]: row for row in preflight["checker_summary"]["problems"]}
                    manifest["tasks"] = [planned[(item["problem_id"], item["condition"], item["repetition"])]
                        for item in preflight["execution"]["execution_order"]]
                    for row in manifest["tasks"]:
                        checker = checkers[row["problem_id"]]
                        row.update({key: checker[key] for key in ("checker_type", "checker_source", "checker_policy", "checker_stratum")})
                        row["expected_checker_type"] = checker["effective_checker_type"]
                self._export(store, manifest)
            manifest["status"] = "running"
            strategies = {strategy.name: strategy for strategy in config.strategies}
            try:
                for index, row in enumerate(manifest["tasks"]):
                    if row["status"] not in {"pending", "running", "interrupted"}:
                        continue
                    if preflight:
                        # Files, prompts and dirty diff must stay frozen between tasks.
                        check_frozen_preflight(config, self.service, preflight_id,
                            expected_report_hash=manifest["preflight_report_hash"])
                        with self.service.client_factory(self.service.settings.oj_base_url,
                                self.service.settings.oj_api_token, timeout_seconds=config.http_timeout) as client:
                            observation = client.get_feedback_mode()
                        if (observation.get("status") != "confirmed"
                                or observation.get("mode") != preflight["feedback"]["actual_feedback_mode"]):
                            raise PreflightError("Feedback metadata drift; use a new preflight/experiment ID")
                        # Public inputs are also frozen when the preflight could
                        # fetch them. This is an ordinary authorized GET, before
                        # task creation; it does not run contestant code.
                        problem = next(item for item in preflight["checker_summary"]["problems"]
                                       if item["problem_id"] == row["problem_id"])
                        if problem.get("problem_input_hash"):
                            with self.service.client_factory(self.service.settings.oj_base_url,
                                    self.service.settings.oj_api_token, timeout_seconds=config.http_timeout) as client:
                                current_problem = client.get_problem(row["problem_id"])
                            if fingerprint(current_problem.as_dict()) != problem["problem_input_hash"]:
                                raise PreflightError("Public problem input drift; use a new preflight/experiment ID")
                    total_spent = sum((Decimal(str(t.get("budget_committed_cny", 0)))
                        for i, t in enumerate(manifest["tasks"]) if i != index), Decimal(0))
                    cap = min(Decimal(str(config.harness.max_cost_cny)),
                              Decimal(str(config.total_cost_cny)) - total_spent)
                    if cap <= 0:
                        self._skip(row, "experiment_budget_limit")
                        self._export(store, manifest)
                        continue
                    active = row["status"] in {"running", "interrupted"}
                    if active:
                        # Restore the exact allocated budget, not a fresh allowance.
                        policy = HarnessPolicy(**row["effective_budget"])
                    else:
                        policy = replace(config.harness, max_cost_cny=float(cap))
                    request = config.request(row["problem_id"], strategies[row["strategy"]], policy=policy)
                    row["effective_budget"] = asdict(policy)
                    row["status"] = "running"
                    self._export(store, manifest)  # task intent durable before any remote action
                    workspace = None
                    try:
                        if active:
                            root = self.service.workspace_root / row["task_id"]
                            if not root.exists():
                                workspace = self.service.create_workspace(request, row["task_id"],
                                    experiment_id=experiment_id, strategy=row["strategy"])
                            elif row["mode"] == "harness-loop" and (root / "checkpoint.json").is_file():
                                workspace = TaskWorkspace.load(self.service.workspace_root, row["task_id"])
                            else:
                                if root.is_symlink():
                                    raise ValueError("Unsafe saved task")
                                workspace = TaskWorkspace(root, TaskState(**TaskWorkspace(
                                    root, TaskState(row["task_id"], row["problem_id"])).read_json("state.json")))
                                if not workspace.state.terminal_status:
                                    workspace.state.terminal_status = "result_unknown"
                                    workspace.state.termination_reason = "interrupted_code_only_not_reissued"
                                    workspace.state.current_phase = "DONE"
                                    workspace.save_state()
                                manifest["tasks"][index] = task_row(workspace, row)
                                self._export(store, manifest)
                                continue
                        else:
                            workspace = self.service.create_workspace(request, row["task_id"],
                                experiment_id=experiment_id, strategy=row["strategy"])
                        if self.on_export:
                            row["workspace"] = str(workspace.root)
                            self._export(store, manifest)  # live link only after the task exists
                        self.service.run(request, row["task_id"], workspace=workspace,
                            resume=active and (workspace.root / "checkpoint.json").is_file())
                        manifest["tasks"][index] = task_row(workspace, row)
                    except KeyboardInterrupt:
                        if workspace is not None:
                            partial = task_row(workspace, row)
                            partial["status"] = "interrupted"
                            manifest["tasks"][index] = partial
                        manifest["status"] = "interrupted"
                        self._export(store, manifest)
                        raise
                    except Exception as exc:
                        # Never re-run an intent after a setup/runtime failure. Keep evidence.
                        if workspace is not None:
                            partial = task_row(workspace, row)
                            partial["status"] = "failed"
                            partial["execution_error_kind"] = type(exc).__name__
                            manifest["tasks"][index] = partial
                        else:
                            row.update(status="failed", solved=False, terminal_status="internal_failure",
                                termination_reason="task_setup_failed", execution_error_kind=type(exc).__name__,
                                estimated_cost=None, conditions_verified=False)
                    self._export(store, manifest)
                    final_row = manifest["tasks"][index]
                    if final_row.get("termination_reason") == "provider_exceeded_configured_token_bound":
                        manifest["status"] = "stopped"
                        manifest["stop_reason"] = "provider_token_bound_violated"
                        self._export(store, manifest)
                        return manifest
                manifest["status"] = "completed"
                self._export(store, manifest)
                return manifest
            except ValueError:
                if preflight:
                    manifest["status"] = "stopped"
                    manifest["stop_reason"] = "preflight_drift"
                    self._export(store, manifest)
                raise
            except KeyboardInterrupt:
                manifest["status"] = "interrupted"
                self._export(store, manifest)
                raise
