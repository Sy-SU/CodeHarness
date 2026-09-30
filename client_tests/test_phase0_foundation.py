from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from agent.config import ClientConfigurationError, ClientSettings
from agent.models.registry import ModelRegistry


ROOT = Path(__file__).parents[1]


def test_client_packages_import_without_minioj_server():
    for module in (
        "agent",
        "agent.cli",
        "agent.config",
        "agent.core.agent",
        "agent.models.registry",
        "agent.oj_client",
        "agent.tools.runtime",
        "agent.workspace.task",
        "experiments.summarize",
    ):
        importlib.import_module(module)


def test_client_source_has_no_minioj_internal_imports():
    forbidden = []
    for package in (ROOT / "agent", ROOT / "experiments"):
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name == "oj" or name.startswith("oj.") or name == "shared" or name.startswith("shared."):
                        forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
    assert forbidden == []


def test_client_configuration_loads_only_explicit_connection_values():
    settings = ClientSettings.from_environment(
        {
            "OJ_BASE_URL": "https://minioj.example.test/root/",
            "OJ_API_TOKEN": "oj_test_value",
            "MODEL_CONFIG": "config/models.example.yaml",
            # A server-only value must have no effect on the client settings.
            "DATABASE_URL": "sqlite:///must-not-be-used.db",
        }
    )
    assert settings.oj_base_url == "https://minioj.example.test/root"
    assert settings.oj_api_token == "oj_test_value"
    assert settings.model_config == Path("config/models.example.yaml")
    assert "oj_test_value" not in repr(settings)


@pytest.mark.parametrize(
    "values",
    (
        {},
        {"OJ_BASE_URL": "https://minioj.example.test", "OJ_API_TOKEN": ""},
        {"OJ_BASE_URL": "not-a-url", "OJ_API_TOKEN": "oj_test"},
        {"OJ_BASE_URL": "https://user:pass@example.test", "OJ_API_TOKEN": "oj_test"},
    ),
)
def test_client_configuration_rejects_missing_or_unsafe_values(values):
    with pytest.raises(ClientConfigurationError):
        ClientSettings.from_environment(values)


def test_non_secret_model_example_parses_without_calling_a_model(monkeypatch):
    monkeypatch.setenv("BAILIAN_BASE_URL", "https://provider.example.test/v1")
    monkeypatch.setenv("BAILIAN_API_KEY", "test-only-placeholder")
    registry = ModelRegistry.from_yaml(ROOT / "config" / "models.example.yaml")
    assert {profile.value for profile in registry.models} == {
        "fast",
        "standard",
        "strong",
        "max",
    }


def test_environment_example_is_client_only_and_contains_no_secret():
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    assert set(values) == {
        "OJ_BASE_URL",
        "OJ_API_TOKEN",
        "BAILIAN_API_KEY",
        "BAILIAN_BASE_URL",
        "MODEL_CONFIG",
    }
    assert values["OJ_API_TOKEN"] == ""
    assert values["BAILIAN_API_KEY"] == ""
    assert values["OJ_BASE_URL"] == ""
    assert values["BAILIAN_BASE_URL"] == ""
