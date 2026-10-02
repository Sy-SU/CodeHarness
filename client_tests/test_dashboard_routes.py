from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

# Base Agent test installs remain independent from optional web dependencies.
pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
from dashboard.app import create_app
from dashboard.cli import main
from dashboard.config import DashboardSettings
from client_tests.test_dashboard_reader import make_task, event, write_json


@pytest.fixture
def web(tmp_path):
    root = tmp_path / "workspace"
    task = make_task(root, "ac")
    config = tmp_path / "models.yaml"
    config.write_text('''models:
  standard:
    provider: fixture
    model: fixture-only
    parameters:
      temperature: 0
      api_key: must-never-display
policy:
  debug_escalation:
    enabled: false
''')
    app = create_app(DashboardSettings(root, model_config=config, env_file=None))

    def request(url, method="GET", **kwargs):
        async def invoke():
            cookies = kwargs.pop("cookies", None)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", cookies=cookies) as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(invoke())
    return root, task, app, request


@pytest.mark.parametrize("url", ["/", "/tasks", "/tasks/ac", "/experiments", "/models", "/api/dashboard/summary", "/api/dashboard/tasks", "/api/dashboard/tasks/ac", "/api/dashboard/tasks/ac/events", "/api/dashboard/experiments", "/api/dashboard/models", "/static/css/dashboard.css", "/static/js/dashboard.js"])
def test_readonly_pages_and_apis(web, url):
    response = web[-1](url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]


def test_html_escape_and_secrets_in_models_and_artifacts(web):
    request = web[-1]
    html = request("/tasks/ac").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "must-never-display" not in request("/models").text
    assert "must-never-display" not in request("/api/dashboard/models").text
    write_json(web[1] / "artifacts" / "result.json", {"api_key": "artifact-secret", "a": "Bearer secret-value"})
    response = request("/tasks/ac/artifacts/artifacts/result.json")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/plain")
    assert "artifact-secret" not in response.text and "secret-value" not in response.text


def test_search_filters_sort_and_pagination(web):
    request = web[-1]
    result = request("/api/dashboard/tasks?task_id=ac&problem_id=t1001&mode=code-only&profile=standard&status=accepted&verdict=AC&sort=duration").json()
    assert result["total"] == 1 and result["tasks"][0]["task_id"] == "ac"
    assert request("/api/dashboard/tasks?verdict=WA").json()["total"] == 0
    assert request("/api/dashboard/tasks?offset=1").json()["tasks"] == []
    assert request("/api/dashboard/tasks?limit=0").status_code == 422
    assert "Trace" not in request("/api/dashboard/tasks").text


def test_events_incremental_cursor_pagination_and_reset(web):
    _, task, _, request = web
    first = request("/api/dashboard/tasks/ac/events?limit=2").json()
    assert len(first["events"]) == 2 and first["next_cursor"] == 2
    generation = first["generation"]
    after = request(f"/api/dashboard/tasks/ac/events?cursor=2&generation={generation}").json()
    assert len(after["events"]) == first["total"] - 2 and not after["reset"]
    with (task / "events.jsonl").open("a") as handle:
        handle.write(json.dumps(event("FUTURE_EVENT", {"value": "<script>bad</script>"})) + "\n")
    new = request(f"/api/dashboard/tasks/ac/events?cursor={first['total']}&generation={generation}").json()
    assert len(new["events"]) == 1 and new["events"][0]["event_type"] == "UNKNOWN EVENT"
    (task / "events.jsonl").write_text(json.dumps(event("TASK_CREATED")) + "\n")
    reset = request(f"/api/dashboard/tasks/ac/events?cursor={new['next_cursor']}&generation={generation}").json()
    assert reset["reset"] and len(reset["events"]) == 1


def test_event_pages_do_not_drop_records_above_sanitizer_metadata_limit(web):
    _, task, _, request = web
    (task / "events.jsonl").write_text("".join(json.dumps(event("STATE_CHANGE", {"n": n})) + "\n" for n in range(600)))
    page = request("/api/dashboard/tasks/ac/events?limit=500").json()
    assert len(page["events"]) == page["next_cursor"] == 500
    assert len(request("/api/dashboard/tasks/ac/events?cursor=500").json()["events"]) == 100


