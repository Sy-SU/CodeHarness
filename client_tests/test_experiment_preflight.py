"""All HTTP uses MockTransport; all solve/resume work uses existing Fake agents."""
from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml

from agent.config import ClientSettings
from agent.core.checker import SampleGatePolicy as GatePolicy
from agent.core.context import ContextBuilder
from agent.core.policy import ModelPolicy
from agent.models.registry import ModelRegistry
from agent.models.types import ModelProfile, AgentRole
from experiments.config import ExperimentConfig, Strategy
from experiments.freeze import frozen_fingerprint, git_snapshot, prompt_snapshot
from experiments.order import DEFAULT_EXECUTION_SEED, execution_order, validate_order
from experiments.preflight import (METRIC_FIELDS, ReadOnlyProbe, build_preflight, checker_preflight,
    check_frozen_preflight, feedback_preflight, load_preflight, save_preflight)
from experiments.preflight_models import apply_model_metadata, configured_models, registry_profiles
from experiments.runner import ExperimentRunner
from experiments.cli import main
from client_tests.test_phase5_experiments import config, service, ExperimentOJ, Provider

ROOT = Path(__file__).parents[1]


def SampleGatePolicy(**kwargs):
    """Legacy preflight cases keep their explicit v2 policy."""
    return GatePolicy(**{"version": "sample_check_v2", **kwargs})


@pytest.fixture
def git_state(monkeypatch):
    value = {"git_commit_sha": "a" * 40, "git_dirty": True, "git_diff_hash": "b" * 64}
    monkeypatch.setattr("experiments.freeze.git_snapshot", lambda root: dict(value))
    return value


class FixtureProbe:
    def __init__(self, *, feedback="full", checker="tokens", sample_count=1, model="fake-model"):
        self.mode, self.kind, self.count, self.model = feedback, checker, sample_count, model
        self.requests = []

    def feedback(self):
        return {"mode": self.mode, "source": "fixture:GET /me"}

    def problem(self, problem_id):
        return {"problem_id": problem_id, "checker": self.kind, "checker_source": "fixture:problem_metadata",
                "sample_count": self.count, "metadata_available": True, "rating": None}

    def provider_models(self, provider):
        return {"data": [{"id": self.model}]}


def fake_preflight(tmp_path, *, probe=None, cfg=None, executor=None):
    executor = executor or service(tmp_path)[0]
    model_path = tmp_path / "models.yaml"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text("# fixture, never read as provider configuration\n")
    cfg = cfg or config(model_config=model_path)
    cfg = replace(cfg, model_config=model_path)
    report = build_preflight(cfg, executor.settings, registry=executor.registry, policy=executor.base_policy,
        probe=probe or FixtureProbe(), git_root=tmp_path)
    return cfg, executor, report


@pytest.mark.parametrize("spec,kind,trusted,llm,status", [
    ({"kind": "exact"}, "exact", True, False, "READY"),
    ({"kind": "token"}, "token", True, False, "READY"),
    ({"kind": "float", "absolute_tolerance": 1e-6, "relative_tolerance": 1e-6}, "float", True, False, "READY"),
    ({"kind": "float", "absolute_tolerance": 1e-6}, "float", False, True, "READY_WITH_WARNINGS"),
    ({"kind": "special"}, "special", False, True, "READY_WITH_WARNINGS"),
    ({"kind": "unknown"}, "unknown", False, True, "READY_WITH_WARNINGS"),
])
def test_checker_classification_uses_runtime_policy_without_execution(spec, kind, trusted, llm, status):
    spec = {**spec, "source": "explicit fixture"}
    cfg = config(sample_checking=SampleGatePolicy(overrides={"sum": spec}).as_dict())
    summary, blockers, warnings = checker_preflight(cfg, {"sum": {"metadata_available": True, "sample_count": 2}})
    row = summary["problems"][0]
    assert row["checker_type"] == kind and row["checker_source"] == "explicit fixture"
    assert row["checker_resolution_source"] == "explicit_experiment_config"
    assert row["verified_checker"] is trusted and row["requires_llm_generated_checker"] is llm
    assert row["preflight_status"] == status and not blockers
    assert row["effective_checker_type"] == ("llm_generated_unverified" if llm else kind)
    assert summary["strata"]["remote_verifiable"] == 0


