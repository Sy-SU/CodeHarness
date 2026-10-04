from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import replace

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from dashboard.app import create_app
from dashboard.config import DashboardSettings
from dashboard.services.launcher import LaunchService
from client_tests.test_phase5_experiments import service, ExperimentOJ


@pytest.fixture
def launch_web(tmp_path):
    executor, provider, oj = service(tmp_path / "workspace")
    settings = DashboardSettings(executor.workspace_root, enable_launch=True, harness_config=None, env_file=None)
    launcher = LaunchService(settings, service_factory=lambda: executor, autostart=False, capacity=2)
    app = create_app(settings, launcher=launcher)

    def request(url, method="GET", **kwargs):
        async def invoke():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(invoke())

    token = re.search(r'name="csrf-token" content="([^"]+)"', request("/").text)[1]
    headers = {"Origin": "http://127.0.0.1", "X-CodeHarness-CSRF": token}
    yield launcher, provider, oj, request, headers
    launcher.close()


def payload(**changes):
    data = {"request_id": uuid.uuid4().hex, "problem_id": "sum", "mode": "harness-loop",
            "profile": "standard", "confirm_remote_calls": True}
    data.update(changes)
    return data


def test_launch_returns_before_calls_and_worker_uses_shared_bounded_runtime(launch_web):
    launcher, provider, oj, request, headers = launch_web
    data = payload()
    result = request("/api/dashboard/launch", "POST", json=data, headers=headers)
    assert result.status_code == 202
    task_id = result.json()["task_id"]
    assert not provider.calls and not oj.submissions
    assert launcher.resume_request(task_id).policy.max_cost_cny == 1
    assert launcher.resume_request(task_id).expected_feedback_mode == "verdict_only"
    assert launcher.resume_request(task_id).require_feedback_mode
    assert request(f"/tasks/{task_id}").status_code == 200
    assert request(f"/api/dashboard/tasks/{task_id}").json()["summary"]["current_phase"] == "QUEUED"
    launcher.run_next()
    detail = request(f"/api/dashboard/tasks/{task_id}").json()
    state = json.loads((launcher.settings.workspace_root / task_id / "state.json").read_text())
    assert state["expected_feedback_mode"] == state["actual_feedback_mode"] == "verdict_only"
    assert state["feedback_mode_status"] == "confirmed"
    assert detail["summary"]["solved"] and detail["summary"]["final_verdict"] == "AC"
    assert len(provider.calls) == 2 and len(oj.submissions) == 1 and oj.run_calls == 1
    assert request(f"/api/dashboard/jobs/{data['request_id']}").json()["status"] == "finished"


def test_code_only_dashboard_launch_is_one_call_one_submit_and_no_samples(launch_web):
    launcher, provider, oj, request, headers = launch_web
    result = request("/api/dashboard/launch", "POST", json=payload(mode="code-only"), headers=headers)
    assert result.status_code == 202
    launcher.run_next()
    assert len(provider.calls) == 1 and len(oj.submissions) == 1
    assert not oj.run_calls and not oj.feedback_calls


@pytest.mark.parametrize("args,expected", [([], "verdict_only"),
    (["--feedback-mode", "full"], "full"), (["--feedback-mode", "verdict_only"], "verdict_only")])
def test_dashboard_cli_defaults_to_verdict_only_and_retains_explicit_override(tmp_path, monkeypatch, args, expected):
    from pathlib import Path
    import uvicorn
    from dashboard.cli import main
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: apps.append(app))
    assert main(["--workspace-root", str(tmp_path), "--harness-config",
        str(Path(__file__).parents[1] / "config/harness.example.yaml"), *args]) == 0
    launcher = apps[0].state.launcher
    try:
        request = launcher.settings.task_request(payload())
        assert request.expected_feedback_mode == expected and request.require_feedback_mode
        assert launcher.queue.empty()
    finally:
        launcher.close()


