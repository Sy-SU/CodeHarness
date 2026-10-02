"""Contest tests: frozen public membership, one existing bounded task per problem.

AC counts are a client report, not official contest points, penalties or rankings.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from agent.core.harness import HarnessPolicy
from agent.execution import RunRequest, fingerprint
from agent.oj_client.client import OJClient, OJClientError
from agent.oj_client.contests import contest_identifier
from agent.oj_client.standings import OfficialPerformance
from agent.workspace.task import TaskState, TaskWorkspace
from .config import ExperimentConfig, IDENTIFIER, Strategy
from .results import csv_text
from .runner import ExperimentRunner


@dataclass(frozen=True)
class ContestRequest:
    contest_id: str
    run: RunRequest
    total_cost_cny: float = 1.0

    def __post_init__(self):
        object.__setattr__(self, "contest_id", contest_identifier(self.contest_id))
        if not isinstance(self.run, RunRequest):
            raise ValueError("Contest requires a single-task run configuration")
        if self.run.contest_id not in {None, self.contest_id}:
            raise ValueError("Contest scope differs from the task configuration")
        object.__setattr__(self, "run", replace(self.run, contest_id=self.contest_id))
        if not self.run.require_feedback_mode or self.run.expected_feedback_mode not in {"full", "verdict_only"}:
            raise ValueError("Contest tests require an explicit supported feedback mode")
        if (isinstance(self.total_cost_cny, bool) or not isinstance(self.total_cost_cny, (int, float))
                or not math.isfinite(self.total_cost_cny) or self.total_cost_cny <= 0):
            raise ValueError("Contest total budget must be finite and positive")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        run = dict(data["run"])
        run["policy"] = HarnessPolicy(**run["policy"])
        run.setdefault("sample_checking", None)
        data["run"] = RunRequest(**run)
        return cls(**data)


def contest_store(workspace_root, run_id, *, create=False):
    if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
        raise ValueError("Invalid contest run ID")
    base = Path(workspace_root).resolve() / ".contests"
    if base.is_symlink():
        raise ValueError("Unsafe contest storage")
    if create:
        return TaskWorkspace.create(base, run_id, "contest", "contest-test")
    root = base / run_id
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Contest run unavailable")
    return TaskWorkspace(root, TaskState(run_id, "contest", mode="contest-test"))


def read_report(workspace_root, run_id):
    store = contest_store(workspace_root, run_id)
    report = store.read_json("report.json")
    try:
        metadata = store.read_json("performance.json")
    except (OSError, ValueError):
        metadata = None
    return enrich_report(report, metadata)


def enrich_report(report, metadata=None):
    """Merge additive enrichment; never rewrite historical execution results."""
    result = {**OfficialPerformance().as_dict(), **report}
    if metadata is not None:
        if (metadata.get("run_id") != report.get("run_id")
                or metadata.get("contest_id") != report.get("contest_id")
                or metadata.get("configuration_fingerprint") != report.get("configuration_fingerprint")):
            result.update(OfficialPerformance(official_performance_status="invalid").as_dict())
        else:
            try:
                result.update(OfficialPerformance(**{key: metadata.get(key)
                    for key in OfficialPerformance.__dataclass_fields__}).as_dict())
            except (TypeError, ValueError):
                result.update(OfficialPerformance(official_performance_status="invalid").as_dict())
    return result


def refresh_performance(workspace_root, run_id, settings, *, client_factory=OJClient):
    """Independent read-only HTTP enrichment. Never creates an ExecutionService."""
    from dataclasses import replace
    from datetime import datetime, timezone
    store = contest_store(workspace_root, run_id)
    with store.exclusive_run():
        record = store.read_json("contest.json")
        report = store.read_json("report.json")
        contest_id = contest_identifier(record["request"]["contest_id"])
        endpoint = fingerprint(settings.oj_base_url)
        identity = record.get("account_identity")
        if (identity is None or record.get("account_identity_endpoint_sha256") != endpoint):
            observation = OfficialPerformance(official_performance_status="identity_unresolved")
        else:
            try:
                with client_factory(settings.oj_base_url, settings.oj_api_token,
                        timeout_seconds=record["request"]["run"]["http_timeout"]) as client:
                    observation = client.get_official_performance(contest_id, identity)
            except OJClientError:
                observation = OfficialPerformance(official_performance_status="unavailable")
        observation = replace(observation, official_performance_identity=identity,
            official_performance_source=f"GET /api/v1/contests/{contest_id}/standings#rows[].performance",
            official_performance_fetched_at=observation.official_performance_fetched_at
                or datetime.now(timezone.utc).isoformat(), official_performance_endpoint_sha256=endpoint)
        metadata = {**observation.as_dict(), "run_id": run_id, "contest_id": contest_id,
                    "configuration_fingerprint": record["configuration_fingerprint"]}
        store.write_json("performance.json", metadata)
        return enrich_report(report, metadata)


def list_reports(workspace_root, limit=100):
    base = Path(workspace_root).resolve() / ".contests"
    if base.is_symlink() or not base.is_dir():
        return []
    reports = []
    for path in sorted(base.iterdir(), key=lambda path: path.name, reverse=True):
        if len(reports) >= limit:
            break
        try:
            report = read_report(workspace_root, path.name)
            reports.append({key: report.get(key) for key in
                ("run_id", "contest_id", "title", "status", "reason", "accepted", "total_problems",
                 "official_performance", "official_performance_status")})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return reports


class ContestRunner:
    def __init__(self, service):
        self.service = service

    def _record(self, request, run_id):
        return {"run_id": run_id, "request": asdict(request), "status": "queued",
            "configuration_fingerprint": fingerprint({"schema": "contest-test-v1",
                "request": asdict(request), "conditions": self.service.snapshot(request.run)}),
            "contest": None}

    def prepare(self, request, run_id):
        """Durable local intent before queueing; makes no remote requests."""
        store = contest_store(self.service.workspace_root, run_id, create=True)
        record = self._record(request, run_id)
        store.write_json("contest.json", record)
        store.write_json("report.json", self._report(record))
        return run_id

    @staticmethod
    def _report(record, manifest=None):
        snapshot = record.get("contest") or {}
        membership = snapshot.get("problems", [])
        tasks = manifest["tasks"] if manifest else []
        by_problem = {task["problem_id"]: task for task in tasks}
        problems = []
        for member in membership:
            task = by_problem.get(member["problem_id"], {})
            problems.append({**member, **{key: task.get(key) for key in
                ("task_id", "status", "terminal_status", "termination_reason", "final_verdict",
                 "llm_calls", "submission_attempts", "estimated_cost", "budget_committed_cny",
                 "first_try_ac", "recovered_to_ac", "recovery_type", "formal_recovery_to_ac",
                 "sample_gate_status", "sample_check_unverifiable_count")},
                "task_id": task.get("task_id") if task.get("workspace") else None,
                "solved": task.get("solved") is True and task.get("final_verdict") == "AC"})
        costs = [task.get("estimated_cost", 0 if task.get("status") == "pending" else None)
                 for task in tasks]
        return {"schema_version": "contest-test-v1", "run_id": record["run_id"],
            **OfficialPerformance().as_dict(),
            "contest_id": record["request"]["contest_id"], "title": snapshot.get("title"),
            "contest_status": snapshot.get("status"), "source": snapshot.get("source"),
            "status": manifest["status"] if manifest else record["status"],
            "reason": (manifest.get("stop_reason") if manifest else record.get("reason")),
            "error_kind": record.get("error_kind"), "http_status": record.get("http_status"),
            "score_policy": "ac_count_only_v1", "official_score": None,
            "total_problems": len(membership) if membership else None,
            "accepted": sum(problem["solved"] for problem in problems),
            "finished_problems": sum(task.get("status") in {"completed", "failed"} for task in tasks),
            "not_started_problems": sum(task.get("status") == "not_started" for task in tasks),
            "llm_calls": sum(task.get("llm_calls", 0) for task in tasks),
            "submission_attempts": sum(task.get("submission_attempts", 0) for task in tasks),
            "accepted_count": sum(problem["solved"] for problem in problems),
            "problem_count": len(membership) if membership else None,
            "formal_submissions": sum(task.get("oj_submissions", 0) for task in tasks),
            "custom_runs": sum(task.get("custom_run_count", 0) for task in tasks),
            "debug_count": sum(task.get("debug_iterations", 0) for task in tasks),
            "replan_count": sum(task.get("replan_count", 0) for task in tasks),
            "input_tokens": sum(task.get("input_tokens", 0) for task in tasks)
                if all(task.get("input_tokens", 0) is not None for task in tasks) else None,
            "output_tokens": sum(task.get("output_tokens", 0) for task in tasks)
                if all(task.get("output_tokens", 0) is not None for task in tasks) else None,
            "estimated_cost_cny": sum(costs) if all(cost is not None for cost in costs) else None,
            "budget_committed_cny": manifest.get("budget_committed_cny", 0) if manifest else 0,
            "total_cost_limit_cny": record["request"]["total_cost_cny"],
            "per_problem_cost_limit_cny": record["request"]["run"]["policy"]["max_cost_cny"],
            "problems": problems, "configuration_fingerprint": record["configuration_fingerprint"]}

    def run(self, request, run_id, *, resume=False):
        store = contest_store(self.service.workspace_root, run_id, create=not resume)
        with store.exclusive_run():
            signature = fingerprint({"schema": "contest-test-v1", "request": asdict(request),
                                     "conditions": self.service.snapshot(request.run)})
            if resume:
                record = store.read_json("contest.json")
                if record["configuration_fingerprint"] != signature:
                    raise ValueError("Contest configuration differs from the saved run")
                if record["status"] in {"completed", "blocked", "failed", "stopped"}:
                    return read_report(self.service.workspace_root, run_id)  # local enrichment only
            else:
                record = self._record(request, run_id)
                record["status"] = "fetching"
                store.write_json("contest.json", record)
                store.write_json("report.json", self._report(record))

            if record["contest"] is None:
                try:
                    with self.service.client_factory(self.service.settings.oj_base_url,
                            self.service.settings.oj_api_token,
                            timeout_seconds=request.run.http_timeout) as client:
                        record["contest"] = asdict(client.get_contest(request.contest_id))
                        try:
                            record["account_identity"] = (client.get_account_identity()
                                if hasattr(client, "get_account_identity") else None)
                        except OJClientError:
                            record["account_identity"] = None
                        record["account_identity_endpoint_sha256"] = fingerprint(self.service.settings.oj_base_url)
                except OJClientError as exc:
                    record.update(status="blocked", reason="contest_unavailable",
                                  error_kind=exc.kind.value, http_status=exc.http_status)
                else:
                    record["status"] = "running" if record["contest"]["problems"] else "blocked"
                    if record["status"] == "blocked":
                        record["reason"] = "contest_problems_not_visible"
                store.write_json("contest.json", record)
                store.write_json("report.json", self._report(record))
            if record["status"] == "blocked":
                return store.read_json("report.json")

            run = request.run
            config = ExperimentConfig(name=run_id,
                problems=[problem["problem_id"] for problem in record["contest"]["problems"]],
                strategies=[Strategy("contest", run.mode, run.profile, run.roles)], repetitions=1,
                total_cost_cny=request.total_cost_cny, model_config=self.service.settings.model_config,
                harness=run.policy, http_timeout=run.http_timeout, poll_interval=run.poll_interval,
                deadline=run.deadline, expected_feedback_mode=run.expected_feedback_mode,
                require_feedback_mode=run.require_feedback_mode,
                send_custom_run_code_alias=run.send_custom_run_code_alias, contest_id=request.contest_id,
                sample_checking=run.sample_checking)

            def export(manifest):
                report = self._report(record, manifest)
                store.write_json("report.json", report)
                store.write_text("problems.csv", csv_text(report["problems"]))

            try:
                # The batch lock is separate from the contest journal lock. An
                # interrupted prepared intent can resume without fetching membership.
                batch_exists = (self.service.workspace_root / ".experiments" / run_id).exists()
                manifest = ExperimentRunner(self.service, on_export=export).run(config, run_id,
                    resume=resume and batch_exists)
                record["status"] = manifest["status"]
            except BaseException as exc:
                record.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                              error_kind=type(exc).__name__)
                report = store.read_json("report.json")
                report.update(status=record["status"], error_kind=record["error_kind"])
                store.write_json("report.json", report)
                raise
            finally:
                store.write_json("contest.json", record)
            return read_report(self.service.workspace_root, run_id)
