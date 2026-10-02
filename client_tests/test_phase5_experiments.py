from __future__ import annotations

import csv
import io
import json
from dataclasses import replace

import httpx
import pytest

from agent.config import ClientSettings
from agent.core.harness import HarnessPolicy
from agent.core.policy import ModelPolicy
from agent.execution import ExecutionService, RunRequest, fingerprint
from agent.models.registry import ModelDefinition, ModelRegistry
from agent.models.types import LLMResponse, ModelProfile, TokenUsage
from agent.oj_client.client import OJClient
from agent.oj_client.types import JudgeFeedback
from experiments.cli import main
from experiments.config import ExperimentConfig, Strategy
from experiments.results import csv_text, summarize_rows, task_row
from experiments.runner import ExperimentRunner
from client_tests.test_phase4_harness import GOOD, OJ


class Provider:
    base_url = "https://model.test/v1"
    timeout_seconds = 1

    def __init__(self, *, usage=TokenUsage(100, 50), interrupt=False):
        self.calls, self.usage, self.interrupt = [], usage, interrupt
        self.parameters = []

    def complete(self, messages, *, model, profile, parameters):
        self.calls.append((profile.value, messages))
        self.parameters.append(dict(parameters))
        if self.interrupt:
            self.interrupt = False
            raise KeyboardInterrupt()
        content = "plan" if "Phase: PLAN" in messages[-1].content else GOOD
        return LLMResponse(content, self.usage, "fake", model, profile)


class ExperimentOJ(OJ):
    base_url = "https://oj.test"
    send_custom_run_code_alias = False

    def __init__(self, *args, feedback_mode="verdict_only", **kwargs):
        super().__init__(*args, **kwargs)
        self.feedback_mode = feedback_mode

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_feedback_mode(self):
        return {"mode": self.feedback_mode, "source": "GET /api/v1/me",
                "status": "confirmed" if self.feedback_mode else "not_advertised"}

    def get_problem(self, problem_id):
        return replace(super().get_problem(problem_id), problem_id=problem_id)


def service(tmp_path, *, provider=None, oj=None, priced=True):
    provider = provider or Provider()
    oj = oj or ExperimentOJ(["AC"] * 100)
    definition = ModelDefinition("fake", "fake-model", {"max_tokens": 4096},
        1 if priced else None, 1 if priced else None, "CNY" if priced else None, 32000)
    registry = ModelRegistry({"fake": provider}, {p: definition for p in ModelProfile})
    result = ExecutionService(ClientSettings("https://oj.test", "test-token"), tmp_path,
        registry=registry, base_policy=ModelPolicy(), client_factory=lambda *a, **kw: oj)
    return result, provider, oj


def config(**kwargs):
    defaults = dict(name="fixture", problems=["sum"], repetitions=1, total_cost_cny=1,
        model_config="fixture-models.yaml", strategies=[
            Strategy("standard-code", "code-only", "standard"),
            Strategy("standard-harness", "harness-loop", "standard")])
    defaults.update(kwargs)
    return ExperimentConfig(**defaults)


def test_shared_request_and_experiment_defaults_require_verdict_only():
    for request in (RunRequest("sum"), config().request("sum", config().strategies[0])):
        assert request.expected_feedback_mode == "verdict_only"
        assert request.require_feedback_mode


@pytest.mark.parametrize("actual", [None])
@pytest.mark.parametrize("mode", ["code-only", "harness-loop"])
def test_default_shared_request_blocks_unknown_before_model_calls(tmp_path, actual, mode):
    executor, provider, oj = service(tmp_path, oj=ExperimentOJ(feedback_mode=actual))
    request = RunRequest("sum", mode, "standard")
    workspace = executor.create_workspace(request, "default-condition")
    result = executor.run(request, workspace.state.task_id, workspace=workspace)
    assert result.terminal_status == "condition_mismatch"
    assert workspace.state.expected_feedback_mode == "verdict_only"
    assert workspace.state.actual_feedback_mode == actual
    artifact = workspace.read_json("artifacts/result.json")
    assert artifact["actual_feedback_mode"] == actual
    assert artifact["effective_feedback_mode"] is None
    assert artifact["feedback_policy"] == "formal_verdict_only_v1"
    assert not provider.calls and not oj.submissions and not oj.problem_calls