def test_new_default_does_not_rewrite_saved_full_task_or_repost_on_resume(tmp_path):
    executor, provider, oj = service(tmp_path, oj=ExperimentOJ(["AC"], feedback_mode="full", interrupt="wait"))
    old_settings = DashboardSettings(executor.workspace_root, enable_launch=True,
        harness_config=None, env_file=None, expected_feedback_mode="full")
    old_launcher = LaunchService(old_settings, service_factory=lambda: executor, autostart=False)
    restarted = None
    try:
        job = old_launcher.enqueue(uuid.uuid4().hex, old_settings.task_request(payload()))
        with pytest.raises(KeyboardInterrupt):
            old_launcher.run_next()
        new_settings = replace(old_settings, expected_feedback_mode=DashboardSettings().expected_feedback_mode)
        assert new_settings.task_request(payload()).expected_feedback_mode == "verdict_only"
        restarted = LaunchService(new_settings, service_factory=lambda: executor, autostart=False)
        saved_request = restarted.resume_request(job["task_id"])
        assert saved_request.expected_feedback_mode == "full" and saved_request.require_feedback_mode
        restarted.enqueue(uuid.uuid4().hex, saved_request, resume_task_id=job["task_id"])
        restarted.run_next()
        assert len(provider.calls) == 2 and len(oj.submissions) == 1
        saved_config = json.loads((executor.workspace_root / job["task_id"] /
            "artifacts/execution-config.json").read_text())
        assert saved_config["expected_feedback_mode"] == "full"
    finally:
        old_launcher.close()
        if restarted is not None:
            restarted.close()


def test_persisted_request_id_is_idempotent_and_different_intent_conflicts(launch_web):
    launcher, provider, oj, request, headers = launch_web
    data = payload()
    first = request("/api/dashboard/launch", "POST", json=data, headers=headers)
    second = request("/api/dashboard/launch", "POST", json=data, headers=headers)
    assert first.json()["task_id"] == second.json()["task_id"]
    assert launcher.queue.qsize() == 1
    assert request("/api/dashboard/launch", "POST", json={**data, "profile": "strong"}, headers=headers).status_code == 409
    assert request("/api/dashboard/launch", "POST", json={**data, "max_cost_cny": 2}, headers=headers).status_code == 409
    launcher.run_next()
    assert request("/api/dashboard/launch", "POST", json=data, headers=headers).json()["status"] == "finished"
    assert len(provider.calls) == 2 and len(oj.submissions) == 1


@pytest.mark.parametrize("headers_change", [{"Origin": "https://attacker.test"}, {"Origin": "null"},
    {"Origin": "http://127.0.0.1:99"}, {"X-CodeHarness-CSRF": "bad"},
    {"Sec-Fetch-Site": "cross-site"}])
def test_csrf_origin_and_port_guards_do_not_enqueue_or_call(launch_web, headers_change):
    launcher, provider, oj, request, headers = launch_web
    result = request("/api/dashboard/launch", "POST", json=payload(), headers={**headers, **headers_change})
    assert result.status_code == 403
    assert not launcher.queue.qsize() and not provider.calls and not oj.submissions


@pytest.mark.parametrize("changes", [{"confirm_remote_calls": False}, {"confirm_remote_calls": "true"},
    {"request_id": "../escape"}, {"mode": "shell"}, {"profile": "invented"},
    {"model_config": "/etc/passwd"}, {"command": "rm -rf /"},
    {"mode": "code-only", "profile": None}, {"problem_id": ""}])
def test_unsupported_inputs_never_call_remote_services(launch_web, changes):
    launcher, provider, oj, request, headers = launch_web
    assert request("/api/dashboard/launch", "POST", json=payload(**changes), headers=headers).status_code == 422
    assert not launcher.queue.qsize() and not provider.calls and not oj.submissions


def test_body_size_content_type_and_host_are_guarded(launch_web):
    launcher, provider, oj, request, headers = launch_web
    assert request("/api/dashboard/launch", "POST", content="x", headers=headers).status_code == 415
    assert request("/api/dashboard/launch", "POST", content="x" * 9000,
        headers={**headers, "Content-Type": "application/json"}).status_code == 413
    assert request("/api/dashboard/launch", "POST", json=payload(),
        headers={**headers, "Host": "attacker.test"}).status_code == 400
    assert not launcher.queue.qsize() and not provider.calls and not oj.submissions


