"""v3 integration/regression tests use Fakes; never execute code or paid HTTP."""
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from agent.core.checker import CheckerSpec, SampleGatePolicy, sample_mode
from agent.core.harness import HarnessLoop
from agent.oj_client.client import OJResultUnknownError, OJProtocolError, OJHTTPError
from agent.oj_client.types import ClientErrorKind, CustomRunResult, ProblemSample
from agent.workspace.task import TaskWorkspace
from experiments.config import ExperimentConfig
from experiments.freeze import prompt_snapshot
from experiments.main_preflight import extend_main_preflight, check_main_receipt
from experiments.preflight import build_preflight, save_preflight, check_frozen_preflight
from client_tests.test_phase4_harness import OJ, Router, GOOD, BAD, events, make_agent, good_variant
from client_tests.test_phase5_experiments import service, config, ExperimentOJ
from client_tests.test_experiment_preflight import FixtureProbe, git_state
from client_tests.test_main_final_preflight import main_fixture

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: pytest.fail("v3 regressions permit no real HTTP"))


class OutputOJ(OJ):
    def __init__(self, kind="testlib", **kw):
        super().__init__(**kw)
        self.kind = kind

    def get_problem(self, problem_id):
        return replace(super().get_problem(problem_id), extra_fields={"checker": self.kind})

    def run_code(self, code, stdin):
        if self.sample_results or self.interrupt == "sample":
            return super().run_code(code, stdin)
        self.run_calls += 1
        return CustomRunResult("OK", stdout="another possible output", exit_code=0)


@pytest.mark.parametrize("spec,mode", [
    (CheckerSpec("exact"), "semantic_check"), (CheckerSpec("token"), "semantic_check"),
    (CheckerSpec("float", absolute_tolerance=0, relative_tolerance=0), "semantic_check"),
    (CheckerSpec("float"), "execution_only"), (CheckerSpec("special"), "execution_only"),
    (CheckerSpec("unknown"), "execution_only")])
def test_v3_decision_matrix_uses_trust_not_model(spec, mode):
    assert sample_mode(spec, SampleGatePolicy()) == mode
    assert sample_mode(spec, SampleGatePolicy(), remote_verified=True) == "semantic_check"


@pytest.mark.parametrize("policy", [{"llm_checker": "submit_on_pass"}, {"on_unverifiable": "submit"},
    {"unverifiable_output": "compare_tokens"}, {"version": "sample_check_v2", "unverifiable_output": "execution_only"}])
def test_policy_cannot_promote_untrusted_or_unknown_results(policy):
    with pytest.raises(ValueError):
        SampleGatePolicy(**policy)


@pytest.mark.parametrize("kind", ["tokens", "testlib", "lines"])
def test_v3_default_frozen_policy_and_zero_checker_calls(tmp_path, kind):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD]), OutputOJ(kind=kind,
        sample_results=[CustomRunResult("OK", stdout="3", exit_code=0)]))
    assert agent.run_harness_loop().solved
    assert w.read_json("checkpoint.json")["config"]["sample_checking"] == SampleGatePolicy().as_dict()
    assert w.state.checker_schema_version == "checker_state_v3"
    assert w.state.recovery_metrics["llm_checker_generation_count"] == 0
    assert not list((w.root / "artifacts/checkers").glob("*"))


def test_trusted_token_mismatch_debugs_before_first_formal(tmp_path):
    class CheckedOJ(OJ):
        def submit_solution(self, *args, **kwargs):
            assert len(events(w, "SAMPLE_RESULT")) == 2
            assert w.state.debug_iterations == 1
            assert events(w, "SAMPLE_RESULT")[0]["payload"]["status"] == "sample_wrong_answer"
            return super().submit_solution(*args, **kwargs)
    agent, w, oj = make_agent(tmp_path, Router(["plan", BAD, GOOD]), CheckedOJ())
    assert agent.run_harness_loop().solved and len(oj.submissions) == 1
    m = w.state.recovery_metrics
    assert m["sample_semantic_verified_count"] == 2 and m["sample_output_unverifiable_count"] == 0
    assert m["recovered_after_sample_failure"] and not m["sample_execution_recovery"]
    assert events(w, "STATE_CHANGE")[3]["payload"]["reason"] == "sample_wrong_answer"


@pytest.mark.parametrize("kind", ["testlib", "lines"])
@pytest.mark.parametrize("run", [CustomRunResult("OK", stdout="different", exit_code=0),
    CustomRunResult("OK", stdout=None, exit_code=0),
    CustomRunResult("OK", stdout="truncated", stdout_truncated=True, exit_code=0)])