@pytest.mark.parametrize("url", ["/tasks/ac/artifacts/.env", "/tasks/ac/artifacts/%2Fetc%2Fpasswd", "/tasks/ac/artifacts/artifacts/%2E%2E/%2E%2E/secret", "/tasks/%2E%2E/artifacts/state.json", "/api/dashboard/tasks/missing"])
def test_traversal_and_unlisted_files_are_unavailable(web, url):
    assert web[-1](url).status_code == 404


def test_routes_never_change_workspace_or_allow_mutations(web, monkeypatch):
    from agent.models.provider import OpenAICompatibleProvider
    from agent.oj_client.client import OJClient
    monkeypatch.setattr(OpenAICompatibleProvider, "complete", lambda *_args, **_kwargs: pytest.fail("No LLM calls"))
    monkeypatch.setattr(OJClient, "get_problem", lambda *_args, **_kwargs: pytest.fail("No MiniOJ calls"))
    root, _, _, request = web
    def hashes():
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
    before = hashes()
    for url in ["/", "/tasks", "/tasks/ac", "/experiments", "/models", "/api/dashboard/tasks/ac", "/api/dashboard/tasks/ac/events"]:
        assert request(url).status_code == 200
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert request(url, method).status_code == 405
    assert before == hashes()


def test_configuration_selection_loopback_and_cli_guards(web, monkeypatch):
    with pytest.raises(ValueError):
        DashboardSettings(host="0.0.0.0")
    assert DashboardSettings().host == "127.0.0.1"
    assert web[-1]("/", headers={"Host": "attacker.example"}).status_code == 400
    with pytest.raises(SystemExit) as result:
        main(["--host", "0.0.0.0"])
    assert result.value.code == 2
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert main(["--workspace-root", str(web[0])]) == 0
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 8765


def test_partial_writes_do_not_500_and_terminal_signal_is_explicit(web):
    _, task, _, request = web
    first = request("/api/dashboard/tasks/ac").json()
    assert first["summary"]["terminal"]
    (task / "state.json").write_text("{")
    with (task / "events.jsonl").open("a") as handle:
        handle.write('{"type":')
    result = request("/api/dashboard/tasks/ac").json()
    assert result["summary"]["data_status"] == "partial" and result["warnings"]
    assert request("/tasks/ac").status_code == 200
    assert "Timeline" in request("/tasks/ac").text


def test_models_no_selection_and_no_probe_autodiscovery(tmp_path):
    from dashboard.repository.configuration import ConfigurationRepository
    from dashboard.security import Sanitizer
    result = ConfigurationRepository(None, Sanitizer(environ={})).read()
    assert result.profiles == [] and result.roles == {} and result.status == "No configuration selected"
    bad = tmp_path / "bad.yaml"
    bad.write_text("models: [broken]")
    assert ConfigurationRepository(bad, Sanitizer(environ={})).read().status == "Configuration unavailable"


def test_dashboard_extra_is_optional_and_assets_are_in_package():
    root = Path(__file__).parents[1]
    text = (root / "pyproject.toml").read_text()
    default = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert not any(name in default for name in ("fastapi", "jinja2", "uvicorn"))
    assert 'codeharness-dashboard = "dashboard.cli:main"' in text
    assert (root / "dashboard" / "templates" / "base.html").is_file()


def visible_html(response):
    # Do not count the inert translation catalog as rendered page text.
    import re
    return re.sub(r"<script\b[^>]*>.*?</script>", "", response.text, flags=re.S)