def test_queue_backpressure_and_read_pages_do_not_trigger_execution(launch_web):
    launcher, provider, oj, request, headers = launch_web
    for _ in range(2):
        assert request("/api/dashboard/launch", "POST", json=payload(), headers=headers).status_code == 202
    assert request("/api/dashboard/launch", "POST", json=payload(), headers=headers).status_code == 429
    for path in ("/", "/tasks", "/models", "/experiments"):
        assert request(path).status_code == 200
    assert not provider.calls and not oj.submissions


def test_restart_marks_old_job_interrupted_without_automatically_running_it(launch_web):
    launcher, provider, oj, request, headers = launch_web
    data = payload()
    request("/api/dashboard/launch", "POST", json=data, headers=headers)
    restarted = LaunchService(launcher.settings, service_factory=launcher.service_factory, autostart=False)
    assert restarted.job(data["request_id"])["status"] == "interrupted"
    assert restarted.queue.qsize() == 0
    assert not provider.calls and not oj.submissions
    restarted.close()


def test_known_submission_checkpoint_resumes_from_dashboard_without_repost(launch_web, monkeypatch):
    launcher, provider, oj, request, headers = launch_web
    # Persist a genuine harness interruption in the production worker path.
    data = payload()
    result = request("/api/dashboard/launch", "POST", json=data, headers=headers)
    task_id = result.json()["task_id"]
    oj.interrupt = "wait"
    with pytest.raises(KeyboardInterrupt):
        launcher.run_next()
    # KeyboardInterrupt is not an ordinary exception; prior server is considered stopped.
    launcher.active.clear()
    assert launcher.can_resume(task_id)
    response = request(f"/api/dashboard/tasks/{task_id}/resume", "POST",
        json={"request_id": "0" * 32, "confirm_remote_calls": True}, headers=headers)
    assert response.status_code == 202
    # Older finished/interrupted UUIDs must not hide the active resume intent.
    assert request(f"/api/dashboard/tasks/{task_id}/resume", "POST",
        json={"request_id": uuid.uuid4().hex, "confirm_remote_calls": True}, headers=headers).status_code == 409
    launcher.run_next()
    assert len(provider.calls) == 2 and len(oj.submissions) == 1
    assert request(f"/api/dashboard/tasks/{task_id}").json()["summary"]["solved"]


def test_simplified_launch_ui_is_bilingual_and_diagnostics_are_collapsed(launch_web):
    launcher, _, _, request, headers = launch_web
    home = request("/", headers={"Cookie": "codeharness_language=zh"}).text
    assert "发起任务" in home and "开始任务" in home and 'id="launch-form"' in home
    assert 'name="confirm" type="checkbox" required' in home
    assert "任务配置" in home and 'id="task-config" class="task-config"' in home
    assert "模型调用次数上限" in home and "正式提交尝试上限" in home
    assert "任务额度（元）" in home and 'name="max_cost_cny" type="number"' in home
    assert 'value="1.0" required' in home
    assert "本地 · 只读" not in home
    result = request("/api/dashboard/launch", "POST", json=payload(), headers=headers)
    launcher.run_next()
    html = request(f"/tasks/{result.json()['task_id']}").text
    assert '<details class="advanced-diagnostics">' in html
    assert 'id="solution-code"' in html and 'id="submissions"' in html
    assert html.index('<details class="advanced-diagnostics">') < html.index('id="solution"')