def test_verdict_only_wa_without_hidden_testcase_can_debug_and_accept(tmp_path):
    class VerdictOnlyOJ(ExperimentOJ):
        def get_feedback(self, submission_id):
            self.feedback_calls += 1
            return JudgeFeedback(self.verdicts[int(submission_id) - 1], "Wrong answer")

    executor, provider, oj = service(tmp_path, oj=VerdictOnlyOJ(["WA", "AC"]))
    manifest = ExperimentRunner(executor).run(config(strategies=[
        Strategy("harness", "harness-loop", "standard")]), "verdict-only-debug")
    row = manifest["tasks"][0]
    assert row["solved"] and row["actual_feedback_mode"] == "verdict_only"
    assert row["conditions_verified"] and len(provider.calls) == 3
    assert len(oj.submissions) == 2 and oj.feedback_calls == 1
    debug_prompt = provider.calls[-1][1][-1].content
    assert "Phase: DEBUG" in debug_prompt and '"verdict": "WA"' in debug_prompt
    assert "Wrong answer" not in debug_prompt and "hidden testcase" not in debug_prompt


def test_batch_two_modes_json_csv_audit_and_idempotent_resume(tmp_path):
    executor, provider, oj = service(tmp_path)
    runner = ExperimentRunner(executor)
    manifest = runner.run(config(), "two-modes")
    assert manifest["status"] == "completed"
    assert [row["solved"] for row in manifest["tasks"]] == [True, True]
    assert [row["llm_calls"] for row in manifest["tasks"]] == [1, 2]
    assert oj.run_calls == 1 and oj.feedback_calls == 0 and len(oj.submissions) == 2
    assert len(provider.calls) == 3
    assert all(row["audit"]["status"] == "consistent" for row in manifest["tasks"])
    assert all(row["expected_feedback_mode"] == row["actual_feedback_mode"] == "verdict_only"
               and row["conditions_verified"] for row in manifest["tasks"])
    root = tmp_path / ".experiments/two-modes"
    rows = list(csv.DictReader(io.StringIO((root / "tasks.csv").read_text())))
    assert len(rows) == 2 and rows[0]["actual_feedback_mode"] == "verdict_only"
    assert json.loads((root / "tasks.json").read_text()) == manifest["tasks"]
    assert len(json.loads((root / "summary.json").read_text())) == 2
    summary = json.loads((root / "summary.json").read_text())
    assert sum(group["input_tokens"] for group in summary) == 300
    assert sum(group["calls_per_model_profile"]["standard"] for group in summary) == 3
    assert all(group["unknown_usage_tasks"] == 0 for group in summary)
    assert runner.run(config(), "two-modes", resume=True) == manifest
    assert len(provider.calls) == 3 and len(oj.submissions) == 2


def test_global_reservations_stop_later_calls(tmp_path):
    executor, provider, oj = service(tmp_path)
    manifest = ExperimentRunner(executor).run(config(total_cost_cny=0.04), "budget")
    assert len(provider.calls) == 1 and len(oj.submissions) == 1
    assert manifest["tasks"][1]["terminal_status"] == "budget_exhausted"
    assert manifest["budget_committed_cny"] <= 0.04


def test_provider_bound_violation_stops_batch_and_cannot_resume(tmp_path):
    executor, provider, oj = service(tmp_path, provider=Provider(usage=TokenUsage(32001, 50)))
    runner = ExperimentRunner(executor)
    manifest = runner.run(config(), "unsafe-bound")
    assert manifest["status"] == "stopped" and len(provider.calls) == 1
    assert manifest["tasks"][1]["status"] == "pending" and not oj.submissions
    with pytest.raises(ValueError, match="Cannot resume"):
        runner.run(config(), "unsafe-bound", resume=True)
    assert len(provider.calls) == 1