@pytest.mark.parametrize("wire,kind", [("tokens", "token"), ("testlib", "special"), ("lines", "unknown"),
    ("yesno", "unknown"), ("llm_generated_unverified", "unknown"), (None, "unknown")])
def test_metadata_aliases_do_not_guess_semantics_or_tolerances(wire, kind):
    summary, _, _ = checker_preflight(config(), {"sum": {"metadata_available": True,
        "checker": wire, "checker_source": "MiniOJ API", "sample_count": 1,
        "statement": "absolute or relative error 1e-9"}})
    row = summary["problems"][0]
    assert row["checker_type"] == kind and row["float_abs_tolerance"] is None
    assert row["float_rel_tolerance"] is None


@pytest.mark.parametrize("policy,blocked", [
    (SampleGatePolicy(llm_checker="disabled"), True),
    (SampleGatePolicy(llm_checker="advisory"), True),
    (SampleGatePolicy(llm_checker="disabled", on_unverifiable="submit"), False),
    (SampleGatePolicy(), False),
])
def test_untrusted_checker_only_blocks_when_config_has_no_formal_path(policy, blocked):
    summary, blockers, _ = checker_preflight(config(sample_checking=policy.as_dict()),
        {"sum": {"metadata_available": True, "checker": "testlib", "checker_source": "API", "sample_count": 1}})
    assert bool(blockers) is blocked and len(summary["problems"]) == 1
    assert summary["checker_distribution"]["special"] == 1


def test_code_only_and_zero_samples_never_require_generated_checker():
    for cfg, count in [(config(strategies=[Strategy("code", "code-only", "standard")]), 1), (config(), 0)]:
        summary, blockers, _ = checker_preflight(cfg, {"sum": {"sample_count": count}})
        assert not summary["problems"][0]["requires_llm_generated_checker"] and not blockers


def test_rating_and_checker_metadata_conflicts_are_blockers_without_rewriting_config():
    cfg = config(problem_metadata={"sum": {"rating": 1200, "rating_source": "fixture"}},
                 sample_checking=SampleGatePolicy(overrides={"sum": {"kind": "token"}}).as_dict())
    before = copy.deepcopy(cfg.as_dict())
    summary, blockers, _ = checker_preflight(cfg, {"sum": {"metadata_available": True, "rating": 1400,
        "checker": "testlib", "checker_source": "API", "sample_count": 1}})
    assert blockers == ["problem_rating_drift:sum", "checker_metadata_conflicts_with_frozen_override:sum"]
    assert cfg.as_dict() == before and summary["problems"][0]["checker_type"] == "token"


@pytest.mark.parametrize("actual,status,effective", [("verdict_only", "READY", "verdict_only"),
    ("full", "READY", "verdict_only"), (None, "BLOCKED", None)])
def test_feedback_native_projected_and_unknown(actual, status, effective):
    result = feedback_preflight(config(), {"mode": actual, "source": "GET /me"})
    assert result["feedback_preflight_status"] == status
    assert result["actual_feedback_mode"] == actual and result["effective_feedback_mode"] == effective
    assert result["feedback_policy"] == "formal_verdict_only_v1"


def test_configured_snapshot_and_partial_verified_metadata_are_independent(tmp_path):
    executor, _, _ = service(tmp_path)
    snapshots, routes = configured_models(config(), executor.registry, executor.base_policy)
    model = snapshots[0]
    assert model["configured"]["max_output_tokens"] == 4096
    assert all(value is None for key, value in model["verified"].items() if key != "field_sources")
    apply_model_metadata(model, {"data": [{"id": "fake-model", "max_output_tokens": 8192,
        "context_window": 131072, "pricing": {"input_price": 2, "currency": "CNY", "unit": "per_million_tokens"}}]},
        verified_at="fixture-time")
    verified = model["verified"]
    assert verified["model_available"] and verified["max_output_tokens"] == 8192
    assert verified["context_window"] == 131072 and verified["input_limit"] is None
    assert verified["input_price"] == 2 and verified["output_price"] is None
    assert verified["verified_at"] == "fixture-time" and model["needs_live_probe"]
    assert model["configured"]["input_price"] == 1 and routes["standard-harness"]["CODE"] == "standard"


@pytest.mark.parametrize("body", [{"data": [{"id": "fake-model"}]}, {"data": [{"id": "fake-model",
    "max_tokens": 8192, "context_length": 123, "pricing": {"input_price": 2, "output_price": 8}}]}])
