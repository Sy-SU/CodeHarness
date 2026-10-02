from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import replace

import httpx
import pytest

from agent.core.harness import HarnessPolicy
from agent.execution import ExecutionService, RunRequest
from agent.oj_client.client import OJClient, OJProtocolError, OJResultUnknownError
from agent.oj_client.contests import ContestProblem, ContestSnapshot
from experiments.contest import ContestRequest, ContestRunner, read_report, list_reports
from client_tests.test_phase5_experiments import ExperimentOJ, Provider, service


PUBLIC = '<p class="eyebrow">Contest #1</p><h1>Test</h1><span class="tag">RUNNING</span>'
TABLE = '<section><h2>Problems</h2><table><tbody>{}</tbody></table></section>'
ROW = '<tr><td>A</td><td><a href="/minioj/contests/1/problems/A">Sum</a></td><td>1000 ms</td></tr>'


def http_client(handler, **kwargs):
    return OJClient("https://oj.test/minioj", "secret-value", client=httpx.Client(
        transport=httpx.MockTransport(handler)), **kwargs)


def test_public_contest_adapter_reads_only_table_and_identifier():
    paths = []
    def handle(request):
        paths.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer secret-value"
        html = PUBLIC + TABLE.format(ROW) + '<a href="https://external.test/editorial">Editorial</a>'
        if request.url.path.endswith("/problems/A"):
            html = '<p class="eyebrow mono">T1003</p><h1>Sum</h1><script>SECRET_HIDDEN</script>'
        return httpx.Response(200, text=html, headers={"Content-Type": "text/html"})
    contest = http_client(handle).get_contest("01")
    assert contest == ContestSnapshot("1", "Test", "RUNNING", [ContestProblem("A", "T1003", "Sum")])
    assert paths == ["/minioj/contests/1", "/minioj/contests/1/problems/A"]
    assert "SECRET_HIDDEN" not in str(contest)


@pytest.mark.parametrize("html", [
    "<h1>Login</h1>", PUBLIC + TABLE.format(ROW.replace("/minioj/contests/1", "/minioj/contests/2")),
    PUBLIC + TABLE.format(ROW.replace("/minioj/contests/1/problems/A", "https://evil.test/A")),
    PUBLIC + TABLE.format(ROW + ROW), PUBLIC.replace("#1", "#2") + TABLE.format(ROW),
    PUBLIC + TABLE.format(ROW.replace("/problems/A", "/problems/..%2FA")),
])
def test_public_adapter_fails_closed_on_changed_or_unsafe_page(html):
    paths = []
    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})
    with pytest.raises(OJProtocolError):
        http_client(handle).get_contest("1")
    assert paths == ["/minioj/contests/1"]


@pytest.mark.parametrize("identifier", [None, 1, True, "", "0", "-1", "../1", "1?x", "9"*20])
def test_contest_ids_are_positive_and_path_safe(identifier):
    with pytest.raises(ValueError):
        ContestRequest(identifier, RunRequest("contest"))


def test_invisible_contest_is_not_treated_as_successful_empty_test():
    client = http_client(lambda request: httpx.Response(200,
        text=PUBLIC.replace("RUNNING", "UPCOMING") + TABLE.format('<tr><td colspan="3">Not started</td></tr>'),
        headers={"content-type": "text/html"}))
    assert client.get_contest("1").problems == []


def test_ambiguous_public_problem_id_or_wrong_content_type_blocks():
    def handle(request):
        html = PUBLIC + TABLE.format(ROW) if not request.url.path.endswith("/A") else (
            '<p class="eyebrow">T1003</p><p class="eyebrow">T1004</p>')
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})
    with pytest.raises(OJProtocolError):
        http_client(handle).get_contest("1")
    with pytest.raises(OJProtocolError):
        http_client(lambda request: httpx.Response(200, json={})).get_contest("1")


@pytest.mark.parametrize("invalid", [False, 0, -1, float("nan"), float("inf"), "1"])
def test_explicit_whole_contest_budget_is_validated(invalid):
    with pytest.raises(ValueError):
        ContestRequest("1", RunRequest("contest"), invalid)