def test_ten_submissions_is_independent_per_task_for_same_problem(tmp_path):
    executor, _, oj = service(tmp_path, oj=ExperimentOJ(["WA"] * 30))
    cfg = config(total_cost_cny=3, strategies=[Strategy("harness", "harness-loop", "standard"),
        Strategy("second-harness", "harness-loop", "strong"),
        Strategy("code", "code-only", "strong")])
    manifest = ExperimentRunner(executor).run(cfg, "submission-cap")
    assert len(oj.submissions) == 21
    assert [t["submission_attempts"] for t in manifest["tasks"]] == [10, 10, 1]
    assert all(t["effective_budget"]["max_cost_cny"] == 1 for t in manifest["tasks"])
    assert all(t["budget_committed_cny"] <= 1 for t in manifest["tasks"])
    assert manifest["budget_committed_cny"] <= 3


def test_unknown_prices_and_usage_are_not_free_and_failures_are_preserved(tmp_path):
    executor, provider, oj = service(tmp_path, priced=False)
    result = ExperimentRunner(executor).run(config(), "missing-prices")
    assert not provider.calls and not oj.submissions
    assert all(t["terminal_status"] == "budget_unverifiable" for t in result["tasks"])
    other = tmp_path / "usage"
    executor, provider, oj = service(other, provider=Provider(usage=None))
    result = ExperimentRunner(executor).run(config(), "missing-usage")
    assert not oj.submissions and len(provider.calls) == 2
    assert all(t["estimated_cost"] is None and t["input_tokens"] is None for t in result["tasks"])
    assert result["budget_committed_cny"] > 0
    assert all(g["estimated_cost"] is None for g in summarize_rows(result["tasks"]))
    assert all(g["input_tokens"] is None and g["unknown_usage_tasks"] == 1
               for g in summarize_rows(result["tasks"]))
    for row in result["tasks"]:
        from pathlib import Path
        artifact = json.loads((Path(row["workspace"]) / "artifacts/result.json").read_text())
        assert artifact["input_tokens"] is None and artifact["output_tokens"] is None
        assert artifact["usage_missing_count"] == 1
    from experiments.summarize import summarize
    assert all(g["average_input_tokens"] is None and g["unknown_usage_tasks"] == 1
               for g in summarize(other).values())


@pytest.mark.parametrize("actual,expected,required", [(None, None, True), (None, "full", False),
    ("verdict_only", "full", False)])
def test_unverified_or_mismatched_conditions_prevent_paid_calls(tmp_path, actual, expected, required):
    executor, provider, oj = service(tmp_path, oj=ExperimentOJ(feedback_mode=actual))
    manifest = ExperimentRunner(executor).run(config(expected_feedback_mode=expected,
        require_feedback_mode=required), "conditions")
    assert not provider.calls and not oj.submissions
    assert all(t["terminal_status"] == "condition_mismatch" and not t["conditions_verified"] for t in manifest["tasks"])


def test_unknown_actual_mode_is_never_filled_from_client_expectation_or_diagnostics(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ExperimentOJ(["AC"] * 20, feedback_mode=None))
    manifest = ExperimentRunner(executor).run(config(expected_feedback_mode=None,
        require_feedback_mode=False), "unknown-mode")
    assert len(provider.calls) == 3 and len(oj.submissions) == 2
    assert all(t["actual_feedback_mode"] is None and not t["conditions_verified"] for t in manifest["tasks"])
    assert all(not g["conditions_verified"] for g in summarize_rows(manifest["tasks"]))


def test_resume_known_submission_uses_existing_id_and_no_extra_paid_call(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ExperimentOJ(["AC"], interrupt="wait"))
    cfg = config(strategies=[Strategy("harness", "harness-loop", "standard")])
    runner = ExperimentRunner(executor)
    with pytest.raises(KeyboardInterrupt):
        runner.run(cfg, "resume-known")
    saved = json.loads((tmp_path / ".experiments/resume-known/manifest.json").read_text())
    assert saved["status"] == "interrupted" and saved["budget_committed_cny"] > 0
    result = runner.run(cfg, "resume-known", resume=True)
    assert result["tasks"][0]["solved"] and len(provider.calls) == 2 and len(oj.submissions) == 1