def test_unverifiable_execution_ok_submits_without_preformal_debug(tmp_path, kind, run):
    class CheckedOJ(OutputOJ):
        def submit_solution(self, *args, **kwargs):
            assert w.state.debug_iterations == 0
            sample = events(w, "SAMPLE_RESULT")[0]["payload"]
            assert sample["sample_check_status"] == "output_unverifiable"
            assert sample["status"] == "execution_pass_output_unverifiable" and sample["passed"] is None
            assert sample["semantic_verification"] == "unavailable"
            assert sample["sample_policy"] == "execution_only" and sample["allow_formal_submit"] is True
            assert sample["checker_verification"] == "unverifiable" and "generated_checker" not in sample
            return super().submit_solution(*args, **kwargs)
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD]), CheckedOJ(kind=kind, sample_results=[run]))
    assert agent.run_harness_loop().solved and len(oj.submissions) == 1
    assert len(events(w, "REVIEW_RESULT")) == 1 and (w.root / "artifacts/review.md").is_file()
    m = w.state.recovery_metrics
    assert m["first_candidate_sample_pass"] is None and m["sample_gate_reject_count"] == 0
    assert m["sample_output_unverifiable_count"] == m["formal_submit_after_unverifiable_sample_count"] == 1
    assert m["sample_semantic_verified_count"] == m["sample_execution_failure_count"] == 0
    assert not m["recovered_after_sample_failure"] and not m["sample_execution_recovery"]


@pytest.mark.parametrize("kind", ["testlib", "lines"])
def test_formal_wa_after_unverifiable_output_has_only_formal_recovery(tmp_path, kind):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD, good_variant(1)]), OutputOJ(kind, verdicts=["WA", "AC"]))
    assert agent.run_harness_loop().solved and len(oj.submissions) == 2
    debug = [e["payload"] for e in events(w, "STATE_CHANGE") if e["payload"]["to"] == "DEBUG"]
    assert len(debug) == 1 and debug[0]["reason"] == "formal_verdict_WA"
    m = w.state.recovery_metrics
    assert m["formal_recovery_to_ac"] and m["recovery_type"] == ["formal_WA"]
    assert not m["recovered_after_sample_failure"] and not m["sample_execution_recovery"]
    assert m["sample_output_unverifiable_count"] == m["formal_submit_after_unverifiable_sample_count"] == 2
    assert m["sample_gate_reject_count"] == m["sample_execution_failure_count"] == m["llm_checker_generation_count"] == 0
    assert m["recovery_evidence"][0]["review_completed"]


@pytest.mark.parametrize("kind", ["testlib", "lines", "tokens"])
@pytest.mark.parametrize("run", [CustomRunResult("CE", stdout=""), CustomRunResult("RE", stdout="", exit_code=1),
    CustomRunResult("TLE", stdout=""), CustomRunResult("MLE", stdout=""), CustomRunResult("OLE", stdout=""),
    CustomRunResult("OK", stdout="", exit_code=7)])
def test_runtime_failure_is_execution_recovery_not_semantic_wa(tmp_path, kind, run):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD, good_variant(1)]), OutputOJ(kind,
        sample_results=[run, CustomRunResult("OK", stdout="3", exit_code=0)]))
    assert agent.run_harness_loop().solved and len(oj.submissions) == 1
    first = events(w, "SAMPLE_RESULT")[0]["payload"]
    assert first["status"] == "sample_execution_failure" and first["execution_status"] == "execution_failed"
    debug = next(e["payload"] for e in events(w, "STATE_CHANGE") if e["payload"]["to"] == "DEBUG")
    assert debug["reason"] == "sample_execution_failure"
    m = w.state.recovery_metrics
    assert m["sample_execution_failure_count"] == 1 and m["sample_execution_recovery"]
    assert m["recovery_type"] == ["sample_execution_failure"] and not m["recovered_after_sample_failure"]
    assert not m["formal_recovery_to_ac"] and m["first_candidate_sample_pass"] is None


@pytest.mark.parametrize("status", ["AC", "WA"])
def test_nonzero_exit_in_trusted_legacy_status_is_still_execution_failure(tmp_path, status):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD, good_variant(1)]), OJ(sample_results=[
        CustomRunResult(status, stdout="3", exit_code=1), CustomRunResult("OK", stdout="3", exit_code=0)]))
    assert agent.run_harness_loop().solved and len(oj.submissions) == 1
    p = events(w, "SAMPLE_RESULT")[0]["payload"]
    assert p["status"] == "sample_execution_failure" and p["verdict"] == "RE"
    assert w.state.recovery_metrics["sample_execution_recovery"]
    assert not w.state.recovery_metrics["recovered_after_sample_failure"]


