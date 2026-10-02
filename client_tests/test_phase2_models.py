from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
import yaml

from agent.core.policy import DebugEscalationPolicy, ModelPolicy
from agent.models.cli import main as model_cli_main
from agent.models.provider import OpenAICompatibleProvider
from agent.models.registry import (
    ModelConfigurationError,
    ModelDefinition,
    ModelRegistry,
)
from agent.models.router import ModelRouter
from agent.models.runtime import ModelCallFailed, ModelCallRuntime
from agent.models.types import (
    AgentRole,
    ChatMessage,
    CostEstimate,
    LLMCallStatus,
    LLMError,
    LLMErrorKind,
    LLMResponse,
    ModelProfile,
    TokenUsage,
)
from agent.workspace.task import TaskWorkspace
from experiments.summarize import summarize


def make_provider(handler):
    return OpenAICompatibleProvider(
        "test-provider",
        "https://models.example.test/v1",
        "test-secret-key",
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_provider_normalizes_success_usage_tool_calls_and_configured_parameters():
    observed = {}

    def handler(request):
        observed.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-secret-key"
        return httpx.Response(
            200,
            headers={"x-request-id": "req_header"},
            json={
                "id": "req_body",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{\"relative\":\"solution.cpp\"}",
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )

    response = make_provider(handler).complete(
        [ChatMessage("user", "inspect")],
        model="model-a",
        profile=ModelProfile.STANDARD,
        parameters={"temperature": 0},
    )
    assert response.status is LLMCallStatus.SUCCEEDED
    assert response.content == ""
    assert response.usage == TokenUsage(11, 7)
    assert response.request_id == "req_header"
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"relative": "solution.cpp"}
    assert observed["temperature"] == 0
    assert observed["model"] == "model-a"


def test_provider_preserves_missing_usage_instead_of_inventing_zero():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "req_no_usage",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )

    response = make_provider(handler).complete(
        [ChatMessage("user", "hello")],
        model="model-a",
        profile=ModelProfile.FAST,
        parameters={},
    )
    assert response.succeeded
    assert response.usage is None


def test_provider_returns_safe_typed_http_failure_without_response_body_or_key():
    def handler(request):
        return httpx.Response(
            429,
            headers={"x-request-id": "req_rate"},
            json={"error": {"message": "server echoed test-secret-key"}},
        )

    provider = make_provider(handler)
    response = provider.complete(
        [ChatMessage("user", "hello")],
        model="model-a",
        profile=ModelProfile.FAST,
        parameters={},
    )
    assert not response.succeeded
    assert response.error == LLMError(
        kind=LLMErrorKind.HTTP,
        message="test-provider model request returned HTTP 429",
        status_code=429,
        retryable=True,
    )
    assert response.request_id == "req_rate"
    assert "test-secret-key" not in repr(provider)
    assert "test-secret-key" not in str(response)


def test_provider_classifies_transport_and_protocol_failures():
    def transport_failure(request):
        raise httpx.ConnectError("do not expose this detail", request=request)

    transport = make_provider(transport_failure).complete(
        [ChatMessage("user", "hello")],
        model="model-a",
        profile=ModelProfile.FAST,
        parameters={},
    )
    assert transport.error.kind is LLMErrorKind.TRANSPORT
    assert transport.error.retryable
    assert "do not expose" not in transport.error.message

    protocol = make_provider(
        lambda request: httpx.Response(200, json={"choices": []})
    ).complete(
        [ChatMessage("user", "hello")],
        model="model-a",
        profile=ModelProfile.FAST,
        parameters={},
    )
    assert protocol.error.kind is LLMErrorKind.PROTOCOL


def config_data():
    models = {
        profile.value: {
            "provider": "test",
            "model": f"model-{profile.value}",
            "parameters": {"temperature": 0.1},
        }
        for profile in ModelProfile
    }
    return {
        "providers": {
            "test": {
                "type": "openai-compatible",
                "base_url_env": "MODEL_BASE_URL",
                "api_key_env": "MODEL_API_KEY",
                "timeout_seconds": 15,
                "supported_parameters": ["temperature"],
            }
        },
        "models": models,
        "policy": {
            "roles": {
                "PLAN": "strong",
                "CODE": "standard",
                "TEST_GENERATION": "strong",
                "DEBUG": "standard",
                "REVIEW": "strong",
            },
            "debug_escalation": {"enabled": False},
        },
    }


def write_config(tmp_path, data):
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_registry_loads_four_routes_parameters_and_unknown_pricing(tmp_path):
    registry = ModelRegistry.from_yaml(
        write_config(tmp_path, config_data()),
        environ={
            "MODEL_BASE_URL": "https://models.example.test/v1",
            "MODEL_API_KEY": "secret",
        },
    )
    assert set(registry.models) == set(ModelProfile)
    definition = registry.definition(ModelProfile.STANDARD)
    assert definition.model == "model-standard"
    assert definition.parameters == {"temperature": 0.1}
    assert not definition.pricing_known


@pytest.mark.parametrize(
    "mutate, match",
    [
        (
            lambda data: data["providers"]["test"].update(
                {"supported_parameters": []}
            ),
            "Unsupported parameters",
        ),
        (
            lambda data: data["models"]["max"].pop("model"),
            "Invalid model ID",
        ),
        (
            lambda data: data["models"]["fast"].update(
                {"input_cost_per_million": 1}
            ),
            "requires both",
        ),
        (
            lambda data: data["providers"]["test"].update(
                {"timeout_seconds": float("nan")}
            ),
            "positive number",
        ),
    ],
)
def test_registry_rejects_invalid_parameters_routes_pricing_and_timeout(
    tmp_path, mutate, match
):
    data = config_data()
    mutate(data)
    with pytest.raises(ModelConfigurationError, match=match):
        ModelRegistry.from_yaml(
            write_config(tmp_path, data),
            environ={
                "MODEL_BASE_URL": "https://models.example.test/v1",
                "MODEL_API_KEY": "secret",
            },
        )


def test_registry_rejects_missing_environment_and_missing_profile(tmp_path):
    with pytest.raises(ModelConfigurationError, match="missing its base URL"):
        ModelRegistry.from_yaml(write_config(tmp_path, config_data()), environ={})
    data = config_data()
    del data["models"]["max"]
    with pytest.raises(ModelConfigurationError, match="Missing model profiles: max"):
        ModelRegistry.from_yaml(
            write_config(tmp_path, data),
            environ={
                "MODEL_BASE_URL": "https://models.example.test/v1",
                "MODEL_API_KEY": "secret",
            },
        )


class StaticProvider:
    name = "fake"

    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, messages, *, model, profile, parameters):
        self.calls.append((messages, model, profile, parameters))
        return replace(
            self.response,
            provider=self.name,
            model=model,
            profile=profile,
        )