@pytest.mark.parametrize("mode", ["code-only", "harness-loop"])
def test_interrupted_paid_call_is_not_reissued_by_batch_resume(tmp_path, mode):
    executor, provider, oj = service(tmp_path, provider=Provider(interrupt=True))
    cfg = config(strategies=[Strategy("one", mode, "standard")])
    runner = ExperimentRunner(executor)
    with pytest.raises(KeyboardInterrupt):
        runner.run(cfg, "resume-unknown")
    saved = json.loads((tmp_path / ".experiments/resume-unknown/tasks.json").read_text())[0]
    assert saved["estimated_cost"] is None and saved["input_tokens"] is None
    result = runner.run(cfg, "resume-unknown", resume=True)
    assert len(provider.calls) == 1 and not oj.submissions
    row = result["tasks"][0]
    assert row["terminal_status"] == "result_unknown" and row["uncertain_llm_calls"] == 1
    assert row["estimated_cost"] is None and row["input_tokens"] is None and row["output_tokens"] is None
    assert row["cost_estimate_status"] == "pending_usage" and row["budget_committed_cny"] > 0
    from experiments.summarize import summarize
    assert next(iter(summarize(tmp_path).values()))["average_estimated_cost"] is None
    from dashboard.repository.workspace import WorkspaceRepository
    summary = WorkspaceRepository(tmp_path).summary(row["task_id"])
    assert summary.estimated_cost is None and summary.input_tokens is None and summary.output_tokens is None


def test_route_price_endpoint_drift_and_duplicate_experiment_are_rejected(tmp_path):
    executor, provider, oj = service(tmp_path)
    runner = ExperimentRunner(executor)
    runner.run(config(), "frozen")
    with pytest.raises(FileExistsError):
        runner.run(config(), "frozen")
    provider.base_url = "https://different-model.test"
    with pytest.raises(ValueError, match="differ"):
        runner.run(config(), "frozen", resume=True)
    assert len(provider.calls) == 3 and len(oj.submissions) == 2


def test_comparison_keys_separate_full_verdict_only_unknown_and_fixed_mixed(tmp_path):
    executor, _, _ = service(tmp_path)
    manifest = ExperimentRunner(executor).run(config(), "group")
    row = manifest["tasks"][0]
    rows = [row, {**row, "comparison_key": fingerprint("full"), "actual_feedback_mode": "full"},
        {**row, "comparison_key": fingerprint("unknown"), "actual_feedback_mode": None, "conditions_verified": False}]
    assert len(summarize_rows(rows)) == 3
    mixed = config(strategies=[Strategy("mixed", "harness-loop")])
    result = ExperimentRunner(executor).run(mixed, "mixed")
    assert [p for p, _ in executor.registry.provider("fake").calls][-2:] == ["strong", "standard"]
    assert result["tasks"][0]["configuration_fingerprint"] != manifest["tasks"][1]["configuration_fingerprint"]


def test_trace_corruption_is_reported_not_treated_as_verified(tmp_path):
    executor, _, _ = service(tmp_path)
    request = RunRequest("sum", "code-only", "standard")
    workspace = executor.create_workspace(request, "audit")
    executor.run(request, "audit", workspace=workspace)
    workspace.write_text("events.jsonl", workspace.read_text("events.jsonl") + "{broken\n42\n")
    row = task_row(workspace, {"task_id": "audit", "strategy": "code", "mode": "code-only"})
    assert row["audit"]["status"] == "incomplete_or_inconsistent"