@pytest.mark.parametrize("run", [CustomRunResult("IE", stdout=""), CustomRunResult("NEW", stdout=""),
    CustomRunResult("WA", stdout=""), CustomRunResult("AC", stdout=""),
    CustomRunResult("OK", stdout="same", exit_code=None),
    CustomRunResult("OK", stdout="same", exit_code=True),
    CustomRunResult("OK", stdout="same", exit_code="0")])
def test_unknown_execution_result_never_debugs_or_submits(tmp_path, run):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD]), OutputOJ(sample_results=[run]))
    result = agent.run_harness_loop()
    assert result.terminal_status in {"client_failure", "remote_infrastructure_failure"}
    assert not oj.submissions and not events(w, "SAMPLE_RESULT") and w.state.debug_iterations == 0
    assert oj.run_calls == 1 and len(events(w, "LLM_CALL")) == 2
    assert agent.run_harness_loop(resume=True).terminal_status == result.terminal_status
    assert oj.run_calls == 1


@pytest.mark.parametrize("error", [
    OJResultUnknownError("unknown", kind=ClientErrorKind.RESULT_UNKNOWN, method="POST", path="run"),
    OJProtocolError("protocol", kind=ClientErrorKind.PROTOCOL, method="POST", path="run"),
    OJHTTPError("http", kind=ClientErrorKind.HTTP, method="POST", path="run")])
def test_custom_run_http_errors_fail_closed_without_replay(tmp_path, error):
    class FailedOJ(OutputOJ):
        def run_code(self, *args, **kwargs):
            self.run_calls += 1
            raise error
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD]), FailedOJ())
    result = agent.run_harness_loop()
    assert not result.solved and not oj.submissions and w.state.debug_iterations == 0
    assert w.read_json("checkpoint.json")["pending_operation"]["kind"] == "sample_run"
    agent.run_harness_loop(resume=True)
    assert oj.run_calls == 1


def test_interrupted_sample_run_records_intent_and_never_reissues(tmp_path):
    router = Router(["plan", GOOD]); oj = OutputOJ(interrupt="sample")
    agent, w, _ = make_agent(tmp_path, router, oj)
    with pytest.raises(KeyboardInterrupt):
        agent.run_harness_loop()
    assert w.read_json("checkpoint.json")["pending_operation"]["kind"] == "sample_run"
    resumed, restored, _ = make_agent(tmp_path, router, oj, workspace=TaskWorkspace.load(tmp_path, w.state.task_id))
    result = resumed.run_harness_loop(resume=True)
    assert result.terminal_status == "result_unknown" and oj.run_calls == 1
    assert not oj.submissions and restored.state.debug_iterations == 0 and len(router.calls) == 2


def test_resume_after_committed_unverifiable_sample_does_not_repeat_run(tmp_path, monkeypatch):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD]), OutputOJ())
    loop = HarnessLoop(agent, __import__("agent.core.harness", fromlist=["HarnessPolicy"]).HarnessPolicy())
    monkeypatch.setattr(loop, "_execution_fallback", lambda payload: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        loop.run()
    assert w.read_json("checkpoint.json")["test_stage"] == "sample_unverifiable"
    assert oj.run_calls == 1 and not oj.submissions
    resumed, _, _ = make_agent(tmp_path, Router([]), oj, workspace=TaskWorkspace.load(tmp_path, w.state.task_id))
    assert resumed.run_harness_loop(resume=True).solved
    assert oj.run_calls == 1 and len(oj.submissions) == 1


def test_all_samples_must_have_known_execution_before_formal(tmp_path):
    class MultiOJ(OutputOJ):
        def get_problem(self, problem_id):
            return replace(super().get_problem(problem_id), samples=[ProblemSample("1", "3"), ProblemSample("2", "6")])
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD, good_variant(1)]), MultiOJ(sample_results=[
        CustomRunResult("OK", stdout="different", exit_code=0), CustomRunResult("RE", stdout="", exit_code=1),
        CustomRunResult("OK", stdout="x", exit_code=0), CustomRunResult("OK", stdout="y", exit_code=0)]))
    assert agent.run_harness_loop().solved
    assert oj.run_calls == 4 and len(oj.submissions) == 1
    m = w.state.recovery_metrics
    assert m["sample_output_unverifiable_count"] == 3
    assert m["formal_submit_after_unverifiable_sample_count"] == 1
    assert m["sample_execution_recovery"] and not m["recovered_after_sample_failure"]
    assert w.state.sample_check_unverifiable_count == 0