def make_router(response, *, priced=False):
    provider = StaticProvider(response)
    definition = ModelDefinition(
        provider="fake",
        model="fake-model",
        parameters={"temperature": 0},
        input_cost_per_million=2.0 if priced else None,
        output_cost_per_million=4.0 if priced else None,
        currency="USD" if priced else None,
    )
    registry = ModelRegistry(
        {"fake": provider},
        {ModelProfile.STANDARD: definition},
    )
    return ModelRouter(registry), provider


def success_response(usage=TokenUsage(10, 5)):
    return LLMResponse(
        content="ok",
        usage=usage,
        provider="fake",
        model="fake-model",
        profile=ModelProfile.STANDARD,
        request_id="req_1",
        latency_ms=3,
    )


def test_router_passes_configured_route_and_distinguishes_known_unknown_cost():
    router, provider = make_router(success_response(), priced=True)
    response = router.complete(ModelProfile.STANDARD, [ChatMessage("user", "hi")])
    assert provider.calls[0][1:] == (
        "fake-model",
        ModelProfile.STANDARD,
        {"temperature": 0},
    )
    estimate = router.estimate_cost(response)
    assert estimate.known
    assert estimate.currency == "USD"
    assert estimate.amount == pytest.approx(0.00004)

    unknown_price, _ = make_router(success_response(), priced=False)
    assert unknown_price.estimate_cost(response).reason == "missing_pricing"
    missing_usage, _ = make_router(success_response(None), priced=True)
    assert missing_usage.estimate_cost(success_response(None)).reason == "missing_usage"


def test_policy_keeps_mixed_defaults_and_never_implicitly_escalates():
    policy = ModelPolicy()
    assert policy.choose(AgentRole.PLAN) is ModelProfile.STRONG
    assert policy.choose(AgentRole.CODE) is ModelProfile.STANDARD
    assert (
        policy.choose(AgentRole.DEBUG, prior_failures=99, failure_kind="WA")
        is ModelProfile.STANDARD
    )


def test_policy_escalates_only_for_explicit_threshold_and_failure_kind():
    policy = ModelPolicy(
        debug_escalation=DebugEscalationPolicy(
            enabled=True,
            threshold=2,
            failure_kinds=frozenset({"WA", "CE"}),
        )
    )
    assert (
        policy.choose(AgentRole.DEBUG, prior_failures=1, failure_kind="WA")
        is ModelProfile.STANDARD
    )
    assert (
        policy.choose(AgentRole.DEBUG, prior_failures=2, failure_kind="IE")
        is ModelProfile.STANDARD
    )
    assert (
        policy.choose(AgentRole.DEBUG, prior_failures=2, failure_kind="WA")
        is ModelProfile.STRONG
    )