def test_csv_preserves_unknown_and_escapes_formula_cells():
    text = csv_text([{"problem": "=CMD()", "cost": None, "usage_status": "missing_usage", "calls": {"strong": 1}}])
    row = next(csv.DictReader(io.StringIO(text)))
    assert row["problem"].startswith("'=CMD") and row["cost"] == ""
    assert row["usage_status"] == "missing_usage" and json.loads(row["calls"]) == {"strong": 1}


@pytest.mark.parametrize("changes", [{"repetitions": 0}, {"total_cost_cny": float('nan')},
    {"problems": []}, {"problems": ["sum", "sum"]}, {"name": "../escape"},
    {"repetitions": 1001}, {"schema_version": "unknown"},
    {"problems": ["sum", " sum "]}, {"problems": [1]}])
def test_invalid_config_never_reaches_network(changes):
    with pytest.raises((ValueError, TypeError)):
        config(**changes)


def test_relative_yaml_paths_and_five_explicit_targets(tmp_path):
    with pytest.raises(ValueError, match="roles must be a mapping"):
        Strategy("bad-roles", "harness-loop", roles=None)
    path = tmp_path / "experiment.yaml"
    path.write_text("name: test\nproblems: [sum]\nrepetitions: 1\ntotal_cost_cny: 1\nmodel_config: models.yaml\nstrategies:\n- {name: code, mode: code-only, profile: standard}\n")
    cfg = ExperimentConfig.from_yaml(path)
    assert cfg.model_config == tmp_path / "models.yaml"
    assert cfg.expected_feedback_mode == "verdict_only" and cfg.require_feedback_mode
    from pathlib import Path
    template = ExperimentConfig.from_yaml(Path(__file__).parents[1] / "config/experiment.example.yaml")
    assert {(s.mode, s.profile) for s in template.strategies if s.profile} == {
        (m,p) for m in ("code-only", "harness-loop") for p in ("standard", "strong")}
    assert len(template.strategies) == 5 and template.task_count == 5
    assert template.strategies[-1].roles == {"PLAN": "strong", "CODE": "standard", "DEBUG": "standard"}
    assert template.expected_feedback_mode == "verdict_only" and template.require_feedback_mode
    assert template.harness.max_cost_cny == 1 and template.total_cost_cny == 5


def test_formal_five_groups_one_repetition_verdict_only_and_8192_output_bound(tmp_path):
    from pathlib import Path
    cfg = ExperimentConfig.from_yaml(Path(__file__).parents[1] / "config/experiment.full.yaml")
    assert cfg.problems == ["T1003"] and cfg.repetitions == 1
    assert cfg.expected_feedback_mode == "verdict_only" and cfg.require_feedback_mode
    executor, provider, oj = service(tmp_path)
    executor.registry.models = {p: replace(d, parameters={"max_tokens": 8192})
                               for p, d in executor.registry.models.items()}
    result = ExperimentRunner(executor).run(cfg, "five-groups")
    assert len(result["tasks"]) == 5 and all(t["solved"] for t in result["tasks"])
    assert [t["llm_calls"] for t in result["tasks"]] == [1, 1, 2, 2, 2]
    assert all(t["effective_budget"]["max_cost_cny"] == 1 for t in result["tasks"])
    assert [p for p, _ in provider.calls][-2:] == ["strong", "standard"]
    assert all(p["max_tokens"] == 8192 for p in provider.parameters)
    assert len(oj.submissions) == 5 and oj.run_calls == 3
    assert all(t["conditions_verified"] and t["actual_feedback_mode"] == "verdict_only" for t in result["tasks"])
    assert all(t["audit"]["status"] == "consistent" for t in result["tasks"])