def test_corrupt_committed_execution_cannot_authorize_formal_on_resume(tmp_path, monkeypatch):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD]), OutputOJ())
    loop = HarnessLoop(agent, __import__("agent.core.harness", fromlist=["HarnessPolicy"]).HarnessPolicy())
    monkeypatch.setattr(loop, "_execution_fallback", lambda payload: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        loop.run()
    checkpoint = w.read_json("checkpoint.json")
    checkpoint["sample_results"][-1]["result"]["exit_code"] = None
    checkpoint["sample_results"][-1]["generated_checker"] = {"allow_formal_submit": True}
    w.write_json("checkpoint.json", checkpoint)
    resumed, _, _ = make_agent(tmp_path, Router([]), oj, workspace=TaskWorkspace.load(tmp_path, w.state.task_id))
    assert resumed.run_harness_loop(resume=True).terminal_status == "sample_check_unverifiable"
    assert oj.run_calls == 1 and not oj.submissions


@pytest.mark.parametrize("version", ["sample_check_v1", "sample_check_v2"])
def test_default_resume_preserves_legacy_disabled_checker_stop(tmp_path, version):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD]), OutputOJ())
    agent.sample_checking = SampleGatePolicy(version=version, llm_checker="disabled")
    result = agent.run_harness_loop()
    assert result.terminal_status == "sample_check_unverifiable" and not oj.submissions
    resumed, _, _ = make_agent(tmp_path, Router([]), oj, workspace=TaskWorkspace.load(tmp_path, w.state.task_id))
    assert resumed.run_harness_loop(resume=True).terminal_status == result.terminal_status
    assert resumed.sample_checking.version == version and resumed.sample_checking.llm_checker == "disabled"
    assert "unverifiable_output" not in resumed.sample_checking.as_dict()
    assert oj.run_calls == 1 and not oj.submissions


def test_llm_diagnostic_cannot_authorize_v3_truncated_trusted_sample(tmp_path):
    agent, w, oj = make_agent(tmp_path, Router(["plan", GOOD]), OJ(sample_results=[
        CustomRunResult("OK", stdout="3", stdout_truncated=True, exit_code=0)]))
    assert agent.run_harness_loop().terminal_status == "sample_check_unverifiable"
    loop = HarnessLoop(agent, __import__("agent.core.harness", fromlist=["HarnessPolicy"]).HarnessPolicy())
    loop.checkpoint = w.read_json("checkpoint.json")
    loop.checkpoint["sample_results"][-1]["generated_checker"] = {"allow_formal_submit": True, "decision": True}
    with pytest.raises(Exception, match="stdout_truncated"):
        loop._sample_unverifiable()
    assert not oj.submissions and w.state.debug_iterations == 0


@pytest.mark.parametrize("kind", ["special", "unknown"])
@pytest.mark.parametrize("condition", range(5))
def test_all_main_conditions_use_same_policy_and_never_generate_checker(tmp_path, kind, condition):
    main = ExperimentConfig.from_yaml(ROOT / "config/experiment.small.yaml")
    strategy = main.strategies[condition]
    class ConditionOJ(ExperimentOJ):
        def get_problem(self, problem_id):
            return replace(super().get_problem(problem_id), extra_fields={"checker": "testlib" if kind == "special" else "lines"})
        def run_code(self, *args, **kwargs):
            self.run_calls += 1
            return CustomRunResult("OK", stdout="different", exit_code=0)
    executor, provider, oj = service(tmp_path, oj=ConditionOJ())
    policy = SampleGatePolicy(overrides={"sum": {"kind": kind}}).as_dict()
    cfg = config(strategies=[strategy], sample_checking=policy)
    request = cfg.request("sum", strategy)
    workspace = executor.create_workspace(request, "condition-fixture")
    assert executor.run(request, workspace.state.task_id, workspace=workspace).solved
    m = workspace.state.recovery_metrics
    assert m["llm_checker_generation_count"] == m["checker_custom_run_count"] == 0
    assert all(e["payload"].get("purpose") != "sample_checker_generation" for e in events(workspace, "LLM_CALL"))
    assert len(oj.submissions) == 1
    if strategy.mode == "code-only":
        assert len(provider.calls) == 1 and oj.run_calls == oj.feedback_calls == 0
        assert not events(workspace, "SAMPLE_RESULT")
        assert all(m[key] == 0 for key in ("sample_semantic_verified_count", "sample_output_unverifiable_count",
            "sample_execution_failure_count", "formal_submit_after_unverifiable_sample_count"))
    else:
        assert len(provider.calls) == 2 and oj.run_calls == 1
        assert events(workspace, "SAMPLE_RESULT")[0]["payload"]["sample_policy"] == "execution_only"
        assert workspace.read_json("checkpoint.json")["config"]["sample_checking"] == policy