@pytest.mark.parametrize("cost", [0, -1, True, None, "2", [], {}, 1e309, 10 ** 400])
def test_invalid_task_cost_never_enqueues(launch_web, cost):
    launcher, provider, oj, request, headers = launch_web
    # Infinity is rejected by the server even if a non-standard JSON client sends it.
    response = request("/api/dashboard/launch", "POST", content=json.dumps(payload(max_cost_cny=cost)),
                       headers={**headers, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert not launcher.queue.qsize() and not provider.calls and not oj.submissions


@pytest.mark.parametrize("mode", ["code-only", "harness-loop"])
def test_dashboard_selected_budget_is_saved_and_enforced_before_model_call(launch_web, mode):
    launcher, provider, oj, request, headers = launch_web
    response = request("/api/dashboard/launch", "POST", json=payload(mode=mode, max_cost_cny=0.01), headers=headers)
    task_id = response.json()["task_id"]
    assert launcher.resume_request(task_id).policy.max_cost_cny == 0.01
    launcher.run_next()
    summary = request(f"/api/dashboard/tasks/{task_id}").json()["summary"]
    assert summary["status"] == "budget_exhausted"
    assert not provider.calls and not oj.submissions


def test_dashboard_budget_can_be_increased_but_resume_cannot_reset_it(launch_web):
    launcher, provider, oj, request, headers = launch_web
    response = request("/api/dashboard/launch", "POST", json=payload(max_cost_cny=2.5), headers=headers)
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert launcher.resume_request(task_id).policy.max_cost_cny == 2.5
    oj.interrupt = "wait"
    with pytest.raises(KeyboardInterrupt):
        launcher.run_next()
    launcher.active.clear()
    assert request(f"/api/dashboard/tasks/{task_id}/resume", "POST", json={
        "request_id": uuid.uuid4().hex, "confirm_remote_calls": True, "max_cost_cny": 3}, headers=headers).status_code == 422
    response = request(f"/api/dashboard/tasks/{task_id}/resume", "POST", json={
        "request_id": uuid.uuid4().hex, "confirm_remote_calls": True}, headers=headers)
    assert response.status_code == 202
    assert launcher.resume_request(task_id).policy.max_cost_cny == 2.5
    launcher.run_next()
    assert len(provider.calls) == 2 and len(oj.submissions) == 1


@pytest.mark.parametrize("actual", [None])
def test_dashboard_verdict_only_is_expected_not_fabricated_when_server_mode_unknown(launch_web, actual):
    launcher, provider, oj, request, headers = launch_web
    oj.feedback_mode = actual
    response = request("/api/dashboard/launch", "POST", json=payload(profile=None), headers=headers)
    launcher.run_next()
    summary = request(f"/api/dashboard/tasks/{response.json()['task_id']}").json()["summary"]
    assert summary["status"] == "condition_mismatch"
    state = json.loads((launcher.settings.workspace_root / response.json()["task_id"] / "state.json").read_text())
    assert state["expected_feedback_mode"] == "verdict_only"
    assert state["actual_feedback_mode"] == actual
    assert not provider.calls and not oj.submissions


@pytest.mark.parametrize("mode", ["code-only", "harness-loop"])
def test_dashboard_accepts_full_with_effective_verdict_only(launch_web, mode):
    launcher, provider, oj, request, headers = launch_web
    oj.feedback_mode = "full"
    response = request("/api/dashboard/launch", "POST", json=payload(mode=mode), headers=headers)
    assert response.status_code == 202
    launcher.run_next()
    summary = request(f"/api/dashboard/tasks/{response.json()['task_id']}").json()["summary"]
    assert summary["solved"] and summary["actual_feedback_mode"] == "full"
    assert summary["effective_feedback_mode"] == "verdict_only"
    assert summary["feedback_policy"] == "formal_verdict_only_v1"
    home = request("/").text
    assert "Full server feedback is accepted and reduced to verdict-only" in home
    translated = request("/", headers={"Cookie": "codeharness_language=zh"}).text
    assert "服务端 full 可兼容降为 verdict-only" in translated
    html = request(f"/tasks/{response.json()['task_id']}").text
    assert re.search(r'data-summary="actual_feedback_mode"[^>]*>\s*full\s*<', html)
    assert re.search(r'data-summary="effective_feedback_mode"[^>]*>\s*verdict_only\s*<', html)
    assert len(provider.calls) == (1 if mode == "code-only" else 2)
    assert len(oj.submissions) == 1


def test_dashboard_startup_budget_default_does_not_override_explicit_input(tmp_path):
    settings = DashboardSettings(workspace_root=tmp_path, enable_launch=True,
        harness_config=None, env_file=None, max_cost_cny=2)
    assert settings.launch_policy().max_cost_cny == 2
    with pytest.raises(ValueError):
        replace(settings, max_cost_cny=float("nan"))
    executor, provider, oj = service(tmp_path)
    launcher = LaunchService(settings, service_factory=lambda: executor, autostart=False)
    app = create_app(settings, launcher=launcher)

    async def verify():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            home = (await client.get("/")).text
            assert 'value="2" required' in home
            token = re.search(r'name="csrf-token" content="([^"]+)"', home)[1]
            headers = {"Origin": "http://127.0.0.1", "X-CodeHarness-CSRF": token}
            for cost in (None, 0.005):
                data = payload()
                if cost is not None:
                    data["max_cost_cny"] = cost
                response = await client.post("/api/dashboard/launch", json=data, headers=headers)
                assert response.status_code == 202
                assert launcher.resume_request(response.json()["task_id"]).policy.max_cost_cny == (cost or 2)
    try:
        asyncio.run(verify())
        assert not provider.calls and not oj.submissions
    finally:
        launcher.close()


def test_launcher_javascript_posts_numeric_budget_and_retains_mode_guard():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed for the frontend payload test")
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const listeners = {}, changes = {}, button = {}, message = {}, note = {}, summary = {}, details = {};
const input = value => ({value, defaultValue: value, disabled: false, handlers: {},
    addEventListener(n, f) {this.handlers[n] = f;}});
const elements = {mode: {value: 'harness-loop', addEventListener: (n, f) => changes[n] = f},
    profile: {value: '', options: [{}]}, problem_id: {value: ' T1003 '},
    max_cost_cny: input('2.5'), max_llm_calls: input('7'), max_submissions: input('4'),
    http_timeout: input('0.75'), poll_interval: input('0'), deadline: input('2'), confirm: {checked: true}};
const reset = {addEventListener: (n, f) => changes.reset = f};
const form = {elements, reportValidity: () => true, querySelector: () => button,
    addEventListener: (n, f) => listeners[n] = f};
let sent, redirected;
const document = {querySelector: () => ({content: 'csrf-test'}),
    getElementById: id => ({'launch-form': form, 'mode-note': note, 'launch-message': message,
        'config-summary': summary, 'task-config': details, 'reset-task-config': reset}[id])};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document,
    window: {CodeHarnessI18n: {t: (text, values = {}) => text.replace(/\{(\w+)\}/g, (m, k) => values[k] ?? m)}},
    crypto: {randomUUID: () => 'test-id'},
    location: {assign: x => redirected = x}, fetch: async (url, options) => {
        sent = {url, ...JSON.parse(options.body)};
        return {ok: true, json: async () => ({task_id: 'task-test'})};
    }});
