from __future__ import annotations

import hashlib
import hmac
import importlib.util
import os
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
PLUGIN_NAME = "hermes-custom-header-plugin"


def header_rule(
    *,
    namespace: str = "installation-a",
    inputs: list[str] | None = None,
    prefix: str = "hermes-",
    digest_length: int = 32,
    strategy: str = "sha256",
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "namespace": namespace,
        "inputs": inputs if inputs is not None else ["session_id", "model"],
        "prefix": prefix,
        "digest_length": digest_length,
    }


def plugin_config(
    providers: dict[str, dict[str, dict[str, Any]]] | None = None,
    *,
    runtime_providers: dict[str, dict[str, Any]] | None = None,
    legacy_runtime_providers: list[dict[str, Any]] | None = None,
    entry_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configured = (
        providers
        if providers is not None
        else {
            "custom:thunder-forge": {
                "headers": {
                    "X-Olla-Session-ID": header_rule(),
                }
            }
        }
    )
    entry: dict[str, Any] = {"providers": configured}
    if entry_metadata:
        entry.update(entry_metadata)
    config: dict[str, Any] = {
        "plugins": {
            "entries": {
                PLUGIN_NAME: entry,
            }
        }
    }
    if runtime_providers is not None:
        config["providers"] = runtime_providers
    if legacy_runtime_providers is not None:
        config["custom_providers"] = legacy_runtime_providers
    return config


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
        configured = config_value if config_value is not None else plugin_config()
        config.load_config = lambda: configured  # type: ignore[attr-defined]
    config.get_env_value = lambda key: os.environ.get(key)  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", runtime_provider)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config)

    spec = importlib.util.spec_from_file_location("test_hermes_custom_header_plugin", PLUGIN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
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
    registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge"))


def test_versions_and_manifest_are_consistent(monkeypatch: Any) -> None:
    load_plugin(monkeypatch, lambda _: "custom:thunder-forge")
    manifest_text = (ROOT / "plugin.yaml").read_text()
    manifest = yaml.safe_load(manifest_text)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert manifest == {
        "manifest_version": 1,
        "name": PLUGIN_NAME,
        "version": "0.3.0",
        "description": "Add computed request headers to explicitly configured custom providers",
    }
    assert manifest["version"] == project["project"]["version"]
    assert project["project"]["name"] == PLUGIN_NAME
    assert f"## [{manifest['version']}]" in (ROOT / "CHANGELOG.md").read_text()
    assert "requires_env" not in manifest


def test_matching_provider_injects_configured_header_without_mutation(monkeypatch: Any) -> None:
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
    assert result["source"] == PLUGIN_NAME
    expected_digest = hashlib.sha256(b"installation-a\0session-alpha\0agent-better").hexdigest()[:32]
    assert result["request"]["extra_headers"] == {
        "X-Trace-ID": "trace-1",
        "X-Olla-Session-ID": f"hermes-{expected_digest}",
    }
    assert result["request"] is not request
    assert result["request"]["extra_headers"] is not original_headers
    assert request == original_snapshot
    assert lookup_calls == ["http://thunder-forge.example/v1"]


def test_accepts_hermes_managed_plugin_entry_fields(monkeypatch: Any) -> None:
    config = plugin_config(entry_metadata={"allow_tool_override": False, "llm": {"enabled": False}, "future": True})
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge", config))

    result = callback(request={}, session_id="s1", model="m1", base_url="http://tf/v1")

    assert result is not None
    assert "X-Olla-Session-ID" in result["request"]["extra_headers"]


def test_header_name_and_value_recipe_are_configurable(monkeypatch: Any) -> None:
    config = plugin_config(
        {
            "custom:local-lab": {
                "headers": {
                    "X-Local-Affinity": header_rule(
                        inputs=["session_id"],
                        prefix="conversation-",
                        digest_length=16,
                    )
                }
            }
        }
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:local-lab", config))

    result = callback(request={}, session_id="session-alpha", model="agent", base_url="http://lab/v1")

    assert result is not None
    expected = hashlib.sha256(b"installation-a\0session-alpha").hexdigest()[:16]
    assert result["request"]["extra_headers"] == {"X-Local-Affinity": f"conversation-{expected}"}


def test_hmac_sha256_uses_an_installation_secret(monkeypatch: Any) -> None:
    secret = "0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("HERMES_CUSTOM_HEADER_HMAC_KEY", secret)
    config = plugin_config(
        {
            "custom:local-lab": {
                "headers": {
                    "X-Local-Affinity": header_rule(strategy="hmac-sha256"),
                }
            }
        }
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:local-lab", config))

    result = callback(request={}, session_id="session-alpha", model="agent", base_url="http://lab/v1")

    assert result is not None
    expected = hmac.new(
        secret.encode(),
        b"installation-a\0session-alpha\0agent",
        hashlib.sha256,
    ).hexdigest()[:32]
    value = result["request"]["extra_headers"]["X-Local-Affinity"]
    assert value == f"hermes-{expected}"
    assert secret not in value


def test_hmac_secret_is_required_and_never_logged(monkeypatch: Any, caplog: Any) -> None:
    config = plugin_config(
        {
            "custom:local-lab": {
                "headers": {
                    "X-Local-Affinity": header_rule(strategy="hmac-sha256"),
                }
            }
        }
    )
    for secret in (None, "short-secret"):
        caplog.clear()
        if secret is None:
            monkeypatch.delenv("HERMES_CUSTOM_HEADER_HMAC_KEY", raising=False)
        else:
            monkeypatch.setenv("HERMES_CUSTOM_HEADER_HMAC_KEY", secret)
        lookup_calls: list[str] = []
        callback = registered_callback(
            load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:local-lab", config)
        )

        assert callback(request={}, session_id="s1", model="m1", base_url="http://lab/v1") is None
        assert lookup_calls == []
        assert "must contain at least 32 UTF-8 bytes" in caplog.text
        if secret:
            assert secret not in caplog.text


def test_multiple_headers_are_supported_for_one_provider(monkeypatch: Any) -> None:
    config = plugin_config(
        {
            "custom:local-lab": {
                "headers": {
                    "X-Conversation": header_rule(inputs=["session_id"], prefix="", digest_length=64),
                    "X-Model-Conversation": header_rule(prefix="hc-", digest_length=16),
                }
            }
        }
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:local-lab", config))

    result = callback(request={}, session_id="s1", model="m1", base_url="http://lab/v1")

    assert result is not None
    assert set(result["request"]["extra_headers"]) == {"X-Conversation", "X-Model-Conversation"}


def test_different_explicit_provider_can_have_a_different_recipe(monkeypatch: Any) -> None:
    config = plugin_config(
        {
            "custom:thunder-forge": {"headers": {"X-Olla-Session-ID": header_rule()}},
            "custom:local-lab": {"headers": {"X-Local-Affinity": header_rule(inputs=["session_id"], prefix="lab-")}},
        }
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:local-lab", config))

    result = callback(request={}, session_id="s1", model="m1", base_url="http://lab/v1")

    assert result is not None
    assert "X-Local-Affinity" in result["request"]["extra_headers"]
    assert "X-Olla-Session-ID" not in result["request"]["extra_headers"]


def test_unconfigured_provider_leaves_request_unchanged(monkeypatch: Any) -> None:
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:another-provider"))
    request = {"messages": []}

    assert callback(request=request, session_id="session-alpha", model="agent", base_url="http://other/v1") is None
    assert request == {"messages": []}


def test_shared_provider_base_url_fails_closed_before_lookup(monkeypatch: Any, caplog: Any) -> None:
    shared_url = "http://shared.example/v1"
    config = plugin_config(
        runtime_providers={
            "first": {"api": shared_url},
            "second": {"api": f"{shared_url}/"},
        }
    )
    lookup_calls: list[str] = []
    callback = registered_callback(
        load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:first", config)
    )

    assert callback(request={}, session_id="s1", model="m1", base_url=shared_url) is None
    assert lookup_calls == []
    assert "shared custom-provider base URL" in caplog.text
    assert shared_url not in caplog.text


def test_current_and_legacy_entries_for_same_provider_are_not_ambiguous(monkeypatch: Any) -> None:
    shared_url = "http://shared.example/v1"
    config = plugin_config(
        runtime_providers={"thunder-forge": {"api": shared_url}},
        legacy_runtime_providers=[{"name": "thunder-forge", "base_url": shared_url}],
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge", config))

    result = callback(request={}, session_id="s1", model="m1", base_url=shared_url)

    assert result is not None
    assert "X-Olla-Session-ID" in result["request"]["extra_headers"]


def test_missing_or_empty_configuration_fails_closed_without_lookup(monkeypatch: Any) -> None:
    for config in ({}, plugin_config({})):
        lookup_calls: list[str] = []
        callback = registered_callback(
            load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge", config)
        )

        assert callback(request={}, session_id="session-alpha", model="agent", base_url="http://tf/v1") is None
        assert lookup_calls == []


def test_malformed_provider_or_rule_configuration_fails_closed(monkeypatch: Any) -> None:
    missing_namespace = header_rule()
    del missing_namespace["namespace"]
    invalid_provider_configs = [
        {"*": {"headers": {"X-Test": header_rule()}}},
        {"custom:provider": {"headers": {"Authorization": header_rule()}}},
        {"custom:provider": {"headers": {"Keep-Alive": header_rule()}}},
        {"custom:provider": {"headers": {"Proxy-Connection": header_rule()}}},
        {"custom:provider": {"headers": {"Bad Header": header_rule()}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(strategy="raw")}}},
        {"custom:provider": {"headers": {"X-Test": missing_namespace}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(namespace="")}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(namespace="bad namespace")}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(namespace="a" * 65)}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(inputs=[])}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(inputs=["model"])}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(inputs=["session_id", "unknown"])}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(prefix="bad\nvalue")}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(prefix=" leading")}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(prefix="a" * 129)}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(digest_length=15)}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(digest_length=65)}}},
        {"custom:provider": {"headers": {"X-Test": header_rule(digest_length=True)}}},
    ]
    for providers in invalid_provider_configs:
        lookup_calls: list[str] = []
        callback = registered_callback(
            load_plugin(
                monkeypatch,
                lambda base_url: lookup_calls.append(base_url) or "custom:provider",
                plugin_config(providers),
            )
        )

        assert callback(request={}, session_id="session-alpha", model="agent", base_url="http://provider/v1") is None
        assert lookup_calls == []