def test_model_id_listing_does_not_verify_limits_or_price(tmp_path, body):
    executor, _, _ = service(tmp_path)
    model = configured_models(config(), executor.registry, executor.base_policy)[0][0]
    apply_model_metadata(model, body, verified_at="now")
    assert model["verified"]["model_available"]
    assert model["verified"]["max_output_tokens"] is None
    assert model["verified"]["context_window"] is None and model["verified"]["input_price"] is None


def test_unavailable_model_blocks_but_unavailable_metadata_warns(tmp_path, git_state):
    cfg, executor, report = fake_preflight(tmp_path, probe=FixtureProbe(model="other"))
    assert report["status"] == "BLOCKED" and "model_unavailable:standard" in report["blockers"]
    probe = FixtureProbe(); probe.provider_models = lambda provider: None
    other = build_preflight(cfg, executor.settings, registry=executor.registry, policy=executor.base_policy,
        probe=probe, git_root=tmp_path)
    assert other["status"] == "READY_WITH_WARNINGS" and not other["blockers"]
    assert "model_availability_unverified:standard" in other["warnings"]


def test_get_only_preflight_sanitizes_metadata_and_resolves_only_used_profiles(tmp_path, git_state):
    model_path = tmp_path / "models.yaml"
    model_path.write_text(yaml.safe_dump({"providers": {"mock": {"type": "openai-compatible",
        "base_url_env": "MOCK_URL", "api_key_env": "MOCK_KEY", "timeout_seconds": 1,
        "supported_parameters": ["max_tokens"]}, "unused": "invalid"}, "models": {
        "standard": {"provider": "mock", "model": "visible", "parameters": {"max_tokens": 8192},
            "input_token_limit": 32768, "input_cost_per_million": 1, "output_cost_per_million": 2, "currency": "CNY"},
        "strong": "unused-invalid", "fast": "unused-invalid", "max": "unused-invalid"}}))
    registry = ModelRegistry.from_yaml(model_path, environ={"MOCK_URL": "https://model.test/v1", "MOCK_KEY": "fixture-secret"},
        required_profiles={ModelProfile.STANDARD})
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET", "No paid POST is permitted in preflight"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "visible"}], "secret": "POISON-META"})
        if request.url.path == "/api/v1/me":
            return httpx.Response(200, json={"feedback_mode": "full", "token": "POISON-META"})
        if request.url.path == "/api/v1/problems/sum":
            return httpx.Response(200, json={"problem_id": "sum", "checker": "tokens", "rating": 1200,
                "tags": ["POISON-META"], "editorial": "POISON-META"})
        assert request.url.path == "/api/v1/agent/problems/sum"
        return httpx.Response(200, json={"problem_id": "sum", "title": "sum", "statement": "Add.",
            "limits": {"time_ms": 1000, "memory_mb": 256}, "samples": [{"input": "1 2", "output": "3"}]})
    provider = registry.provider("mock")
    provider.client.close(); provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    oj_http = httpx.Client(transport=httpx.MockTransport(handler))
    settings = ClientSettings("https://oj.test", "fixture-secret", model_path)
    probe = ReadOnlyProbe(settings, client=oj_http)
    cfg = config(model_config=model_path, strategies=[Strategy("only", "code-only", "standard")])
    try:
        report = build_preflight(cfg, settings, registry=registry, policy=ModelPolicy(), probe=probe, git_root=tmp_path)
        assert report["status"] == "READY_WITH_WARNINGS" and not report["blockers"]
        assert len(requests) == 4 and len(report["side_effects"]["read_only_http_gets"]) == 4
        text = json.dumps(report)
        assert "POISON-META" not in text and "fixture-secret" not in text
        assert report["models"][0]["verified"]["model_available"] is True
        assert report["models"][0]["verified"]["max_output_tokens"] is None
        assert report["checker_summary"]["problems"][0]["sample_count"] == 1
        assert list(registry.models) == [ModelProfile.STANDARD]
        assert report["side_effects"]["llm_calls"] == report["side_effects"]["paid_cost_cny"] == 0
    finally:
        oj_http.close(); provider.client.close()