def test_contest_submission_scope_and_unknown_response_never_retry():
    calls = []
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(202, json={"submission_id": 3, "status": "QUEUED"})
    client = http_client(handle, contest_id="1")
    assert client.submit_solution("T1003", "int main(){}").submission_id == "3"
    assert calls == ["/minioj/api/v1/contests/1/submissions"]
    calls.clear()
    def invalid(request):
        calls.append(request.url.path)
        return httpx.Response(202, json={"wrong": 3})
    with pytest.raises(OJResultUnknownError) as error:
        http_client(invalid, contest_id="1").submit_solution("T1003", "int main(){}")
    assert error.value.submission_state_unknown and not error.value.automatic_retry_allowed
    assert len(calls) == 1


def test_http_contest_execution_uses_contest_post_and_filters_formal_data(tmp_path):
    paths, submissions = [], []
    source = ContestOJ()
    def handle(request):
        paths.append((request.method, request.url.path))
        path = request.url.path
        if path == "/minioj/contests/1":
            return httpx.Response(200, text=PUBLIC + TABLE.format(ROW), headers={"content-type": "text/html"})
        if path == "/minioj/contests/1/problems/A":
            return httpx.Response(200, text='<p class="eyebrow mono">T1003</p>', headers={"content-type": "text/html"})
        if path.endswith("/me"):
            return httpx.Response(200, json={"feedback_mode": "full"})
        if "/agent/problems/" in path:
            return httpx.Response(200, json=source.get_problem("T1003").as_dict())
        if path.endswith("/runs"):
            return httpx.Response(200, json={"status": "OK", "stdout": "3\n", "exit_code": 0})
        if request.method == "POST":
            assert path == "/minioj/api/v1/contests/1/submissions"
            submissions.append(json.loads(request.content))
            return httpx.Response(202, json={"submission_id": 1, "status": "QUEUED"})
        if path.endswith("/submissions/1"):
            return httpx.Response(200, json={"submission_id": 1, "status": "FINISHED", "verdict": "AC",
                "diagnostics": {"hidden_input": "DO_NOT_SHOW_PRIVATE_TESTCASE"}})
        raise AssertionError(path)
    executor, provider, _ = service(tmp_path)
    from agent.config import ClientSettings
    executor.settings = ClientSettings("https://oj.test/minioj", "test-token")
    executor.client_factory = lambda *args, **kwargs: http_client(handle, **kwargs)
    report = ContestRunner(executor).run(request(), "real-http-fixture")
    assert report["accepted"] == 1 and submissions[0]["problem_id"] == "T1003"
    root = tmp_path / report["problems"][0]["task_id"]
    assert "DO_NOT_SHOW_PRIVATE_TESTCASE" not in (root / "events.jsonl").read_text()
    assert len(provider.calls) == 2
    assert ("POST", "/minioj/api/v1/submissions") not in paths


def test_http_read_failure_produces_blocked_report_without_model_calls(tmp_path):
    executor, provider, oj = service(tmp_path)
    executor.client_factory = lambda *args, **kwargs: http_client(
        lambda request: httpx.Response(404, json={"detail": "missing secret-value"}), **kwargs)
    report = ContestRunner(executor).run(request(), "missing-contest")
    assert report["status"] == "blocked" and report["http_status"] == 404
    assert report["total_problems"] is None and not provider.calls
    assert "secret-value" not in json.dumps(report)


class ContestOJ(ExperimentOJ):
    def __init__(self, *args, invisible=False, **kwargs):
        if not args:
            args = (["AC"] * 100,)
        super().__init__(*args, **kwargs)
        self.contest_calls, self.invisible = 0, invisible
        self.members = [ContestProblem("A", "sum", "Sum"), ContestProblem("B", "other", "Other")]

    def get_contest(self, contest_id):
        self.contest_calls += 1
        return ContestSnapshot(contest_id, "Test", "UPCOMING" if self.invisible else "RUNNING",
                               [] if self.invisible else self.members)


def request(**kwargs):
    return ContestRequest("1", RunRequest("contest", profile="standard", **kwargs), 1)