@pytest.mark.parametrize("url,english,chinese", [
    ("/", "Total Tasks", "任务总数"),
    ("/tasks?q=ac&verdict=AC&sort=duration", "Search task or problem", "搜索任务或题目"),
    ("/tasks/ac", "Current / final solution", "当前 / 最终解答"),
    ("/experiments", "No runs", "暂无运行"),
    ("/models", "Profile mapping", "Profile 映射"),
])
def test_all_pages_translate_visible_labels_and_remain_readonly(web, url, english, chinese):
    request = web[-1]
    en = visible_html(request(url))
    zh = visible_html(request(url, cookies={"codeharness_language": "zh"}))
    assert '<html lang="en">' in en and '<html lang="zh">' in zh
    assert english in en and chinese in zh
    assert 'aria-current="true" class="selected">中文</a>' in zh
    assert "本地 · 只读" in zh and "发起任务" not in zh
    if url == "/tasks/ac":
        assert "已结束 · 停止刷新" in zh and "未知" in zh
        assert "solution-v1" in zh and "AC" in zh
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in zh


def test_language_preference_persists_across_navigation_and_switches_back(web):
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web[2]), base_url="http://127.0.0.1") as client:
            assert '<html lang="en">' in (await client.get("/")).text
            switch = await client.get("/language/zh", params={"next": "/tasks?q=ac&verdict=AC#timeline"})
            assert switch.status_code == 303
            assert switch.headers["location"] == "/tasks?q=ac&verdict=AC#timeline"
            cookie = switch.headers["set-cookie"]
            assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
            assert '<html lang="zh">' in (await client.get("/tasks?q=ac&verdict=AC")).text
            assert '<html lang="zh">' in (await client.get("/models")).text
            await client.get("/language/en", params={"next": "/tasks/ac"})
            assert '<html lang="en">' in (await client.get("/tasks/ac")).text
    asyncio.run(invoke())


@pytest.mark.parametrize("target", ["https://attacker.example", "//attacker.example", "/\\attacker.example",
    "/%2f%2fattacker.example", "%252f%252fattacker.example", "/tasks%0d%0aLocation:evil"])
def test_language_switch_rejects_external_encoded_and_control_redirects(web, target):
    response = web[-1]("/language/zh", params={"next": target})
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_invalid_language_falls_back_without_catalog_or_redirect_injection(web):
    request = web[-1]
    assert request("/language/fr").status_code == 404
    assert '<html lang="en">' in request("/", cookies={"codeharness_language": "bad-language"}).text
    result = visible_html(request('/tasks?q=%22%3E%3Cscript%3Ealert(9)%3C/script%3E', cookies={"codeharness_language": "zh"}))
    assert '<script>alert(9)</script>' not in result
    assert "&lt;script&gt;" in result


def test_locales_are_per_request_and_do_not_change_records_or_workspace(web, monkeypatch):
    from agent.models.provider import OpenAICompatibleProvider
    from agent.oj_client.client import OJClient
    monkeypatch.setattr(OpenAICompatibleProvider, "complete", lambda *_args, **_kwargs: pytest.fail("No LLM calls"))
    monkeypatch.setattr(OJClient, "get_problem", lambda *_args, **_kwargs: pytest.fail("No MiniOJ calls"))
    root, _, app, request = web
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            results = await asyncio.gather(*[client.get("/tasks/ac", headers={"Cookie": f"codeharness_language={lang}"}) for lang in ("zh", "en") * 4])
            for response, lang in zip(results, ("zh", "en") * 4):
                assert f'<html lang="{lang}">' in response.text
    asyncio.run(invoke())
    for url in ("/api/dashboard/summary", "/api/dashboard/tasks/ac", "/api/dashboard/tasks/ac/events", "/api/dashboard/models", "/tasks/ac/artifacts/solution.cpp"):
        assert request(url).text == request(url, cookies={"codeharness_language": "zh"}).text
    assert before == {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert not any(route.path.startswith("/api/control") or route.path == "/run" for route in app.routes)


def test_partial_data_and_unknown_placeholders_are_translated_without_altering_ids(web):
    _, task, _, request = web
    (task / "state.json").write_text("{")
    with (task / "events.jsonl").open("a") as handle:
        handle.write('{"type":')
    html = visible_html(request("/tasks/ac", cookies={"codeharness_language": "zh"}))
    assert "state.json：不可用 / 数据不完整" in html
    assert "JSONL 尾行未完成，等待追加" in html
    assert "未知" in html and "每 2 秒刷新" in html