(async () => {
    await listeners.submit({preventDefault(){}});
    assert.deepStrictEqual(sent.task_config, {max_cost_cny: 2.5, max_llm_calls: 7,
        max_submissions: 4, http_timeout: 0.75, poll_interval: 0, deadline: 2});
    assert.strictEqual(sent.profile, null);
    assert.strictEqual(sent.problem_id, 'T1003');
    assert.strictEqual(sent.confirm_remote_calls, true);
    assert.strictEqual(redirected, '/tasks/task-test');
    elements.mode.value = 'code-only'; changes.change();
    assert.strictEqual(elements.profile.value, 'standard');
    assert.strictEqual(elements.profile.options[0].disabled, true);
    assert.strictEqual(elements.max_llm_calls.value, '1');
    assert.strictEqual(elements.max_submissions.disabled, true);
    assert.match(summary.textContent, /1 model calls/);
    await listeners.submit({preventDefault(){}});
    assert.strictEqual(sent.task_config.max_llm_calls, 1);
    elements.mode.value = 'harness-loop'; changes.change();
    assert.strictEqual(elements.max_llm_calls.value, '7');
    assert.strictEqual(elements.max_submissions.value, '4');
    elements.max_llm_calls.value = '3'; elements.max_llm_calls.handlers.input();
    assert.match(summary.textContent, /3 model calls/);
    changes.reset();
    assert.strictEqual(elements.max_llm_calls.value, '7');
    assert.strictEqual(elements.problem_id.value, ' T1003 ');
    assert.strictEqual(elements.confirm.checked, true);
    listeners.invalid({target: {name: 'max_llm_calls'}});
    assert.strictEqual(details.open, true);
})().catch(error => {process.stderr.write(String(error)); process.exitCode = 1;});
"""
    path = Path(__file__).parents[1] / "dashboard/static/js/launch.js"
    result = subprocess.run([node, "-e", script, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_invalid_local_policy_keeps_home_readable_and_launch_fails_without_calls(tmp_path):
    executor, provider, oj = service(tmp_path)
    policy = tmp_path / "invalid.yaml"
    policy.write_text("max_cost_cny: [invalid\n")
    settings = DashboardSettings(tmp_path, enable_launch=True, harness_config=policy, env_file=None)
    launcher = LaunchService(settings, service_factory=lambda: executor, autostart=False)
    app = create_app(settings, launcher=launcher)

    async def verify():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            home = await client.get("/")
            assert home.status_code == 200 and 'value="1.0" required' in home.text
            token = re.search(r'name="csrf-token" content="([^"]+)"', home.text)[1]
            response = await client.post("/api/dashboard/launch", json=payload(), headers={
                "Origin": "http://127.0.0.1", "X-CodeHarness-CSRF": token})
            assert response.status_code == 422
    try:
        asyncio.run(verify())
        assert launcher.queue.qsize() == 0 and not provider.calls and not oj.submissions
    finally:
        launcher.close()


def test_task_configuration_is_frozen_in_job_snapshot_and_reaches_runtime(launch_web):
    launcher, provider, oj, request, headers = launch_web
    config = dict(max_cost_cny=0.5, max_llm_calls=3, max_submissions=2,
                  http_timeout=0.75, poll_interval=0, deadline=2)
    data = payload(task_config=config)
    result = request("/api/dashboard/launch", "POST", json=data, headers=headers)
    assert result.status_code == 202
    task_id = result.json()["task_id"]
    run = launcher.resume_request(task_id)
    assert run.policy.max_cost_cny == 0.5 and run.policy.max_llm_calls == 3
    assert run.policy.max_submissions == 2 and run.http_timeout == 0.75
    assert run.poll_interval == 0 and run.deadline == 2
    executor = launcher.service_factory()
    calls = []
    executor.client_factory = lambda *a, **kw: calls.append(kw) or oj
    original_wait = oj.wait_for_submission
    oj.wait_for_submission = lambda *a, **kw: calls.append(kw) or original_wait(*a, **kw)
    launcher.run_next()
    detail = request(f"/api/dashboard/tasks/{task_id}").json()
    assert detail["summary"]["solved"]
    assert len(provider.calls) == 2 and len(oj.submissions) == 1
    assert calls[0]["timeout_seconds"] == 0.75
    assert calls[1] == {"submission_id": "1", "timeout_seconds": 2, "poll_interval_seconds": 0}
    snapshot = json.loads(request(f"/tasks/{task_id}/artifacts/artifacts/execution-config.json").text)
    assert snapshot["budget"]["max_llm_calls"] == 3 and snapshot["budget"]["max_submissions"] == 2
    assert snapshot["deadline"] == 2
    assert launcher.settings.launch_policy().max_llm_calls == 80  # defaults untouched


@pytest.mark.parametrize("setting,limit,reason", [
    ("max_llm_calls", 1, "llm_call_limit"),
    ("max_submissions", 1, "submission_limit"),
    ("max_cost_cny", 0.01, "cost_reservation_limit")])
def test_selected_task_limits_stop_at_the_runtime_not_just_the_form(launch_web, setting, limit, reason):
    launcher, provider, oj, request, headers = launch_web
    oj.verdicts = ["WA"] * 20
    response = request("/api/dashboard/launch", "POST", json=payload(task_config={setting: limit}), headers=headers)
    assert response.status_code == 202
    launcher.run_next()
    detail = request(f"/api/dashboard/tasks/{response.json()['task_id']}").json()
    assert detail["summary"]["status"] == "budget_exhausted"
    assert detail["summary"]["terminal_reason"] == reason
    if setting == "max_llm_calls":
        assert len(provider.calls) == 1 and not oj.submissions
    elif setting == "max_submissions":
        assert len(oj.submissions) == 1
    else:
        assert not provider.calls and not oj.submissions


@pytest.mark.parametrize("config", [None, [], "bad", {"max_tokens": 1000}, {"debug_before_replan": 1},
    {"feedback_mode": None}, {"model_config": "/etc/passwd"}, {"max_llm_calls": True},
    {"max_llm_calls": 1.5}, {"max_llm_calls": 0}, {"max_submissions": 101}, {"max_submissions": "2"},
    {"max_cost_cny": False}, {"http_timeout": 0}, {"http_timeout": float("nan")},
    {"http_timeout": 10 ** 400}, {"poll_interval": -1}, {"poll_interval": None},
    {"deadline": True}, {"deadline": float("inf")}, {"deadline": "120"}])
def test_invalid_nested_task_configuration_never_enqueues_or_calls(launch_web, config):
    launcher, provider, oj, request, headers = launch_web
    result = request("/api/dashboard/launch", "POST", content=json.dumps(payload(task_config=config)),
                     headers={**headers, "Content-Type": "application/json"})
    assert result.status_code == 422
    assert not launcher.queue.qsize() and not provider.calls and not oj.submissions


def test_configuration_changes_cannot_reuse_nonce_or_be_sent_on_resume(launch_web):
    launcher, provider, oj, request, headers = launch_web
    data = payload(task_config={"max_llm_calls": 5, "max_submissions": 2})
    response = request("/api/dashboard/launch", "POST", json=data, headers=headers)
    task_id = response.json()["task_id"]
    assert request("/api/dashboard/launch", "POST", json=data, headers=headers).json()["task_id"] == task_id
    changed = {**data, "task_config": {"max_llm_calls": 6, "max_submissions": 2}}
    assert request("/api/dashboard/launch", "POST", json=changed, headers=headers).status_code == 409
    assert request("/api/dashboard/launch", "POST", json={**data, "max_cost_cny": 2}, headers=headers).status_code == 422
    oj.interrupt = "wait"
    with pytest.raises(KeyboardInterrupt):
        launcher.run_next()
    launcher.active.clear()
    endpoint = f"/api/dashboard/tasks/{task_id}/resume"
    assert request(endpoint, "POST", json={"request_id": uuid.uuid4().hex,
        "confirm_remote_calls": True, "task_config": {}}, headers=headers).status_code == 422
    assert request(endpoint, "POST", json={"request_id": uuid.uuid4().hex,
        "confirm_remote_calls": True}, headers=headers).status_code == 202
    assert launcher.resume_request(task_id).policy.max_llm_calls == 5
    launcher.run_next()
    assert len(provider.calls) == 2 and len(oj.submissions) == 1


def test_code_only_configuration_cannot_enable_multiple_calls_or_submissions(launch_web):
    launcher, provider, oj, request, headers = launch_web
    for config in ({"max_llm_calls": 2}, {"max_submissions": 2}):
        assert request("/api/dashboard/launch", "POST", json=payload(mode="code-only", task_config=config), headers=headers).status_code == 422
    response = request("/api/dashboard/launch", "POST", json=payload(mode="code-only", task_config={
        "max_llm_calls": 1, "max_submissions": 1}), headers=headers)
    assert response.status_code == 202
    launcher.run_next()
    assert len(provider.calls) == 1 and len(oj.submissions) == 1
    assert not oj.run_calls