def test_prompt_bundle_contains_content_and_dynamic_assembly_not_only_paths():
    bundle = prompt_snapshot()
    assert {entry["prompt_name"] for entry in bundle["prompts"]} == {
        "system", "PLAN", "CODE", "DEBUG", "sample_checker_generation"}
    assert all(len(entry["sha256"]) == 64 and entry["prompt_version"] for entry in bundle["prompts"])
    assert "sections" in bundle["assembly"]["context_builder_source"]
    checker = next(entry for entry in bundle["prompts"] if entry["prompt_name"] == "sample_checker_generation")
    assert "OUTPUT CHECKER" in checker["messages"][-1]["content"]
    assert "{statement}" in checker["messages"][-1]["content"]
    assert bundle == prompt_snapshot()


@pytest.mark.parametrize("change", ["prompt", "problem", "model", "checker", "feedback", "harness",
    "git_diff", "model_file", "oj_endpoint", "provider_endpoint"])
def test_every_material_condition_drift_rejected_before_any_execution(tmp_path, monkeypatch, git_state, change):
    cfg, executor, report = fake_preflight(tmp_path)
    frozen = save_preflight(report, tmp_path, "freeze")
    assert check_frozen_preflight(cfg, executor, "freeze")["fingerprint"] == frozen["fingerprint"]
    if change == "prompt":
        monkeypatch.setattr(ContextBuilder, "SYSTEM", ContextBuilder.SYSTEM + " changed")
    elif change == "problem":
        cfg = replace(cfg, problems=["sum", "other"])
    elif change == "model":
        definition = executor.registry.models[ModelProfile.STANDARD]
        executor.registry.models[ModelProfile.STANDARD] = replace(definition, model="changed")
    elif change == "checker":
        cfg = replace(cfg, sample_checking=SampleGatePolicy(llm_checker="disabled").as_dict())
    elif change == "feedback":
        cfg = replace(cfg, expected_feedback_mode="full")
    elif change == "harness":
        cfg = replace(cfg, harness=replace(cfg.harness, max_cost_cny=2))
    elif change == "git_diff":
        git_state["git_diff_hash"] = "c" * 64
    elif change == "model_file":
        cfg.model_config.write_text("changed\n")
    elif change == "oj_endpoint":
        executor.settings = replace(executor.settings, oj_base_url="https://other.test")
    elif change == "provider_endpoint":
        executor.registry.provider("fake").base_url = "https://other.test/v1"
    with pytest.raises(ValueError, match="drift"):
        ExperimentRunner(executor).run(cfg, "must-not-start", preflight_id="freeze")
    assert not (tmp_path / ".experiments/must-not-start").exists()
    assert not executor.registry.provider("fake").calls
    assert load_preflight(tmp_path, "freeze") == frozen


def test_seeded_order_has_all_sixty_unique_tasks_and_balanced_positions():
    cfg = ExperimentConfig.from_yaml(ROOT / "config/experiment.small.yaml")
    plan = execution_order(cfg)
    assert plan["execution_seed"] == DEFAULT_EXECUTION_SEED
    assert plan == execution_order(cfg) and plan != execution_order(cfg, 13)
    rows = plan["execution_order"]
    assert len(rows) == 60 and len({(r["problem_id"], r["condition"]) for r in rows}) == 60
    blocks = [[r["condition"] for r in rows[i:i+5]] for i in range(0, 60, 5)]
    assert len({tuple(block) for block in blocks}) > 1
    for group in (blocks[:5], blocks[5:10]):
        for position in range(5):
            assert len({block[position] for block in group}) == 5
    with pytest.raises(ValueError, match="ordering"):
        validate_order(cfg, rows[:-1])
    with pytest.raises(ValueError, match="ordering"):
        validate_order(cfg, rows[:-1] + [rows[0]])


def test_storage_never_overwrites_prior_report_and_detects_artifact_tampering(tmp_path, git_state):
    _, _, report = fake_preflight(tmp_path)
    frozen = save_preflight(report, tmp_path, "immutable")
    assert load_preflight(tmp_path, "immutable") == frozen
    with pytest.raises(FileExistsError):
        save_preflight(report, tmp_path, "immutable")
    path = tmp_path / ".experiments/immutable/preflight/prompt-snapshot.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        load_preflight(tmp_path, "immutable")


