from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "__init__.py"


def plugin_config(*identities: str) -> dict[str, Any]:
    return {
        "plugins": {
            "entries": {
                "hermes-olla-sticky-sessions": {
                    "provider_identities": list(identities),
                }
            }
        }
    }


class FakePluginContext:
    def __init__(self) -> None:
        self.middleware: list[tuple[str, Callable[..., dict[str, Any] | None]]] = []

    def register_middleware(self, kind: str, callback: Callable[..., dict[str, Any] | None]) -> None:
        self.middleware.append((kind, callback))


def load_plugin(
    monkeypatch: Any,
    lookup: Callable[[str], str | None],
    config_value: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
) -> ModuleType:
    hermes_cli = ModuleType("hermes_cli")
    hermes_cli.__path__ = []  # type: ignore[attr-defined]
    runtime_provider = ModuleType("hermes_cli.runtime_provider")
    runtime_provider.find_custom_provider_identity = lookup  # type: ignore[attr-defined]
    config = ModuleType("hermes_cli.config")
    if callable(config_value):
        config.load_config = config_value  # type: ignore[attr-defined]
    else:
        configured = config_value if config_value is not None else plugin_config("custom:thunder-forge")
        config.load_config = lambda: configured  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", runtime_provider)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config)

    spec = importlib.util.spec_from_file_location("test_hermes_olla_sticky_sessions_plugin", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registered_callback(module: ModuleType) -> Callable[..., dict[str, Any] | None]:
    context = FakePluginContext()
    module.register(context)

    assert len(context.middleware) == 1
    kind, callback = context.middleware[0]
    assert kind == "llm_request"
    return callback


def test_registers_exactly_one_llm_request_middleware(monkeypatch: Any) -> None:
    module = load_plugin(monkeypatch, lambda _: "custom:thunder-forge")

    registered_callback(module)


def test_versions_and_manifest_are_consistent(monkeypatch: Any) -> None:
    load_plugin(monkeypatch, lambda _: "custom:thunder-forge")
    manifest_text = (ROOT / "plugin.yaml").read_text()
    manifest = yaml.safe_load(manifest_text)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert manifest == {
        "manifest_version": 1,
        "name": "hermes-olla-sticky-sessions",
        "version": "0.1.0",
        "description": "Add conversation-scoped Olla sticky sessions to explicitly allowed providers",
    }
    assert manifest["version"] == project["project"]["version"]
    assert f"## [{manifest['version']}]" in (ROOT / "CHANGELOG.md").read_text()
    assert "THUNDER_FORGE_BASE_URL" not in manifest_text
    assert "requires_env" not in manifest


def test_matching_provider_injects_deterministic_sticky_header(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []
    module = load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge")
    callback = registered_callback(module)
    request = {"messages": [], "extra_headers": {"X-Trace-ID": "trace-1"}}
    original_headers = request["extra_headers"]
    original_snapshot = {"messages": [], "extra_headers": {"X-Trace-ID": "trace-1"}}

    result = callback(
        request=request,
        session_id="session-alpha",
        model="agent-better",
        base_url="http://thunder-forge.example/v1",
    )

    assert result is not None
    assert result["source"] == "hermes-olla-sticky-sessions"
    updated = result["request"]
    expected_digest = hashlib.sha256(b"session-alpha\0agent-better").hexdigest()[:32]
    assert updated["extra_headers"] == {
        "X-Trace-ID": "trace-1",
        "X-Olla-Session-ID": f"hermes-{expected_digest}",
    }
    assert updated is not request
    assert updated["extra_headers"] is not original_headers
    assert request == original_snapshot
    assert lookup_calls == ["http://thunder-forge.example/v1"]


def test_non_thunder_forge_provider_leaves_request_unchanged(monkeypatch: Any) -> None:
    module = load_plugin(monkeypatch, lambda _: "custom:another-provider")
    callback = registered_callback(module)
    request = {"messages": []}

    assert callback(request=request, session_id="session-alpha", model="agent", base_url="http://other/v1") is None
    assert request == {"messages": []}


def test_second_explicitly_allowed_provider_injects_header(monkeypatch: Any) -> None:
    module = load_plugin(
        monkeypatch,
        lambda _: "custom:olla-lab",
        plugin_config("custom:thunder-forge", "custom:olla-lab"),
    )
    callback = registered_callback(module)

    result = callback(request={}, session_id="session-alpha", model="agent", base_url="http://olla-lab/v1")

    assert result is not None
    assert "X-Olla-Session-ID" in result["request"]["extra_headers"]


def test_missing_provider_configuration_fails_closed_without_lookup(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []
    module = load_plugin(
        monkeypatch,
        lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge",
        {},
    )
    callback = registered_callback(module)

    assert callback(request={}, session_id="session-alpha", model="agent", base_url="http://tf/v1") is None
    assert lookup_calls == []


def test_empty_provider_allow_list_fails_closed_without_lookup(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []
    module = load_plugin(
        monkeypatch,
        lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge",
        plugin_config(),
    )
    callback = registered_callback(module)

    assert callback(request={}, session_id="session-alpha", model="agent", base_url="http://tf/v1") is None
    assert lookup_calls == []


def test_malformed_provider_allow_list_fails_closed_without_lookup(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []
    config_value = plugin_config("custom:thunder-forge")
    config_value["plugins"]["entries"]["hermes-olla-sticky-sessions"]["provider_identities"].append("*")
    module = load_plugin(
        monkeypatch,
        lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge",
        config_value,
    )
    callback = registered_callback(module)

    assert callback(request={}, session_id="session-alpha", model="agent", base_url="http://tf/v1") is None
    assert lookup_calls == []


def test_provider_config_load_failure_fails_closed_without_lookup(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []

    def fail_config_load() -> dict[str, Any]:
        raise RuntimeError("config unavailable")

    module = load_plugin(
        monkeypatch,
        lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge",
        fail_config_load,
    )
    callback = registered_callback(module)

    assert callback(request={}, session_id="session-alpha", model="agent", base_url="http://tf/v1") is None
    assert lookup_calls == []


def test_empty_session_leaves_request_unchanged_without_lookup(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []
    module = load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge")
    callback = registered_callback(module)
    request = {"messages": []}

    assert callback(request=request, session_id="", model="agent", base_url="http://thunder-forge/v1") is None
    assert lookup_calls == []
    assert request == {"messages": []}


def test_provider_lookup_failure_leaves_request_unchanged(monkeypatch: Any) -> None:
    def fail_lookup(_: str) -> str | None:
        raise RuntimeError("provider registry unavailable")

    module = load_plugin(monkeypatch, fail_lookup)
    callback = registered_callback(module)
    request = {"messages": [], "extra_headers": {"X-Trace-ID": "trace-1"}}

    assert callback(request=request, session_id="session-alpha", model="agent", base_url="http://tf/v1") is None
    assert request == {"messages": [], "extra_headers": {"X-Trace-ID": "trace-1"}}


def test_explicit_sticky_header_is_preserved_case_insensitively(monkeypatch: Any) -> None:
    module = load_plugin(monkeypatch, lambda _: "custom:thunder-forge")
    callback = registered_callback(module)
    headers = {"x-OLLA-session-id": "caller-session", "X-Trace-ID": "trace-1"}
    request = {"messages": [], "extra_headers": headers}

    assert callback(request=request, session_id="session-alpha", model="agent", base_url="http://tf/v1") is None
    assert request["extra_headers"] is headers
    assert request["extra_headers"] == {
        "x-OLLA-session-id": "caller-session",
        "X-Trace-ID": "trace-1",
    }


def test_key_scope_and_format(monkeypatch: Any) -> None:
    module = load_plugin(monkeypatch, lambda _: "custom:thunder-forge")
    callback = registered_callback(module)

    def key(session_id: str, model: str) -> str:
        result = callback(request={}, session_id=session_id, model=model, base_url="http://tf/v1")
        assert result is not None
        return result["request"]["extra_headers"]["X-Olla-Session-ID"]

    first = key("session-alpha", "agent")
    repeat = key("session-alpha", "agent")
    new_session = key("session-beta", "agent")
    new_model = key("session-alpha", "coder")

    assert first == repeat
    assert first != new_session
    assert first != new_model
    assert re.fullmatch(r"hermes-[0-9a-f]{32}", first)


def test_key_exposes_no_raw_inputs_or_secret_fixtures(monkeypatch: Any) -> None:
    module = load_plugin(monkeypatch, lambda _: "custom:thunder-forge")
    callback = registered_callback(module)
    session_id = "private-session-name"
    model = "private-model-alias"
    endpoint = "http://private-endpoint.example/v1"
    secret = "tf-secret-fixture"

    result = callback(request={}, session_id=session_id, model=model, base_url=endpoint, api_key=secret)

    assert result is not None
    key = result["request"]["extra_headers"]["X-Olla-Session-ID"]
    assert all(raw not in key for raw in (session_id, model, endpoint, secret))