def test_config_load_or_provider_lookup_failure_fails_closed(monkeypatch: Any, caplog: Any) -> None:
    def fail_config_load() -> dict[str, Any]:
        raise RuntimeError("private-endpoint.example provider-secret-fixture")

    config_lookup_calls: list[str] = []
    callback = registered_callback(
        load_plugin(
            monkeypatch,
            lambda base_url: config_lookup_calls.append(base_url) or "custom:thunder-forge",
            fail_config_load,
        )
    )
    assert callback(request={}, session_id="s1", model="m1", base_url="http://tf/v1") is None
    assert config_lookup_calls == []
    assert "Hermes configuration could not be loaded" in caplog.text
    assert "private-endpoint.example" not in caplog.text
    assert "provider-secret-fixture" not in caplog.text

    def fail_lookup(_: str) -> str | None:
        raise RuntimeError("provider registry unavailable")

    callback = registered_callback(load_plugin(monkeypatch, fail_lookup))
    assert callback(request={}, session_id="s1", model="m1", base_url="http://tf/v1") is None


def test_empty_required_runtime_input_fails_closed(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []
    callback = registered_callback(
        load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge")
    )

    assert callback(request={}, session_id="", model="agent", base_url="http://tf/v1") is None
    assert lookup_calls == []
    assert callback(request={}, session_id="session-alpha", model="", base_url="http://tf/v1") is None
    assert callback(request={}, session_id="session\0alpha", model="agent", base_url="http://tf/v1") is None


def test_explicit_header_is_preserved_case_insensitively_while_others_are_added(monkeypatch: Any) -> None:
    config = plugin_config(
        {
            "custom:local-lab": {
                "headers": {
                    "X-Conversation": header_rule(inputs=["session_id"]),
                    "X-Model-Conversation": header_rule(),
                }
            }
        }
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:local-lab", config))
    headers = {"x-CONVERSATION": "caller-value", "X-Trace-ID": "trace-1"}
    request = {"extra_headers": headers}

    result = callback(request=request, session_id="s1", model="m1", base_url="http://lab/v1")

    assert result is not None
    assert result["request"]["extra_headers"]["x-CONVERSATION"] == "caller-value"
    assert "X-Model-Conversation" in result["request"]["extra_headers"]
    assert request["extra_headers"] is headers


def test_all_explicit_headers_return_no_change(monkeypatch: Any) -> None:
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge"))
    headers = {"x-OLLA-session-id": "caller-session"}
    request = {"extra_headers": headers}

    assert callback(request=request, session_id="s1", model="m1", base_url="http://tf/v1") is None
    assert request["extra_headers"] is headers


def test_invalid_existing_extra_headers_fails_closed(monkeypatch: Any) -> None:
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge"))
    request = {"extra_headers": ["invalid"]}

    assert callback(request=request, session_id="s1", model="m1", base_url="http://tf/v1") is None
    assert request == {"extra_headers": ["invalid"]}


def test_value_scope_and_format(monkeypatch: Any) -> None:
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge"))

    def value(session_id: str, model: str) -> str:
        result = callback(request={}, session_id=session_id, model=model, base_url="http://tf/v1")
        assert result is not None
        return result["request"]["extra_headers"]["X-Olla-Session-ID"]

    first = value("session-alpha", "agent")
    assert first == value("session-alpha", "agent")
    assert first != value("session-beta", "agent")
    assert first != value("session-alpha", "coder")
    assert re.fullmatch(r"hermes-[0-9a-f]{32}", first)


def test_namespace_isolates_identical_session_and_model(monkeypatch: Any) -> None:
    first_config = plugin_config(
        {"custom:thunder-forge": {"headers": {"X-Olla-Session-ID": header_rule(namespace="chez")}}}
    )
    second_config = plugin_config(
        {"custom:thunder-forge": {"headers": {"X-Olla-Session-ID": header_rule(namespace="shag")}}}
    )
    first_callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge", first_config))
    second_callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge", second_config))

    first = first_callback(request={}, session_id="shared-id", model="agent", base_url="http://tf/v1")
    second = second_callback(request={}, session_id="shared-id", model="agent", base_url="http://tf/v1")

    assert first is not None and second is not None
    assert first["request"]["extra_headers"] != second["request"]["extra_headers"]


def test_session_only_recipe_is_stable_across_models(monkeypatch: Any) -> None:
    config = plugin_config(
        {"custom:local-lab": {"headers": {"X-Conversation": header_rule(inputs=["session_id"], prefix="session-")}}}
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:local-lab", config))

    first = callback(request={}, session_id="s1", model="m1", base_url="http://lab/v1")
    second = callback(request={}, session_id="s1", model="m2", base_url="http://lab/v1")

    assert first is not None and second is not None
    assert first["request"]["extra_headers"] == second["request"]["extra_headers"]


def test_value_exposes_no_raw_inputs_or_secret_fixtures(monkeypatch: Any) -> None:
    session_id = "private-session-name"
    model = "private-model-alias"
    endpoint = "http://private-endpoint.example/v1"
    secret = "provider-secret-fixture"
    namespace = "private-installation-name"

    config = plugin_config(
        {"custom:thunder-forge": {"headers": {"X-Olla-Session-ID": header_rule(namespace=namespace)}}}
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge", config))

    result = callback(request={}, session_id=session_id, model=model, base_url=endpoint, api_key=secret)

    assert result is not None
    value = result["request"]["extra_headers"]["X-Olla-Session-ID"]
    assert all(raw not in value for raw in (namespace, session_id, model, endpoint, secret))