def test_symlink_storage_is_rejected_without_touching_destination(tmp_path, git_state):
    _, _, report = fake_preflight(tmp_path)
    target = tmp_path / "target"; target.mkdir()
    (tmp_path / ".experiments").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="Unsafe"):
        save_preflight(report, tmp_path, "escape")
    assert not list(target.iterdir())


def test_guarded_runner_uses_saved_order_exports_metrics_and_resumes_without_calls(tmp_path, git_state):
    cfg, executor, report = fake_preflight(tmp_path, probe=FixtureProbe(feedback="verdict_only"))
    save_preflight(report, tmp_path, "freeze")
    runner = ExperimentRunner(executor)
    result = runner.run(cfg, "run", preflight_id="freeze")
    expected = [row["condition"] for row in report["execution"]["execution_order"]]
    assert [row["strategy"] for row in result["tasks"]] == expected
    assert all(set(METRIC_FIELDS) <= set(row) for row in result["tasks"])
    assert all(row["official_performance"] is None for row in result["tasks"])
    assert all(row["candidate_versions"] and row["wall_time"] is not None for row in result["tasks"])
    calls = len(executor.registry.provider("fake").calls)
    assert runner.run(cfg, "run", resume=True) == result
    assert len(executor.registry.provider("fake").calls) == calls
    with pytest.raises(ValueError, match="replace"):
        runner.run(cfg, "run", resume=True, preflight_id="different")


def test_drift_on_resume_leaves_saved_manifest_and_trace_unchanged(tmp_path, git_state, monkeypatch):
    executor, provider, oj = service(tmp_path, oj=ExperimentOJ(interrupt="wait"))
    cfg, _, report = fake_preflight(tmp_path, executor=executor,
        cfg=config(strategies=[Strategy("harness", "harness-loop", "standard")]),
        probe=FixtureProbe(feedback="verdict_only"))
    save_preflight(report, tmp_path, "freeze")
    runner = ExperimentRunner(executor)
    with pytest.raises(KeyboardInterrupt):
        runner.run(cfg, "run", preflight_id="freeze")
    manifest = tmp_path / ".experiments/run/manifest.json"
    before = manifest.read_bytes(); calls, submissions = len(provider.calls), len(oj.submissions)
    monkeypatch.setattr(ContextBuilder, "SYSTEM", "changed")
    with pytest.raises(ValueError, match="drift"):
        runner.run(cfg, "run", resume=True)
    assert manifest.read_bytes() == before and len(provider.calls) == calls and len(oj.submissions) == submissions


def test_in_batch_file_change_stops_before_the_next_task(tmp_path, git_state):
    cfg, executor, report = fake_preflight(tmp_path, probe=FixtureProbe(feedback="verdict_only"))
    save_preflight(report, tmp_path, "freeze")
    def on_export(manifest):
        if any(row["status"] == "completed" for row in manifest["tasks"]):
            cfg.model_config.write_text("changed during experiment\n")
    with pytest.raises(ValueError, match="drift"):
        ExperimentRunner(executor, on_export=on_export).run(cfg, "run", preflight_id="freeze")
    result = json.loads((tmp_path / ".experiments/run/manifest.json").read_text())
    assert result["status"] == "stopped" and result["stop_reason"] == "preflight_drift"
    assert sum(row["status"] == "completed" for row in result["tasks"]) == 1


def test_unknown_feedback_and_feedback_drift_prevent_any_model_call(tmp_path, git_state):
    cfg, executor, report = fake_preflight(tmp_path, probe=FixtureProbe(feedback=None))
    assert "feedback_mode_unknown" in report["blockers"]
    save_preflight(report, tmp_path, "blocked")
    with pytest.raises(ValueError, match="BLOCKED"):
        ExperimentRunner(executor).run(cfg, "run", preflight_id="blocked")
    cfg, executor, report = fake_preflight(tmp_path, probe=FixtureProbe(feedback="full"))
    save_preflight(report, tmp_path, "full")
    with pytest.raises(ValueError, match="Feedback metadata drift"):
        ExperimentRunner(executor).run(cfg, "drift", preflight_id="full")
    assert not executor.registry.provider("fake").calls


def test_main_config_requires_preflight_before_creating_workspace(tmp_path):
    executor, provider, oj = service(tmp_path)
    cfg = ExperimentConfig.from_yaml(ROOT / "config/experiment.small.yaml")
    with pytest.raises(ValueError, match="requires --preflight-id"):
        ExperimentRunner(executor).run(cfg, "never-start")
    assert not list(tmp_path.iterdir()) and not provider.calls and not oj.submissions