def test_policy_loads_role_mapping_and_explicit_escalation_from_yaml(tmp_path):
    data = config_data()
    data["policy"]["debug_escalation"] = {
        "enabled": True,
        "threshold": 3,
        "failure_kinds": ["WA"],
        "from_profile": "standard",
        "to_profile": "strong",
    }
    policy = ModelPolicy.from_yaml(write_config(tmp_path, data))
    assert policy.choose(AgentRole.REVIEW) is ModelProfile.STRONG
    assert (
        policy.choose(AgentRole.DEBUG, prior_failures=3, failure_kind="WA")
        is ModelProfile.STRONG
    )


def test_model_runtime_records_success_usage_cost_and_correlated_trace(tmp_path):
    router, _ = make_router(success_response(), priced=True)
    workspace = TaskWorkspace.create(
        tmp_path,
        "phase2-success",
        "model-probe",
        "phase2-model-probe",
        trace_schema_version="phase2-v1",
    )
    response = ModelCallRuntime(router, workspace).complete(
        AgentRole.CODE,
        ModelProfile.STANDARD,
        [ChatMessage("user", "hello")],
    )
    assert response.succeeded
    assert workspace.state.llm_call_count == 1
    assert workspace.state.llm_success_count == 1
    assert workspace.state.llm_failure_count == 0
    assert workspace.state.input_tokens == 10
    assert workspace.state.output_tokens == 5
    assert workspace.state.cost_estimate_status == "known"
    assert workspace.state.cost_currency == "USD"
    records = [
        json.loads(line)
        for line in (workspace.root / "events.jsonl").read_text().splitlines()
    ]
    assert [record["type"] for record in records] == ["LLM_CALL", "LLM_RESPONSE"]
    assert records[0]["schema_version"] == "phase2-v1"
    assert (
        records[0]["payload"]["correlation_id"]
        == records[1]["payload"]["correlation_id"]
    )
    assert records[1]["payload"]["usage_available"] is True


def test_model_runtime_records_failure_and_missing_usage_without_zero_cost(tmp_path):
    failure = LLMResponse.failure(
        provider="fake",
        model="fake-model",
        profile=ModelProfile.STANDARD,
        error=LLMError(
            kind=LLMErrorKind.HTTP,
            message="fake model request returned HTTP 503",
            status_code=503,
            retryable=True,
        ),
    )
    router, _ = make_router(failure, priced=False)
    workspace = TaskWorkspace.create(
        tmp_path,
        "phase2-failure",
        "model-probe",
        "phase2-model-probe",
        trace_schema_version="phase2-v1",
    )
    with pytest.raises(ModelCallFailed, match="HTTP 503"):
        ModelCallRuntime(router, workspace).complete(
            AgentRole.CODE,
            ModelProfile.STANDARD,
            [ChatMessage("user", "hello")],
        )
    assert workspace.state.llm_call_count == 1
    assert workspace.state.llm_success_count == 0
    assert workspace.state.llm_failure_count == 1
    assert workspace.state.llm_usage_missing_count == 1
    assert workspace.state.model_failures_by_kind == {"http": 1}
    assert workspace.state.estimated_cost is None
    assert workspace.state.cost_estimate_status == "missing_usage"
    response_record = json.loads(
        (workspace.root / "events.jsonl").read_text().splitlines()[-1]
    )
    assert response_record["payload"]["status"] == "failed"
    assert response_record["payload"]["error"]["kind"] == "http"


def test_model_probe_requires_explicit_confirmation_before_configuration(monkeypatch):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("configuration must not load")

    monkeypatch.setattr(ModelRegistry, "from_yaml", forbidden)
    with pytest.raises(SystemExit, match="requires --confirm-call"):
        model_cli_main(["probe", "--profile", "fast"])
    assert not called


def test_summary_exposes_failed_calls_missing_usage_and_unknown_cost(tmp_path):
    task = tmp_path / "phase2-task"
    task.mkdir()
    (task / "state.json").write_text(
        json.dumps(
            {
                "mode": "phase2-model-probe",
                "experiment_variant": "fast",
                "solved": False,
                "llm_call_count": 1,
                "llm_success_count": 0,
                "llm_failure_count": 1,
                "llm_usage_missing_count": 1,
                "estimated_cost": None,
                "cost_estimate_status": "missing_usage",
                "calls_per_model_profile": {"fast": 1},
                "model_failures_by_kind": {"http": 1},
            }
        ),
        encoding="utf-8",
    )
    result = summarize(tmp_path)["phase2-model-probe:fast"]
    assert result["average_llm_failures"] == 1
    assert result["average_llm_usage_missing"] == 1
    assert result["average_estimated_cost"] is None
    assert result["unknown_estimated_cost_tasks"] == 1
    assert result["model_failures_by_kind"] == {"http": 1}
