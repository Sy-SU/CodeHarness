"""Explicit bounded checker-only smoke. No solution or formal-submit runtime."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from agent.core.budget import BudgetStopped, reserve_model_cost
from agent.core.checker import GENERATED_SAMPLE_POLICY, CheckerSpec, SampleGatePolicy
from agent.core.checker_session import GeneratedCheckerSession
from agent.core.context import ContextBuilder
from agent.core.policy import ModelPolicy
from agent.execution import fingerprint
from agent.models.registry import ModelRegistry
from agent.models.router import ModelRouter
from agent.models.runtime import ModelCallRuntime
from agent.models.types import AgentRole
from agent.oj_client.client import OJClient, OJClientError
from agent.tools.runtime import ToolRuntime
from agent.workspace.task import TaskState, TaskWorkspace

from .config import IDENTIFIER
from .freeze import file_hash

SMOKE_PROBLEMS = ("CF2119B", "CF2118B", "CF2113B", "CF2111D", "CF2110C", "CF2103C", "CF2084D", "CF2092D")
SMOKE_VERSION = "checker_live_smoke_v2"
SMOKE_GATES = {"CHECKER_LIVE_SMOKE_PASSED", "CHECKER_LIVE_SMOKE_PASSED_WITH_WARNINGS", "CHECKER_LIVE_SMOKE_FAILED"}
CHECKER_FILES = ("agent/core/checker.py", "agent/core/generated_checker.py", "agent/core/checker_session.py",
                 "agent/core/harness.py", "agent/core/metrics.py", "agent/core/agent.py",
                 "agent/execution.py", "agent/workspace/task.py")


def checker_implementation(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    files = {name: file_hash(root / name) for name in CHECKER_FILES}
    return {"files": files, "sha256": fingerprint(files)}


@dataclass(frozen=True)
class SmokeLimits:
    task_cost_cap_cny: float = 1.0
    total_cost_cap_cny: float = 8.0
    calls_per_problem: int = 1
    total_calls: int = 8
    custom_runs_per_problem: int = 3

    def __post_init__(self):
        # This is a fixed eight-problem, once-per-problem authorization boundary.
        if (self.calls_per_problem != 1 or self.total_calls != 8 or self.custom_runs_per_problem != 3
                or isinstance(self.task_cost_cap_cny, bool) or not 0 < self.task_cost_cap_cny <= 1
                or isinstance(self.total_cost_cap_cny, bool) or self.total_cost_cap_cny != 8 * self.task_cost_cap_cny):
            raise ValueError("Invalid bounded checker smoke limits")


class SmokeHost:
    def __init__(self, workspace, checkpoint, router, policy, client, limits):
        self.workspace, self.state, self.checkpoint, self.limits = workspace, workspace.state, checkpoint, limits
        tools = ToolRuntime(workspace)
        # No submit handler, filesystem tool, contestant runtime or HarnessLoop.
        tools.register("run_code", client.run_code)
        self.agent = SimpleNamespace(router=router, policy=policy, context_builder=ContextBuilder(), tools=tools,
                                     model_runtime=ModelCallRuntime(router, workspace))

    def _save(self):
        self.checkpoint["state"] = asdict(self.state)
        self.workspace.write_json("checkpoint.json", self.checkpoint)
        self.workspace.save_state()

    def _links(self):
        return {"solution_version": None, "code_sha256": None, "model_call_id": None}

    def _guard(self):
        if self.state.custom_run_count >= self.limits.custom_runs_per_problem:
            raise BudgetStopped("checker_execution_failed", "smoke_custom_run_limit")

    def _reserve_model(self, role, messages):
        if role is not AgentRole.CODE or self.state.llm_call_count >= self.limits.calls_per_problem:
            raise BudgetStopped("checker_generation_failed", "smoke_generation_not_reissued")
        profile = self.agent.policy.choose(role, allow_escalation=False)
        inputs, outputs, bound = reserve_model_cost(self.agent.router.route(profile), messages, self.state,
            self.limits.task_cost_cap_cny, audit=lambda data: self.workspace.trace.append("MODEL_BUDGET_CHECK", data))
        self.checkpoint["pending_operation"] = {"kind": "model", "purpose": "sample_checker_generation"}
        self._save()
        self.workspace.trace.append("BUDGET_RESERVATION", {"role": "CODE", "profile": profile.value,
            "reserved_cny": bound, "max_cost_cny": self.limits.task_cost_cap_cny})
        return profile, inputs, outputs


def _problem_result(host):
    problem, spec = host.checkpoint.get("problem"), host.checkpoint.get("sample_checker")
    observations = []
    if problem and spec:
        samples = problem.get("samples", [])
        for index, sample in enumerate(samples[:host.limits.custom_runs_per_problem]):
            value = GeneratedCheckerSession(host).check(sample, sample["output"], index, reference_only=True)
            observations.append(value)
            if value["decision"] is not True:
                host.workspace.trace.append("UNVERIFIED_CHECKER_STOP", value)
                break
    saved = host.checkpoint.get("generated_checker") or {}
    success = bool(observations) and all(value["decision"] is True for value in observations)
    failed = any(value["decision"] is False or value.get("failure_status") in {
        "checker_generation_failed", "checker_execution_failed", "checker_sanity_failed"} for value in observations)
    return {"problem_id": host.state.problem_id, "checker_kind": (spec or {}).get("kind", "unknown"),
        "problem_input_hash": fingerprint(problem) if problem else None,
        "checker_spec": spec, "generation_status": saved.get("generation_status", "not_run"),
        "checker_source_sha256": saved.get("code_sha256"), "checker_id": saved.get("checker_id"),
        "generation_model_call_id": saved.get("model_call_id"), "generation_metadata": saved.get("generation_metadata"),
        "execution_status": "success" if success else observations[-1].get("execution_status") if observations else "not_run",
        "reference_sanity_status": "pass" if success else observations[-1].get("sanity_status") if observations else "not_run",
        "verification_class": "llm_generated_unverified", "live_path_status": "PASS" if success else "FAIL" if failed else "UNKNOWN",
        "observations": observations, "reference_count": len(observations), "candidate_judgments": 0,
        "published_sample_count": len(problem.get("samples", [])) if problem else None,
        "negative_sanity": "not_performed_no_proven_negative_oracle",
        "llm_calls": host.state.llm_call_count, "custom_runs": host.state.custom_run_count,
        "input_tokens": host.state.input_tokens if host.state.cost_estimate_status == "known" else None,
        "output_tokens": host.state.output_tokens if host.state.cost_estimate_status == "known" else None,
        "estimated_cost_cny": host.state.estimated_cost, "provider_reported_cost": None, "billed_cost": None,
        "formal_submissions": 0, "candidate_source_generated": False}


def run_checker_smoke(settings, model_config, workspace_root, smoke_id, *, resume=False, limits=None, registry=None, client=None):
    """The caller must explicitly authorize this paid checker-only operation."""
    if not IDENTIFIER.fullmatch(smoke_id):
        raise ValueError("Invalid checker smoke ID")
    limits = limits or SmokeLimits()
    base = Path(workspace_root).resolve() / ".checker-smoke" / smoke_id
    if any(p.is_symlink() for p in (base, *base.parents)):
        raise ValueError("Unsafe smoke storage")
    policy = ModelPolicy.from_yaml(Path(model_config))
    profile = policy.choose(AgentRole.CODE, allow_escalation=False)
    registry = registry or ModelRegistry.from_yaml(Path(model_config), required_profiles={profile})
    router = ModelRouter(registry)
    provider = registry.provider(router.route(profile).provider)
    frozen = {"schema_version": SMOKE_VERSION, "smoke_id": smoke_id, "problem_ids": list(SMOKE_PROBLEMS),
        "limits": asdict(limits), "model_profile": profile.value, "model_route": asdict(router.route(profile)),
        "provider_endpoint_fingerprint": fingerprint(provider.base_url),
        "provider_transport": getattr(provider, "transport_metadata", None),
        "oj_endpoint_fingerprint": fingerprint(settings.oj_base_url), "sample_policy": SampleGatePolicy(version=GENERATED_SAMPLE_POLICY).as_dict(),
        "checker_implementation": checker_implementation(), "formal_submissions_allowed": False,
        "contestant_generation_allowed": False, "cross_experiment_checker_reuse": False}
    if resume:
        if json.loads((base / "manifest.json").read_text()) != frozen:
            raise ValueError("Checker smoke frozen identity drift")
    else:
        base.mkdir(parents=True, exist_ok=False)
        (base / "manifest.json").write_text(json.dumps(frozen, indent=2) + "\n")
    owns_client = client is None
    client = client or OJClient(settings.oj_base_url, settings.oj_api_token, timeout_seconds=15)
    rows = []
    try:
        for problem_id in SMOKE_PROBLEMS:
            task_id = smoke_id + "-" + problem_id
            task_root = base / task_id
            if resume and task_root.exists():
                temporary = TaskWorkspace(task_root, TaskState(task_id, problem_id))
                checkpoint = temporary.read_json("checkpoint.json")
                workspace = TaskWorkspace(task_root, TaskState(**checkpoint["state"]), trace_schema_version="checker-smoke-v2")
            else:
                workspace = TaskWorkspace.create(base, task_id, problem_id, "checker-smoke", trace_schema_version="checker-smoke-v2")
                workspace.state.current_phase = "CHECKER_SMOKE"
                workspace.state.checker_schema_version = "checker_state_v2"
                checkpoint = {"schema_version": SMOKE_VERSION, "pending_operation": None, "frozen": frozen,
                              "problem": None, "sample_checker": None}
                SmokeHost(workspace, checkpoint, router, policy, client, limits)._save()
            with workspace.exclusive_run():
                checkpoint = workspace.read_json("checkpoint.json")
                if (checkpoint.get("frozen") != frozen or checkpoint["state"].get("task_id") != task_id
                        or checkpoint["state"].get("problem_id") != problem_id):
                    raise ValueError("Smoke task identity drift")
                workspace.state = TaskState(**checkpoint["state"])
                host = SmokeHost(workspace, checkpoint, router, policy, client, limits)
                if checkpoint.get("result") is not None:
                    rows.append(checkpoint["result"])
                    continue
                if checkpoint.get("pending_operation"):
                    # Reconcile the model runtime's durable attempt state, but never resend.
                    state = workspace.read_json("state.json")
                    if state.get("llm_call_count", 0) > host.state.llm_call_count:
                        workspace.state = host.state = TaskState(**state)
                if checkpoint.get("problem") is None:
                    try:
                        metadata = client.get_checker_metadata(problem_id)
                        problem = client.get_problem(problem_id)
                        if problem.problem_id != problem_id:
                            raise ValueError("Smoke problem identity mismatch")
                        checkpoint["problem"] = problem.as_dict()
                        checkpoint["sample_checker"] = asdict(CheckerSpec.from_metadata(metadata["checker"], source=metadata["source"]))
                        workspace.write_json("problem.json", problem.as_dict())
                        workspace.write_json("checker-metadata.json", metadata)
                        host._save()
                    except OJClientError:
                        pass
                row = _problem_result(host)
                checkpoint["result"] = row
                host.state.current_phase = "DONE"
                host._save()
                workspace.write_json("artifacts/result.json", row)
                rows.append(row)
                print(json.dumps({key: row[key] for key in ("problem_id", "generation_status", "execution_status",
                    "reference_sanity_status", "live_path_status", "llm_calls", "custom_runs", "estimated_cost_cny")}), flush=True)
    finally:
        if owns_client:
            client.close()
        for item in registry.providers.values():
            if getattr(item, "client", None) is not None:
                item.client.close()
    passed = all(row["live_path_status"] == "PASS" for row in rows) and len(rows) == 8
    warnings = ["llm_generated_unverified_no_semantic_certification", "public_reference_only_coverage",
                "negative_sanity_not_performed", "provider_reported_and_billed_cost_unknown"]
    def total(key):
        values = [row[key] for row in rows]
        return None if any(value is None for value in values) else sum(values)
    summary = {"schema_version": SMOKE_VERSION, "smoke_id": smoke_id,
        "gate": "CHECKER_LIVE_SMOKE_PASSED_WITH_WARNINGS" if passed else "CHECKER_LIVE_SMOKE_FAILED",
        "created_at": datetime.now(timezone.utc).isoformat(), "frozen": frozen, "problems": rows,
        "blockers": [] if passed else [row["problem_id"] + ":" + row["live_path_status"] for row in rows if row["live_path_status"] != "PASS"],
        "warnings": warnings, "accounting": {key: total(key) for key in ("llm_calls", "custom_runs", "input_tokens", "output_tokens", "estimated_cost_cny")},
        "formal_submissions": 0, "main_tasks_started": 0, "local_generated_checker_executions": 0,
        "provider_reported_cost": None, "billed_cost": None,
        "files_sha256": {str(path.relative_to(base)): file_hash(path) for path in sorted(base.rglob("*"))
                         if path.is_file() and path.name not in {"summary.json", ".run.lock"}}}
    if total("llm_calls") > 8 or total("custom_runs") > 24:
        raise ValueError("Smoke accounting exceeded frozen limit")
    with (base / "summary.json").open("w" if resume else "x") as handle:
        json.dump(summary, handle, indent=2); handle.write("\n")
    return summary


def load_checker_smoke(workspace_root, smoke_id):
    if not IDENTIFIER.fullmatch(smoke_id):
        raise ValueError("Invalid smoke ID")
    root = Path(workspace_root).resolve()
    base = root / ".checker-smoke" / smoke_id
    path = base / "summary.json"
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Unsafe checker smoke evidence")
    summary = json.loads(path.read_text())
    if (summary.get("schema_version") != SMOKE_VERSION or summary.get("smoke_id") != smoke_id
            or summary.get("gate") not in SMOKE_GATES
            or summary.get("frozen", {}).get("checker_implementation") != checker_implementation()
            or [row["problem_id"] for row in summary.get("problems", [])] != list(SMOKE_PROBLEMS)
            or summary.get("formal_submissions") != 0 or summary.get("main_tasks_started") != 0
            or summary.get("local_generated_checker_executions") != 0):
        raise ValueError("Checker smoke identity/integrity mismatch")
    hashes = summary.get("files_sha256", {})
    expected_files = {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file() and p.name not in {"summary.json", ".run.lock"}}
    if set(hashes) != expected_files or not hashes:
        raise ValueError("Checker smoke evidence files differ")
    for name, expected in hashes.items():
        source = base / name
        if Path(name).is_absolute() or ".." in Path(name).parts or source.is_symlink() or file_hash(source) != expected:
            raise ValueError("Checker smoke artifact drift")
    return {"summary": summary, "file_hashes": {str(path.relative_to(root)): file_hash(path),
        **{str((base / name).relative_to(root)): value for name, value in hashes.items()}}}


def main(argv=None):
    import argparse
    from dotenv import load_dotenv
    from agent.config import ClientSettings
    parser = argparse.ArgumentParser(description="Eight public-problem checker-only smoke; no formal submissions")
    parser.add_argument("--smoke-id", required=True)
    parser.add_argument("--model-config", type=Path, default=Path("config/models.infrastructure.yaml"))
    parser.add_argument("--workspace-root", type=Path, default=Path("workspace"))
    parser.add_argument("--confirm-model-call", action="store_true")
    parser.add_argument("--confirm-custom-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_model_call or not args.confirm_custom_run:
        parser.error("Checker-only model calls and Custom Runs require explicit confirmation flags")
    load_dotenv()
    settings = ClientSettings.from_environment(model_config_override=str(args.model_config))
    summary = run_checker_smoke(settings, args.model_config, args.workspace_root, args.smoke_id, resume=args.resume)
    print(json.dumps({"smoke_id": args.smoke_id, "gate": summary["gate"], "accounting": summary["accounting"]}, indent=2))
    return 2 if summary["gate"] == "CHECKER_LIVE_SMOKE_FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