def test_missing_prompt_snapshot_blocks(tmp_path, git_state, monkeypatch):
    monkeypatch.setattr("experiments.preflight.prompt_snapshot", lambda: {})
    _, _, report = fake_preflight(tmp_path)
    assert report["status"] == "BLOCKED" and "missing_prompt_snapshot" in report["blockers"]


def test_offline_preflight_and_invalid_config_emit_blocked_report_without_execution(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("experiments.cli.ExecutionService", lambda *a, **kw: pytest.fail("No execution service in preflight"))
    monkeypatch.setattr("experiments.cli.load_dotenv", lambda: None)
    monkeypatch.setenv("OJ_BASE_URL", "https://oj.test"); monkeypatch.setenv("OJ_API_TOKEN", "fixture-secret")
    monkeypatch.setattr(ReadOnlyProbe, "_oj_get", lambda *a: pytest.fail("Offline must not GET"))
    path = tmp_path / "invalid.yaml"; path.write_text("name: invalid\n")
    args = ["preflight", str(path), "--experiment-id", "invalid", "--workspace-root", str(tmp_path), "--offline"]
    assert main(args) == 2
    report = load_preflight(tmp_path, "invalid")
    assert report["blockers"] == ["experiment_config_invalid"]
    assert report["side_effects"]["read_only_http_gets"] == []
    with pytest.raises(SystemExit):
        main(args)


def test_real_git_snapshot_includes_untracked_and_staged_changes(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "--allow-empty", "-m", "fixture")
    clean = git_snapshot(tmp_path)
    assert clean["git_dirty"] is False
    file = tmp_path / "prompt.py"; file.write_text("first\n")
    added = git_snapshot(tmp_path)
    assert added["git_dirty"] and added["git_diff_hash"] != clean["git_diff_hash"]
    file.write_text("second\n")
    changed = git_snapshot(tmp_path)
    assert changed["git_diff_hash"] != added["git_diff_hash"]
    git("add", "prompt.py")
    assert git_snapshot(tmp_path)["git_diff_hash"] != changed["git_diff_hash"]


@pytest.mark.parametrize("malformed", [{}, {"data": None}, {"data": [{"id": "fake-model"}], "has_more": True},
    {"data": [{"id": "fake-model"}, {"id": "fake-model"}]}])
def test_incomplete_model_catalogue_preserves_unknown_instead_of_false(tmp_path, malformed):
    executor = service(tmp_path)[0]
    model = configured_models(config(), executor.registry, executor.base_policy)[0][0]
    apply_model_metadata(model, malformed, verified_at="now")
    assert model["verified"]["model_available"] is None
    assert model["verified"]["verified_at"] is None and model["needs_live_probe"]


def test_mixed_condition_resolves_real_plan_code_and_debug_models_only(tmp_path, git_state):
    executor = service(tmp_path)[0]
    executor.registry.models = {profile: replace(executor.registry.models[profile], model=profile.value + "-model")
                                for profile in (ModelProfile.STANDARD, ModelProfile.STRONG)}
    cfg = config(strategies=[Strategy("mixed", "harness-loop", roles={"PLAN": "strong", "CODE": "standard", "DEBUG": "standard"})])
    probe = FixtureProbe()
    probe.provider_models = lambda provider: {"data": [{"id": "standard-model"}, {"id": "strong-model"}]}
    _, _, report = fake_preflight(tmp_path, cfg=cfg, executor=executor, probe=probe)
    assert report["condition_routes"] == {"mixed": {"PLAN": "strong", "CODE": "standard", "DEBUG": "standard"}}
    assert {model["model_id"] for model in report["models"]} == {"standard-model", "strong-model"}
    assert not report["blockers"] and not executor.registry.provider("fake").calls


@pytest.mark.parametrize("case", ["prices", "currency", "output", "input", "call_cap", "reservation"])
def test_invalid_budget_or_startup_configuration_blocks_without_calls(tmp_path, git_state, case):
    executor = service(tmp_path, priced=case != "prices")[0]
    cfg = config()
    definition = executor.registry.models[ModelProfile.STANDARD]
    if case == "currency":
        definition = replace(definition, currency="USD")
    elif case == "output":
        definition = replace(definition, parameters={"max_tokens": 0})
    elif case == "input":
        definition = replace(definition, input_token_limit=None)
    elif case == "call_cap":
        cfg = replace(cfg, harness=replace(cfg.harness, max_llm_calls=1))
    elif case == "reservation":
        cfg = replace(cfg, harness=replace(cfg.harness, max_cost_cny=0.05))
    executor.registry.models[ModelProfile.STANDARD] = definition
    _, _, report = fake_preflight(tmp_path, cfg=cfg, executor=executor)
    assert report["status"] == "BLOCKED" and any("budget_config_invalid" in b for b in report["blockers"])
    assert not executor.registry.provider("fake").calls
    if case == "currency":
        assert "configured_call_reservation_cny" not in report["models"][0]


def test_configured_output_exceeds_explicit_verified_metadata_limit_is_blocker(tmp_path, git_state):
    probe = FixtureProbe()
    probe.provider_models = lambda provider: {"data": [{"id": "fake-model", "max_output_tokens": 1024}]}
    _, _, report = fake_preflight(tmp_path, probe=probe)
    assert "configured_output_exceeds_verified_limit:standard" in report["blockers"]
    assert report["models"][0]["verified"]["max_output_tokens"] == 1024


def test_file_change_during_metadata_gets_is_detected_as_mixed_snapshot(tmp_path, git_state):
    cfg, executor, _ = fake_preflight(tmp_path)
    probe = FixtureProbe()
    def provider_models(provider):
        cfg.model_config.write_text("changed during GETs\n")
        return {"data": [{"id": "fake-model"}]}
    probe.provider_models = provider_models
    report = build_preflight(cfg, executor.settings, registry=executor.registry, policy=executor.base_policy,
        probe=probe, git_root=tmp_path)
    assert "fingerprint_inconsistent" in report["blockers"]


def test_verification_timestamps_and_evidence_do_not_change_configured_fingerprint(tmp_path, git_state):
    cfg, executor, report = fake_preflight(tmp_path)
    models, routes = configured_models(cfg, executor.registry, executor.base_policy)
    one = frozen_fingerprint(cfg, models, routes, executor.settings.oj_base_url, git_root=tmp_path)
    for model in models:
        apply_model_metadata(model, {"data": [{"id": "fake-model", "max_output_tokens": 8192}]}, verified_at="different-time")
    two = frozen_fingerprint(cfg, models, routes, executor.settings.oj_base_url, git_root=tmp_path)
    assert one == two == report["fingerprint"]


def test_public_input_drift_blocks_before_any_paid_call(tmp_path, git_state):
    cfg, executor, report = fake_preflight(tmp_path, probe=FixtureProbe(feedback="verdict_only"))
    report["checker_summary"]["problems"][0]["problem_input_hash"] = "different-public-input"
    save_preflight(report, tmp_path, "freeze")
    with pytest.raises(ValueError, match="Public problem input drift"):
        ExperimentRunner(executor).run(cfg, "run", preflight_id="freeze")
    assert not executor.registry.provider("fake").calls


def test_offline_preflight_does_not_touch_any_http_method(tmp_path, git_state):
    cfg, executor, _ = fake_preflight(tmp_path)
    probe = FixtureProbe()
    probe.feedback = probe.problem = probe.provider_models = lambda *a: pytest.fail("Offline must not GET")
    report = build_preflight(cfg, executor.settings, registry=executor.registry, policy=executor.base_policy,
        probe=probe, git_root=tmp_path, offline=True)
    assert "feedback_mode_unknown" in report["blockers"]
    assert report["side_effects"]["read_only_http_gets"] == []
    assert all(model["verified"]["model_available"] is None for model in report["models"])


def test_preflight_cli_local_parse_error_and_bad_seed_produce_reviewable_blocked_reports(tmp_path, monkeypatch):
    monkeypatch.setattr("experiments.cli.load_dotenv", lambda: None)
    monkeypatch.setenv("OJ_BASE_URL", "https://oj.test"); monkeypatch.setenv("OJ_API_TOKEN", "fixture-secret")
    bad = tmp_path / "bad.yaml"; bad.write_text("problems: [\n")
    assert main(["preflight", str(bad), "--experiment-id", "parse", "--workspace-root", str(tmp_path)]) == 2
    assert load_preflight(tmp_path, "parse")["blockers"] == ["experiment_config_invalid"]
    assert main(["preflight", str(ROOT / "config/experiment.small.yaml"), "--experiment-id", "seed",
                 "--execution-seed", "-1", "--workspace-root", str(tmp_path)]) == 2
    assert load_preflight(tmp_path, "seed")["blockers"] == ["invalid_execution_ordering"]


def test_pilot_uses_three_client_verifiable_public_problems_and_same_five_conditions():
    main_cfg = ExperimentConfig.from_yaml(ROOT / "config/experiment.small.yaml")
    pilot = ExperimentConfig.from_yaml(ROOT / "config/experiment.pilot.yaml")
    assert pilot.task_count == 15 and pilot.repetitions == 1 and pilot.strategies == main_cfg.strategies
    assert set(pilot.problems) <= set(main_cfg.problems)
    assert {pilot.problem_metadata[p]["rating"] for p in pilot.problems} == {1400, 1600, 1800}
    assert all(pilot.sample_checking["overrides"][p]["kind"] == "token" for p in pilot.problems)


def test_same_preflight_rejects_raw_yaml_file_drift_and_replacement(tmp_path, git_state):
    cfg, executor, _ = fake_preflight(tmp_path)
    path = tmp_path / "experiment.yaml"; path.write_text(yaml.safe_dump(cfg.as_dict()))
    report = build_preflight(cfg, executor.settings, registry=executor.registry, policy=executor.base_policy,
        probe=FixtureProbe(feedback="verdict_only"), git_root=tmp_path, config_path=path)
    frozen = save_preflight(report, tmp_path, "freeze")
    assert check_frozen_preflight(cfg, executor, "freeze")["fingerprint"] == frozen["fingerprint"]
    path.write_text(path.read_text() + "# changed file\n")
    with pytest.raises(ValueError, match="experiment_config_file_hash"):
        check_frozen_preflight(cfg, executor, "freeze")


def test_preflight_cli_rejects_irrelevant_execution_flags_before_any_work(monkeypatch):
    monkeypatch.setattr("experiments.cli.load_dotenv", lambda: pytest.fail("Reject invalid options first"))
    for option in (["--max-cost-cny", "2"], ["--model-config", "models.yaml"], ["--llm-checker", "disabled"], ["--preflight-id", "old"]):
        with pytest.raises(SystemExit):
            main(["preflight", "config/experiment.small.yaml", "--experiment-id", "unused", *option])


def test_local_review_snapshot_dependency_is_resolved_without_a_model_review(tmp_path, git_state):
    executor = service(tmp_path)[0]
    executor.base_policy = ModelPolicy({**executor.base_policy.mapping, AgentRole.REVIEW: ModelProfile.FAST})
    cfg = config(strategies=[Strategy("mixed", "harness-loop", roles={"PLAN": "strong", "CODE": "standard", "DEBUG": "standard"})])
    assert registry_profiles(cfg, executor.base_policy) == {ModelProfile.STANDARD, ModelProfile.STRONG, ModelProfile.FAST}
    _, _, report = fake_preflight(tmp_path, cfg=cfg, executor=executor)
    assert report["runtime_snapshot_profile_dependencies"] == ["fast"]
    assert {model["profile"] for model in report["models"]} == {"standard", "strong"}
    assert all("REVIEW" not in roles for roles in report["condition_routes"].values())
    assert not executor.registry.provider("fake").calls


def test_preflight_does_not_certify_legacy_whitespace_checker_assumptions():
    summary, blockers, _ = checker_preflight(config(sample_checking=None), {"sum": {"sample_count": 1}})
    assert "checker_policy_not_current:sum" in blockers
    assert summary["problems"][0]["checker_type"] == "token"
    assert summary["problems"][0]["verified_checker"] is False


def test_nonfinite_generation_parameter_is_blocked_before_provider_calls(tmp_path, git_state):
    executor = service(tmp_path)[0]
    definition = executor.registry.models[ModelProfile.STANDARD]
    executor.registry.models[ModelProfile.STANDARD] = replace(definition, parameters={"max_tokens": 4096, "temperature": float("nan")})
    _, _, report = fake_preflight(tmp_path, executor=executor)
    assert "generation_parameters_invalid:standard" in report["blockers"]
    assert not executor.registry.provider("fake").calls
