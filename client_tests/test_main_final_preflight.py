"""Final-main preparation uses local Fakes only; no live execution is permitted."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.core.checker import SampleGatePolicy as GatePolicy
from agent.core.context import ContextBuilder
from agent.execution import fingerprint
from agent.models.types import ModelProfile
from experiments.config import ExperimentConfig
from experiments.main_preflight import (agent_source_hash, checker_matrix, check_main_receipt,
    extend_main_preflight, final_receipt)
from experiments.order import execution_order
from experiments.preflight import (METRIC_FIELDS, PreflightError, build_preflight, checker_preflight,
    load_preflight, save_preflight)
from experiments.results import cached_usage, summarize_rows, summarize_subsets
from experiments.runner import ExperimentRunner
from client_tests.test_experiment_preflight import FixtureProbe, git_state
from client_tests.test_phase5_experiments import Provider, ExperimentOJ, config, service

ROOT = Path(__file__).parents[1]


def SampleGatePolicy(**kwargs):
    return GatePolicy(**{"version": "sample_check_v2", **kwargs})


def main_fixture(tmp_path):
    executor, provider, oj = service(tmp_path)
    model_path = tmp_path / "models.yaml"
    model_path.write_text("# local fake definition\n")
    cfg = replace(ExperimentConfig.from_yaml(ROOT / "config/experiment.small.yaml"), model_config=model_path)
    # Legacy receipt tests keep their explicit v1 policy. v2 smoke binding has
    # separate tests and must not be bypassed by a fixture with no smoke.
    cfg = replace(cfg, sample_checking=GatePolicy(version="sample_check_v1", overrides=cfg.sample_checking["overrides"]).as_dict())
    seen = ["CF2127C", "CF2125D", "CF2127D"]
    pilot_config = replace(cfg, problems=seen, total_cost_cny=15, preparation={"five_conditions": True},
        problem_metadata={key: value for key, value in cfg.problem_metadata.items() if key in seen},
        sample_checking={**cfg.sample_checking, "overrides": {
            key: value for key, value in cfg.sample_checking["overrides"].items() if key in seen}})
    probe = FixtureProbe(feedback="verdict_only")
    def problem(identifier):
        return {"problem_id": identifier, "metadata_available": True,
            "checker": {"token": "tokens", "special": "testlib", "unknown": "lines"}[
                cfg.sample_checking["overrides"][identifier]["kind"]],
            "rating": cfg.problem_metadata[identifier]["rating"], "checker_source": "fixture:metadata",
            "sample_count": 1, "problem_input_hash": fingerprint(oj.get_problem(identifier).as_dict())}
    probe.problem = problem
    pilot_preflight = build_preflight(pilot_config, executor.settings, registry=executor.registry,
                                     policy=executor.base_policy, probe=probe, git_root=tmp_path)
    pilot = {"run_id": "pilot-fixture", "problem_ids": seen,
        "task_ids": [row["task_id"] for row in ExperimentRunner(executor)._plan(pilot_config, "pilot-fixture")],
        "preflight": pilot_preflight, "config": pilot_config.as_dict(), "file_hashes": {},
        "agent_source_hash": agent_source_hash(ROOT),
        "validation": {"checker": {"problem_ids": seen, "kind": "token"},
                       "dedup": {"live_dedup_hit_observed": False}},
        "receipt": {"cost": {"estimated_cost_cny": 1.0598962}}}
    base = build_preflight(cfg, executor.settings, registry=executor.registry, policy=executor.base_policy,
                           probe=probe, git_root=tmp_path)
    report = extend_main_preflight(base, cfg, main_experiment_id="main-fixture", workspace_root=tmp_path, pilot=pilot)
    return cfg, executor, provider, oj, report, pilot


def test_main_receipt_exact_layout_order_contamination_and_budget(tmp_path, git_state):
    cfg, executor, provider, oj, report, pilot = main_fixture(tmp_path)
    frozen = save_preflight(report, tmp_path, "main-preflight")
    assert load_preflight(tmp_path, "main-preflight") == frozen
    receipt = check_main_receipt(frozen, tmp_path, "main-fixture")
    assert receipt == final_receipt(frozen) and receipt["gate"] == "MAIN_V1_READY_WITH_WARNINGS"
    assert receipt["task_count"] == 60 and receipt["problem_count"] == 12 and receipt["condition_count"] == 5
    rows = report["checker_summary"]["problems"]
    assert {row["problem_id"] for row in rows if row["pilot_seen"]} == set(pilot["problem_ids"])
    assert all(row["pilot_run_id"] == ("pilot-fixture" if row["pilot_seen"] else None) for row in rows)
    main = frozen["main_final_preflight"]
    assert len(main["pilot_seen_problems"]) == 3 and len(main["pilot_unseen_problems"]) == 9
    matrix = main["checker_matrix"]
    assert matrix["checker_counts"] == {"exact": 0, "token": 4, "float": 0, "special": 5, "unknown": 3}
    assert matrix["requires_llm_checker"] == 8 and len(matrix["strata"]["llm_generated_unverified"]) == 8
    assert all(not row["checker_live_coverage"] and row["live_validation_required"]
               for row in rows if row["requires_llm_checker"])
    order = report["execution"]["execution_order"]
    assert order == execution_order(cfg, receipt["execution_seed"])["execution_order"]
    assert len(order) == len({(r["problem_id"], r["condition"]) for r in order}) == 60
    assert {(r["problem_id"], r["condition"]) for r in order} == {
        (p, s.name) for p in cfg.problems for s in cfg.strategies}
    assert len({tuple(r["condition"] for r in order[i:i+5]) for i in range(0, 60, 5)}) > 1
    assert not set(pilot["task_ids"]) & {r["task_id"] for r in main["planned_tasks"]}
    budget = main["budget_summary"]
    assert budget["maximum_allocated_budget_cny"] == budget["batch_budget_cny"] == 60
    assert budget["expected_spend_cny"] is None and not provider.calls and not oj.submissions and not oj.run_calls
    assert frozen["fingerprint"]["sha256"] != pilot["preflight"]["fingerprint"]["sha256"]


@pytest.mark.parametrize("failure", ["missing", "blocked", "fingerprint", "experiment_id", "snapshot", "history"])
def test_main_receipt_rejects_before_workspace_or_model_custom_formal(tmp_path, git_state, failure):
    cfg, executor, provider, oj, report, _ = main_fixture(tmp_path)
    if failure == "missing":
        report.pop("main_final_preflight")
    elif failure == "blocked":
        report["main_final_preflight"]["gate"] = "MAIN_V1_BLOCKED"
    elif failure == "fingerprint":
        report["fingerprint"]["sha256"] = "0" * 64
    elif failure == "history":
        report["main_final_preflight"]["pilot_evidence_hashes"] = {"missing-history.json": "0" * 64}
    save_preflight(report, tmp_path, "main-preflight")
    if failure == "snapshot":
        path = tmp_path / ".experiments/main-preflight/preflight/checker-matrix.json"
        path.write_text("{}")
    identifier = "other-main" if failure == "experiment_id" else "main-fixture"
    before_gets = oj.problem_calls
    with pytest.raises(PreflightError):
        ExperimentRunner(executor).run(cfg, identifier, preflight_id="main-preflight")
    assert not (tmp_path / ".experiments" / identifier).exists()
    assert not provider.calls and not oj.submissions and not oj.run_calls and oj.problem_calls == before_gets


def test_valid_receipt_allows_only_local_launch_preparation(tmp_path, git_state):
    cfg, executor, provider, oj, report, _ = main_fixture(tmp_path)
    frozen = save_preflight(report, tmp_path, "main-preflight")
    def stop_after_plan(manifest):
        assert len(manifest["tasks"]) == 60
        assert sum(row["pilot_seen"] for row in manifest["tasks"]) == 15
        assert all(row["status"] == "pending" for row in manifest["tasks"])
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ExperimentRunner(executor, on_export=stop_after_plan).run(cfg, "main-fixture", preflight_id="main-preflight")
    manifest = json.loads((tmp_path / ".experiments/main-fixture/manifest.json").read_text())
    assert manifest["main_final_receipt"] == final_receipt(frozen)
    assert not provider.calls and not oj.submissions and not oj.run_calls
    assert not any((tmp_path / row["task_id"]).exists() for row in manifest["tasks"])
    subsets = json.loads((tmp_path / ".experiments/main-fixture/pilot-subsets.json").read_text())
    assert subsets["subsets"]["pilot_seen"]["task_count"] == 15
    assert subsets["subsets"]["pilot_unseen"]["task_count"] == 45


def test_seen_task_does_not_import_pilot_result_candidate_or_response(tmp_path, git_state):
    cfg, executor, provider, oj, report, pilot = main_fixture(tmp_path)
    old = tmp_path / pilot["task_ids"][0]; old.mkdir()
    poison = "PILOT-ONLY-RESULT-CANDIDATE-RESPONSE"
    for name in ("checkpoint.json", "state.json", "solution.cpp", "events.jsonl"):
        (old / name).write_text(poison)
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    save_preflight(report, tmp_path, "main-preflight")
    def stop_after_seen(manifest):
        if any(row["pilot_seen"] and row["status"] == "completed" for row in manifest["tasks"]):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        ExperimentRunner(executor, on_export=stop_after_seen).run(cfg, "main-fixture", preflight_id="main-preflight")
    manifest = json.loads((tmp_path / ".experiments/main-fixture/manifest.json").read_text())
    seen = next(row for row in manifest["tasks"] if row["pilot_seen"] and row["status"] == "completed")
    assert seen["task_id"] not in pilot["task_ids"] and seen["llm_calls"] > 0 and seen["candidate_versions"]
    assert seen["formal_submissions"] > 0 and seen["reused_results"] == 0
    assert seen["pilot_run_id"] == "pilot-fixture"
    text = (tmp_path / seen["task_id"] / "events.jsonl").read_text()
    assert poison not in text and poison not in json.dumps(manifest)
    assert all(poison not in message.content for _, messages in provider.calls for message in messages)
    assert {p.name: p.read_bytes() for p in old.iterdir()} == before


@pytest.mark.parametrize("change", ["prompt", "model", "checker", "feedback", "dedup", "problems", "order"])
def test_main_fingerprint_drift_rejects_before_any_execution(tmp_path, git_state, monkeypatch, change):
    cfg, executor, provider, oj, report, _ = main_fixture(tmp_path)
    save_preflight(report, tmp_path, "main-preflight")
    if change == "prompt":
        monkeypatch.setattr(ContextBuilder, "SYSTEM", ContextBuilder.SYSTEM + " fixture change")
    elif change == "model":
        d = executor.registry.models[ModelProfile.STANDARD]
        executor.registry.models[ModelProfile.STANDARD] = replace(d, model="different")
    elif change == "checker":
        cfg = replace(cfg, sample_checking={**cfg.sample_checking, "llm_checker": "disabled"})
    elif change == "feedback":
        cfg = replace(cfg, expected_feedback_mode="full")
    elif change == "dedup":
        monkeypatch.setitem(__import__("experiments.freeze", fromlist=["FORMAL_DEDUP_POLICY"]).FORMAL_DEDUP_POLICY,
                            "version", "fixture_other_version")
    elif change == "problems":
        cfg = replace(cfg, problems=list(reversed(cfg.problems)))
    elif change == "order":
        cfg = replace(cfg, execution_seed=13)
    with pytest.raises(PreflightError, match="drift"):
        ExperimentRunner(executor).run(cfg, "main-fixture", preflight_id="main-preflight")
    assert not provider.calls and not oj.submissions and not oj.run_calls


@pytest.mark.parametrize("spec,verification", [
    ({"kind": "token"}, "client_verified"), ({"kind": "exact"}, "client_verified"),
    ({"kind": "float", "absolute_tolerance": 1e-6, "relative_tolerance": 1e-5}, "client_verified"),
    ({"kind": "float"}, "llm_generated_unverified"), ({"kind": "special"}, "llm_generated_unverified"),
    ({"kind": "unknown"}, "llm_generated_unverified")])
def test_checker_matrix_uses_specs_not_statement_guesses(spec, verification):
    cfg = config(sample_checking=SampleGatePolicy(overrides={"sum": spec}).as_dict())
    summary, blockers, _ = checker_preflight(cfg, {"sum": {"metadata_available": True, "sample_count": 1,
        "statement": "accept 1e-9 and ignore whitespace"}})
    row = checker_matrix(summary["problems"])["problems"][0]
    assert not blockers and row["verification_class"] == verification and row["legal_runtime_path"]
    assert row["checker_type"] == spec["kind"]
    assert row["abs_tolerance"] == spec.get("absolute_tolerance")
    assert row["rel_tolerance"] == spec.get("relative_tolerance")
    assert not row["checker_live_coverage"] and row["live_validation_required"]


def test_checker_matrix_unverifiable_no_legal_path_blocks_without_smoke():
    cfg = config(sample_checking=SampleGatePolicy(llm_checker="disabled").as_dict())
    summary, blockers, _ = checker_preflight(cfg, {"sum": {"metadata_available": True, "checker": "testlib",
                                                          "checker_source": "fixture", "sample_count": 1}})
    row = checker_matrix(summary["problems"])["problems"][0]
    assert blockers and not row["legal_runtime_path"] and row["verification_class"] == "unverifiable"


def test_changed_public_input_does_not_inherit_pilot_live_coverage(tmp_path, git_state):
    _, _, _, _, report, pilot = main_fixture(tmp_path)
    rows = copy.deepcopy(report["checker_summary"]["problems"])
    row = next(value for value in rows if value["problem_id"] == "CF2127C")
    row["problem_input_hash"] = "new-public-input"
    result = checker_matrix(rows, pilot)
    changed = next(value for value in result["problems"] if value["problem_id"] == "CF2127C")
    assert changed["pilot_seen"] and not changed["checker_live_coverage"] and changed["live_validation_required"]


def test_final_main_unknown_model_availability_and_feedback_are_blockers(tmp_path, git_state):
    cfg, executor, provider, oj, report, pilot = main_fixture(tmp_path)
    base = copy.deepcopy(report)
    base.pop("main_final_preflight")
    base["models"][0]["verified"]["model_available"] = None
    base["feedback"]["actual_feedback_mode"] = None
    base["blockers"].append("feedback_mode_unknown")
    result = extend_main_preflight(base, cfg, main_experiment_id="main-fixture", workspace_root=tmp_path, pilot=pilot)
    assert result["main_final_preflight"]["gate"] == "MAIN_V1_BLOCKED"
    assert "feedback_mode_unknown" in result["blockers"]
    assert "model_availability_unresolved:standard" in result["blockers"]
    assert not provider.calls and not oj.run_calls and not oj.submissions


def test_final_main_cli_invalid_config_emits_blocked_receipt_without_network(tmp_path, monkeypatch):
    from experiments.cli import main
    monkeypatch.setattr("experiments.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("experiments.cli.ClientSettings.from_environment", lambda **kw: pytest.fail("No remote setup"))
    path = tmp_path / "invalid.yaml"; path.write_text("name: incomplete\n")
    assert main(["preflight", str(path), "--experiment-id", "main-preflight", "--main-experiment-id", "main-fixture",
                 "--pilot-run-id", "pilot-fixture", "--workspace-root", str(tmp_path)]) == 2
    report = load_preflight(tmp_path, "main-preflight")
    receipt = json.loads((tmp_path / ".experiments/main-preflight/preflight/final-receipt.json").read_text())
    assert receipt["gate"] == "MAIN_V1_BLOCKED" and receipt["blockers"] == ["experiment_config_invalid"]
    assert report["side_effects"]["read_only_http_gets"] == []


def test_shared_agent_policy_change_is_blocker_and_no_tuning_occurs(tmp_path, git_state):
    cfg, executor, provider, oj, report, pilot = main_fixture(tmp_path)
    before = copy.deepcopy(cfg.as_dict())
    base = copy.deepcopy(report)
    base.pop("main_final_preflight")
    pilot["agent_source_hash"] = "different-pilot-agent"
    other = extend_main_preflight(base, cfg, main_experiment_id="main-fixture", workspace_root=tmp_path, pilot=pilot)
    assert other["main_final_preflight"]["gate"] == "MAIN_V1_BLOCKED"
    assert "shared_freeze_differs_from_pilot:agent_source_hash" in other["blockers"]
    assert cfg.as_dict() == before and not provider.calls and not oj.submissions


def test_shared_oj_endpoint_change_is_not_pilot_live_validation(tmp_path, git_state):
    cfg, executor, provider, oj, report, pilot = main_fixture(tmp_path)
    base = copy.deepcopy(report)
    base.pop("main_final_preflight")
    base["fingerprint"]["components"]["oj_endpoint_fingerprint"] = "different-oj"
    result = extend_main_preflight(base, cfg, main_experiment_id="main-fixture", workspace_root=tmp_path, pilot=pilot)
    assert "shared_freeze_differs_from_pilot:oj_endpoint_fingerprint" in result["blockers"]
    assert result["main_final_preflight"]["gate"] == "MAIN_V1_BLOCKED" and not provider.calls and not oj.run_calls


@pytest.mark.parametrize("value,expected", [(0, 0), (20, 20), (None, None), (-1, None), (True, None), (101, None)])
def test_cached_tokens_only_from_valid_observed_metadata(value, expected):
    response = {"usage": {"input_tokens": 100}, "usage_metadata": {"prompt_tokens_details": {"cached_tokens": value}}}
    assert cached_usage(response) == expected


def test_result_schema_keeps_formal_history_usage_and_final_outcome(tmp_path):
    class MetadataProvider(Provider):
        def complete(self, *args, **kwargs):
            return replace(super().complete(*args, **kwargs), actual_response_model="fake-model", finish_reason="stop",
                           usage_metadata={"prompt_tokens_details": {"cached_tokens": 20}})
    executor, provider, oj = service(tmp_path, provider=MetadataProvider(), oj=ExperimentOJ(["TLE", "AC"]))
    cfg = config(strategies=[config().strategies[1]])
    manifest = ExperimentRunner(executor).run(cfg, "report-fixture")
    row = manifest["tasks"][0]
    assert set(METRIC_FIELDS) <= set(row)
    assert row["task_final_outcome"] == row["final_verdict"] == "AC"
    assert [r["verdict"] for r in row["all_formal_verdict_observations"]] == ["TLE", "AC"]
    assert row["formal_recovery_to_ac"] and row["cached_tokens"] == 60
    assert row["provider_reported_cost"] is None and row["billed_cost"] is None
    assert row["model_output_metadata"][-1]["extraction_result"] == "candidate_created"
    summary = summarize_rows([row])[0]
    assert summary["task_final_outcomes"] == {"AC": 1} and summary["all_formal_verdicts"] == {"TLE": 1, "AC": 1}
    subsets = summarize_subsets([{**row, "pilot_seen": True}, {**row, "task_id": "other", "pilot_seen": False}])
    assert [subsets["subsets"][key]["task_count"] for key in ("all_problems", "pilot_seen", "pilot_unseen")] == [2, 1, 1]


def test_invalid_output_does_not_replace_earlier_formal_wa_or_extract_prose(tmp_path):
    class InvalidProvider(Provider):
        def complete(self, messages, **kwargs):
            result = super().complete(messages, **kwargs)
            if "Phase: DEBUG" in messages[-1].content:
                result = replace(result, content="Use dynamic programming.", finish_reason="length")
            return result
    executor = service(tmp_path, provider=InvalidProvider(), oj=ExperimentOJ(["WA"]))[0]
    result = ExperimentRunner(executor).run(config(strategies=[config().strategies[1]]), "invalid-fixture")
    row = result["tasks"][0]
    assert row["task_final_outcome"] == "invalid_model_output" and row["final_verdict"] == "WA"
    assert row["all_formal_verdict_observations"][0]["verdict"] == "WA"
    assert row["model_output_metadata"][-1]["finish_reason"] == "length"
    assert row["model_output_metadata"][-1]["extraction_result"] == "failed"
    assert row["candidate_version_count"] == 1 and row["cached_tokens"] is None