def test_contest_reuses_samples_review_feedback_filter_and_completed_resume(tmp_path):
    oj = ContestOJ(["AC", "AC"], feedback_mode="full")
    executor, provider, oj = service(tmp_path, oj=oj)
    scopes = []
    def factory(*args, **kwargs):
        scopes.append(kwargs.get("contest_id"))
        oj.contest_id = kwargs.get("contest_id")
        return oj
    executor.client_factory = factory
    report = ContestRunner(executor).run(request(), "contest-test")
    assert report["status"] == "completed" and report["accepted"] == report["total_problems"] == 2
    assert report["score_policy"] == "ac_count_only_v1" and report["official_score"] is None
    assert report["llm_calls"] == 4 and report["submission_attempts"] == 2 and oj.run_calls == 2
    assert scopes == [None, "1", "1"]
    for row in report["problems"]:
        root = tmp_path / row["task_id"]
        state = json.loads((root / "state.json").read_text())
        config = json.loads((root / "artifacts/execution-config.json").read_text())
        assert state["actual_feedback_mode"] == "full" and state["effective_feedback_mode"] == "verdict_only"
        assert config["contest_id"] == "1" and (root / "artifacts/review.md").is_file()
        assert json.loads((root / "checkpoint.json").read_text())["config"]["contest_id"] == "1"
    before = (len(provider.calls), oj.contest_calls, len(oj.submissions), oj.run_calls)
    assert ContestRunner(executor).run(request(), "contest-test", resume=True) == report
    assert before == (len(provider.calls), oj.contest_calls, len(oj.submissions), oj.run_calls)
    assert (tmp_path / ".contests/contest-test/problems.csv").is_file()
    assert list_reports(tmp_path)[0]["run_id"] == "contest-test"


def test_blocked_or_unknown_mode_never_calls_models(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ContestOJ(invisible=True))
    report = ContestRunner(executor).run(request(), "invisible")
    assert report["status"] == "blocked" and report["total_problems"] is None
    assert report["reason"] == "contest_problems_not_visible" and not provider.calls and not oj.submissions
    executor, provider, oj = service(tmp_path, oj=ContestOJ(feedback_mode=None))
    report = ContestRunner(executor).run(request(), "unknown-mode")
    assert report["accepted"] == 0 and report["total_problems"] == 2
    assert all(row["terminal_status"] == "condition_mismatch" for row in report["problems"])
    assert not provider.calls and not oj.submissions


def test_code_only_contest_keeps_one_call_one_submit_and_no_samples(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ContestOJ())
    run = request(mode="code-only", policy=HarnessPolicy(max_llm_calls=1, max_submissions=1))
    report = ContestRunner(executor).run(run, "code-only-contest")
    assert report["accepted"] == 2 and len(provider.calls) == 2 and len(oj.submissions) == 2
    assert not oj.run_calls and not oj.feedback_calls
    assert all(row["llm_calls"] == row["submission_attempts"] == 1 for row in report["problems"])


def test_total_and_per_problem_caps_apply_and_skips_are_distinct(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ContestOJ())
    run = request(policy=HarnessPolicy(max_cost_cny=0.08))
    run = replace(run, total_cost_cny=0.08)
    report = ContestRunner(executor).run(run, "limited")
    assert report["accepted"] == 1 and report["total_problems"] == 2
    assert report["budget_committed_cny"] <= run.total_cost_cny
    assert report["problems"][1]["terminal_status"] == "budget_exhausted"
    assert len(oj.submissions) == 1 and len(provider.calls) == 2


def test_fully_used_total_cap_marks_unstarted_without_false_completion_or_broken_link(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ContestOJ())
    run = replace(request(mode="code-only", policy=HarnessPolicy(max_llm_calls=1, max_submissions=1)),
                  total_cost_cny=0.036096)
    report = ContestRunner(executor).run(run, "skip-rest")
    assert report["accepted"] == report["finished_problems"] == report["not_started_problems"] == 1
    assert report["problems"][1]["status"] == "not_started"
    assert report["problems"][1]["task_id"] is None
    assert not (tmp_path / "skip-rest-s1-p2-r1").exists()
    assert len(provider.calls) == len(oj.submissions) == 1


def test_resume_keeps_membership_and_poll_id_without_repeating_submission(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ContestOJ(["AC", "AC"], interrupt="wait"))
    with pytest.raises(KeyboardInterrupt):
        ContestRunner(executor).run(request(), "resume")
    assert read_report(tmp_path, "resume")["status"] == "interrupted"
    oj.members = [ContestProblem("X", "changed", "Changed")]
    report = ContestRunner(executor).run(request(), "resume", resume=True)
    assert report["accepted"] == 2 and [row["problem_id"] for row in report["problems"]] == ["sum", "other"]
    assert oj.contest_calls == 1 and len(oj.submissions) == 2 and len(provider.calls) == 4