def test_per_task_cost_is_not_shared_and_budget_drift_or_old_allocation_cannot_resume(tmp_path):
    executor, provider, _ = service(tmp_path, oj=ExperimentOJ(["WA"] * 100))
    cfg = config(total_cost_cny=3, harness=HarnessPolicy(max_cost_cny=1.2), strategies=[
        Strategy("one", "harness-loop", "standard"), Strategy("two", "harness-loop", "standard")])
    runner = ExperimentRunner(executor)
    result = runner.run(cfg, "task-budget")
    assert all(t["effective_budget"]["max_cost_cny"] == 1.2 for t in result["tasks"])
    assert result["budget_allocation"] == "independent_tasks_v1"
    count = len(provider.calls)
    with pytest.raises(ValueError, match="differ"):
        runner.run(replace(cfg, harness=HarnessPolicy(max_cost_cny=2)), "task-budget", resume=True)
    conditions = runner._conditions(cfg)
    manifest = tmp_path / ".experiments/task-budget/manifest.json"
    saved = json.loads(manifest.read_text())
    saved["configuration_fingerprint"] = fingerprint({"config": cfg.as_dict(), "conditions": conditions})
    manifest.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="differ"):
        runner.run(cfg, "task-budget", resume=True)
    assert len(provider.calls) == count


def test_cli_requires_confirmation_before_configuration_or_network():
    with pytest.raises(SystemExit) as exc:
        main(["run", "missing.yaml", "--experiment-id", "not-run"])
    assert exc.value.code == 2


@pytest.mark.parametrize("cost,calls", [(None, 1), (0.01, 0), (2, 1)])
def test_agent_cli_code_only_default_and_custom_cost_guards(tmp_path, monkeypatch, capsys, cost, calls):
    import agent.cli as cli
    executor, provider, oj = service(tmp_path)
    monkeypatch.setenv("OJ_BASE_URL", "https://oj.test")
    monkeypatch.setenv("OJ_API_TOKEN", "test-token")
    monkeypatch.setattr(cli.ModelRegistry, "from_yaml", lambda path: executor.registry)
    monkeypatch.setattr(cli.ModelPolicy, "from_yaml", lambda path: ModelPolicy())
    monkeypatch.setattr(cli, "OJClient", lambda *a, **kw: oj)
    args = ["solve", "sum", "--mode", "code-only", "--profile", "standard", "--task-id", "cli-budget",
            "--workspace-root", str(tmp_path), "--http-timeout", "1", "--poll-interval", "0",
            "--deadline", "1", "--confirm-model-call", "--confirm-submit"]
    if cost is not None:
        args += ["--max-cost-cny", str(cost)]
    assert cli.main(args) == (0 if calls else 2)
    assert json.loads(capsys.readouterr().out)["llm_calls"] == calls
    assert len(provider.calls) == calls and len(oj.submissions) == calls


def test_experiment_cli_per_task_override_does_not_raise_batch_cap(tmp_path, monkeypatch, capsys):
    from pathlib import Path
    import experiments.cli as cli
    executor, provider, oj = service(tmp_path)
    monkeypatch.setattr(cli, "ExecutionService", lambda *a, **kw: executor)
    monkeypatch.setattr(cli.ClientSettings, "from_environment", lambda **kw: executor.settings)
    path = Path(__file__).parents[1] / "config/experiment.full.yaml"
    assert cli.main(["run", str(path), "--experiment-id", "cli-batch-budget", "--workspace-root", str(tmp_path),
                     "--max-cost-cny", "0.01", "--confirm-model-call", "--confirm-submit"]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == 5
    manifest = json.loads((tmp_path / ".experiments/cli-batch-budget/manifest.json").read_text())
    assert all(t["effective_budget"]["max_cost_cny"] == 0.01 for t in manifest["tasks"])
    assert manifest["config"]["total_cost_cny"] == 5
    assert not provider.calls and not oj.submissions


@pytest.mark.parametrize("mode,expected", [("full", "full"), ("verdict_only", "verdict_only"),
    (None, None), ("future", None)])
def test_feedback_mode_is_only_explicit_server_metadata(mode, expected):
    requests = []
    client = OJClient("https://oj.test", "test-token", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: (requests.append(r) or httpx.Response(200, json={"role": "admin", "feedback_mode": mode})))) )
    observation = client.get_feedback_mode()
    assert observation["mode"] == expected
    assert len(requests) == 1 and requests[0].url.path == "/api/v1/me"