def v3_main_fixture(tmp_path):
    cfg, executor, provider, oj, old, pilot = main_fixture(tmp_path)
    cfg = replace(cfg, sample_checking=SampleGatePolicy(overrides=cfg.sample_checking["overrides"]).as_dict())
    pilot["receipt"]["git_sha"] = "60d6ad1ef45ab54c65d6d9c8fd8cb8d3a5f00cc9"
    prior = {p["problem_id"]: p for p in old["checker_summary"]["problems"]}
    probe = FixtureProbe(feedback="verdict_only")
    probe.problem = lambda problem: {"problem_id": problem, "metadata_available": True,
        "checker": prior[problem]["observed_checker"], "checker_source": "fixture:metadata",
        "rating": prior[problem]["rating"], "sample_count": 1, "problem_input_hash": prior[problem]["problem_input_hash"]}
    base = build_preflight(cfg, executor.settings, registry=executor.registry,
        policy=executor.base_policy, probe=probe, git_root=tmp_path)
    return cfg, executor, provider, oj, base, pilot


def test_v3_main_preflight_has_independent_paths_despite_failed_smoke(tmp_path, git_state, monkeypatch):
    # Isolate sample-path coverage from later, separately authorized algorithm
    # changes; the real checker scope rejection is tested without this stub.
    monkeypatch.setattr("experiments.checker_scope.checker_change_scope",
        lambda *args, **kw: {"blockers": [], "policy_version": "authorized_sample_check_v3"})
    cfg, executor, provider, oj, base, pilot = v3_main_fixture(tmp_path)
    failed = {"summary": {"gate": "CHECKER_LIVE_SMOKE_FAILED", "smoke_id": "old-failure", "problems": []}, "file_hashes": {}}
    report = extend_main_preflight(base, cfg, main_experiment_id="main-v3-fixture", workspace_root=tmp_path,
        pilot=pilot, smoke=failed)
    assert report["main_final_preflight"]["gate"] == "MAIN_V1_READY_WITH_WARNINGS" and not report["blockers"]
    matrix = report["main_final_preflight"]["checker_matrix"]
    assert matrix["requires_llm_checker"] == 0 and len(matrix["strata"]["unverifiable"]) == 8
    for row in matrix["problems"]:
        assert row["legal_runtime_path"] and not row["requires_llm_checker"] and row["checker_smoke_id"] is None
        assert row["sample_policy"] == ("semantic_check" if row["checker_type"] == "token" else "execution_only")
        if row["checker_type"] != "token":
            assert row["semantic_verification"] == "unavailable" and row["formal_fallback"] == "enabled"
    assert report["prompt_snapshot"] == prompt_snapshot()
    assert not report["main_final_preflight"]["llm_checker_critical_path"]
    assert not report["main_final_preflight"]["checker_smoke_required"]
    frozen = save_preflight(report, tmp_path, "preflight-v3")
    assert check_main_receipt(frozen, tmp_path, "main-v3-fixture")["gate"] == "MAIN_V1_READY_WITH_WARNINGS"
    assert check_frozen_preflight(cfg, executor, "preflight-v3")["fingerprint"] == frozen["fingerprint"]
    assert not provider.calls and not oj.run_calls and not oj.submissions
    assert not (tmp_path / ".experiments/main-v3-fixture").exists()


def test_v3_main_rejects_advisory_critical_path_configuration(tmp_path, git_state):
    cfg, _, provider, oj, base, pilot = v3_main_fixture(tmp_path)
    cfg = replace(cfg, sample_checking={**cfg.sample_checking, "llm_checker": "advisory"})
    report = extend_main_preflight(base, cfg, main_experiment_id="main-v3-fixture", workspace_root=tmp_path, pilot=pilot)
    assert report["main_final_preflight"]["gate"] == "MAIN_V1_BLOCKED"
    assert "sample_policy_not_authorized" in report["blockers"]
    assert not provider.calls and not oj.run_calls and not oj.submissions