def test_paid_call_interruption_is_not_reissued_on_contest_resume(tmp_path):
    executor, provider, oj = service(tmp_path, provider=Provider(interrupt=True), oj=ContestOJ())
    with pytest.raises(KeyboardInterrupt):
        ContestRunner(executor).run(request(), "uncertain")
    report = ContestRunner(executor).run(request(), "uncertain", resume=True)
    assert report["accepted"] == 1 and report["problems"][0]["terminal_status"] == "result_unknown"
    assert report["estimated_cost_cny"] is None
    assert len(provider.calls) == 3 and len(oj.submissions) == 1


def test_changed_config_and_symlinks_are_rejected(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ContestOJ())
    ContestRunner(executor).run(request(), "frozen")
    with pytest.raises(ValueError):
        ContestRunner(executor).run(replace(request(), total_cost_cny=2), "frozen", resume=True)
    (tmp_path / ".contests/link").symlink_to(tmp_path / ".contests/frozen", target_is_directory=True)
    with pytest.raises(ValueError):
        read_report(tmp_path, "link")
    assert len(provider.calls) == 4 and oj.contest_calls == 1


def test_contest_cli_requires_confirmation_and_reuses_shared_service(tmp_path, monkeypatch, capsys):
    from experiments import cli
    executor, provider, oj = service(tmp_path, oj=ContestOJ())
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setenv("OJ_BASE_URL", "https://oj.test")
    monkeypatch.setenv("OJ_API_TOKEN", "test-token")
    monkeypatch.setattr(cli, "ExecutionService", lambda *args: executor)
    policy = tmp_path / "policy.yaml"
    policy.write_text("max_cost_cny: 1\n")
    args = ["contest", "1", "--experiment-id", "cli-contest", "--workspace-root", str(tmp_path),
            "--harness-config", str(policy), "--profile", "standard"]
    with pytest.raises(SystemExit):
        cli.main(args)
    assert not provider.calls and not oj.contest_calls
    assert cli.main(args + ["--confirm-model-call", "--confirm-submit"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["accepted"] == 2 and report["contest_id"] == "1"


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from dashboard.app import create_app
    from dashboard.config import DashboardSettings
    from dashboard.services.launcher import LaunchService
    executor, provider, oj = service(tmp_path, oj=ContestOJ())
    settings = DashboardSettings(tmp_path, enable_launch=True, harness_config=None, env_file=None)
    launcher = LaunchService(settings, service_factory=lambda: executor, autostart=False)
    app = create_app(settings, launcher=launcher)
    def get(path, method="GET", **kwargs):
        async def invoke():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1",
                                         cookies=kwargs.pop("cookies", None)) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(invoke())
    token = re.search(r'name="csrf-token" content="([^"]+)"', get("/contests").text)[1]
    yield launcher, provider, oj, get, {"Origin": "http://127.0.0.1", "X-CodeHarness-CSRF": token}
    launcher.close()


def payload(**kwargs):
    return {"request_id": uuid.uuid4().hex, "contest_id": "1", "profile": "standard",
            "confirm_remote_calls": True, **kwargs}


def test_dashboard_contest_queue_idempotency_progress_and_local_only_reads(web):
    launcher, provider, oj, get, headers = web
    data = payload()
    response = get("/api/dashboard/contests/launch", "POST", headers=headers, json=data)
    assert response.status_code == 202
    run_id = response.json()["contest_run_id"]
    assert get("/api/dashboard/contests/launch", "POST", headers=headers, json=data).json() == response.json()
    assert get(f"/contests/{run_id}").status_code == 200
    assert get(f"/api/dashboard/contests/{run_id}").json()["status"] == "queued"
    assert not provider.calls and not oj.contest_calls
    changed = get("/api/dashboard/contests/launch", "POST", headers=headers, json={**data, "total_cost_cny": 2})
    assert changed.status_code == 409
    launcher.run_next()
    report = get(f"/api/dashboard/contests/{run_id}").json()
    assert report["accepted"] == 2 and report["launch_job"]["status"] == "finished" and not report["resumable"]
    counts = (len(provider.calls), oj.contest_calls, len(oj.submissions))
    assert get("/contests").status_code == 200 and len(get("/api/dashboard/contests").json()) == 1
    assert get(f"/contests/{run_id}", cookies={"codeharness_language": "zh"}).status_code == 200
    assert counts == (len(provider.calls), oj.contest_calls, len(oj.submissions))


@pytest.mark.parametrize("changes", [
    {"confirm_remote_calls": False}, {"contest_id": "../1"}, {"problems": ["other"]},
    {"model_config": "/etc/passwd"}, {"total_cost_cny": -1}, {"score_policy": "official"},
    {"task_config": {"max_submissions": 11}}, {"task_config": {"roles": {"CODE": "max"}}},
])
def test_dashboard_contest_rejects_unsafe_or_unconfirmed_launch(web, changes):
    launcher, provider, oj, get, headers = web
    assert get("/api/dashboard/contests/launch", "POST", headers=headers,
               json=payload(**changes)).status_code == 422
    assert not provider.calls and not oj.contest_calls


def test_contest_dashboard_csrf_and_resume_saved_scope(web):
    launcher, provider, oj, get, headers = web
    assert get("/api/dashboard/contests/launch", "POST", json=payload()).status_code == 403
    run_id = get("/api/dashboard/contests/launch", "POST", json=payload(), headers=headers).json()["contest_run_id"]
    oj.interrupt = "wait"
    with pytest.raises(KeyboardInterrupt):
        launcher.run_next()
    assert get(f"/api/dashboard/contests/{run_id}").json()["resumable"]
    response = get(f"/api/dashboard/contests/{run_id}/resume", "POST", headers=headers,
        json={"request_id": uuid.uuid4().hex, "confirm_remote_calls": True})
    assert response.status_code == 202
    assert get(f"/api/dashboard/contests/{run_id}/resume", "POST", headers=headers,
        json={"request_id": uuid.uuid4().hex, "confirm_remote_calls": True}).status_code == 409
    launcher.run_next()
    assert get(f"/api/dashboard/contests/{run_id}").json()["accepted"] == 2
    assert oj.contest_calls == 1 and len(oj.submissions) == 2


def test_dashboard_contest_report_reader_retains_size_and_symlink_defenses(tmp_path):
    from dashboard.repository.contests import ContestRepository
    from dashboard.repository.workspace import WorkspaceRepository
    executor, _, _ = service(tmp_path, oj=ContestOJ())
    ContestRunner(executor).run(request(), "safe-report")
    reader = ContestRepository(WorkspaceRepository(tmp_path))
    assert reader.detail("safe-report")["accepted"] == 2
    root = tmp_path / ".contests/safe-report"
    (root / "report.json").write_text("x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        reader.detail("safe-report")
    (root / "report.json").unlink()
    (root / "report.json").symlink_to(tmp_path / "safe-report-s1-p1-r1/state.json")
    with pytest.raises((OSError, ValueError)):
        reader.detail("safe-report")
    assert reader.list() == []


def test_readonly_dashboard_contests_never_initialize_execution(tmp_path):
    pytest.importorskip("fastapi")
    from dashboard.app import create_app
    from dashboard.config import DashboardSettings
    executor, provider, oj = service(tmp_path, oj=ContestOJ())
    ContestRunner(executor).run(request(), "read-only")
    before = (len(provider.calls), oj.contest_calls, len(oj.submissions))
    app = create_app(DashboardSettings(tmp_path, enable_launch=False, env_file=None))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
            for path in ("/contests", "/contests/read-only", "/api/dashboard/contests/read-only"):
                assert (await client.get(path)).status_code == 200
            assert (await client.post("/api/dashboard/contests/launch", json=payload())).status_code in {404, 405}
    asyncio.run(check())
    assert before == (len(provider.calls), oj.contest_calls, len(oj.submissions))


def test_existing_single_task_nonce_keeps_pre_contest_intent_format(tmp_path):
    from dataclasses import asdict
    from agent.execution import fingerprint
    from dashboard.config import DashboardSettings
    from dashboard.services.launcher import LaunchService
    executor, _, _ = service(tmp_path)
    launcher = LaunchService(DashboardSettings(tmp_path, harness_config=None),
                             service_factory=lambda: executor, autostart=False)
    run = RunRequest("sum", profile="standard")
    nonce = uuid.uuid4().hex
    try:
        first = launcher.enqueue(nonce, run)
        old_request = asdict(run)
        old_request.pop("contest_id")
        saved = launcher._store(nonce).read_json("job.json")
        assert saved["intent"] == fingerprint({"operation": "solve", "request": old_request, "task_id": None})
        assert "contest_id" not in saved["request"]
        assert launcher.enqueue(nonce, run) == first
    finally:
        launcher.close()
