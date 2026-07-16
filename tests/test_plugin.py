from __future__ import annotations

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
PLUGIN_NAME = "hermes-custom-header-plugin"


def provider_header(name: str = "X-Olla-Session-ID") -> dict[str, str]:
    return {"header": name}


def plugin_config(
    providers: dict[str, dict[str, str]] | None = None,
    *,
    runtime_providers: dict[str, dict[str, Any]] | None = None,
    legacy_runtime_providers: list[dict[str, Any]] | None = None,
    entry_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configured = providers if providers is not None else {"custom:thunder-forge": provider_header()}
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
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert manifest == {
        "manifest_version": 1,
        "name": PLUGIN_NAME,
        "version": "0.4.1",
        "description": "Add session-and-model request headers to explicitly configured custom providers",
    }
    assert manifest["version"] == project["project"]["version"]
    assert project["project"]["name"] == PLUGIN_NAME
    assert f"## [{manifest['version']}]" in (ROOT / "CHANGELOG.md").read_text()
    assert "requires_env" not in manifest


def test_ci_covers_every_declared_python_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    classifier_prefix = "Programming Language :: Python :: "
    declared_versions = set()
    for classifier in project["project"]["classifiers"]:
        version = classifier.removeprefix(classifier_prefix)
        if classifier.startswith(classifier_prefix) and re.fullmatch(r"\d+\.\d+", version):
            declared_versions.add(version)
    tested_versions = set(workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"])

    assert declared_versions <= tested_versions


def test_readme_documents_the_canonical_plugin_provider_identity() -> None:
    readme = " ".join((ROOT / "README.md").read_text().split())

    assert "canonical `custom:<name>` identity" in readme
    assert "`model.provider` may be `thunder-forge` or `custom:thunder-forge`" in readme
    assert "the plugin key is `custom:thunder-forge` in both cases" in readme
    assert "must exactly match Hermes' `model.provider` value" not in readme


def test_matching_provider_injects_session_and_model_without_mutation(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []
    module = load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge")
    callback = registered_callback(module)
    request = {"messages": [], "extra_headers": {"X-Trace-ID": "trace-1"}}
    original_headers = request["extra_headers"]
    original_snapshot = {"messages": [], "extra_headers": {"X-Trace-ID": "trace-1"}}

    result = callback(
        request=request,
        session_id="20260715_210001_a1b2c3",
        model="agent-better",
        base_url="http://thunder-forge.example/v1",
    )

    assert result is not None
    assert result["source"] == PLUGIN_NAME
    assert result["request"]["extra_headers"] == {
        "X-Trace-ID": "trace-1",
        "X-Olla-Session-ID": "20260715_210001_a1b2c3:agent-better",
    }
    assert result["request"] is not request
    assert result["request"]["extra_headers"] is not original_headers
    assert request == original_snapshot
    assert lookup_calls == ["http://thunder-forge.example/v1"]


def test_accepts_hermes_managed_plugin_entry_fields(monkeypatch: Any) -> None:
    config = plugin_config(entry_metadata={"allow_tool_override": False, "llm": {"enabled": False}, "future": True})
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge", config))

    result = callback(request={}, session_id="s1", model="agent-better", base_url="http://tf/v1")

    assert result is not None
    assert result["request"]["extra_headers"] == {"X-Olla-Session-ID": "s1:agent-better"}


def test_header_name_is_configurable(monkeypatch: Any) -> None:
    config = plugin_config({"custom:local-lab": provider_header("X-Local-Affinity")})
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:local-lab", config))

    result = callback(request={}, session_id="session-alpha", model="agent", base_url="http://lab/v1")

    assert result is not None
    assert result["request"]["extra_headers"] == {"X-Local-Affinity": "session-alpha:agent"}


def test_different_explicit_provider_uses_its_own_header(monkeypatch: Any) -> None:
    config = plugin_config(
        {
            "custom:thunder-forge": provider_header(),
            "custom:local-lab": provider_header("X-Local-Affinity"),
        }
    )
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:local-lab", config))

    result = callback(request={}, session_id="s1", model="m1", base_url="http://lab/v1")

    assert result is not None
    assert result["request"]["extra_headers"] == {"X-Local-Affinity": "s1:m1"}


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

    result = callback(request={}, session_id="s1", model="agent-better", base_url=shared_url)

    assert result is not None
    assert result["request"]["extra_headers"] == {"X-Olla-Session-ID": "s1:agent-better"}


def test_missing_or_empty_configuration_fails_closed_without_lookup(monkeypatch: Any) -> None:
    for config in ({}, plugin_config({})):
        lookup_calls: list[str] = []
        callback = registered_callback(
            load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge", config)
        )

        assert callback(request={}, session_id="session-alpha", model="agent", base_url="http://tf/v1") is None
        assert lookup_calls == []


def test_malformed_provider_configuration_fails_closed(monkeypatch: Any) -> None:
    invalid_provider_configs: list[dict[str, Any]] = [
        {"*": provider_header()},
        {"custom:provider": {}},
        {"custom:provider": {"header": "X-Test", "extra": True}},
        {"custom:provider": {"header": 7}},
        {"custom:provider": provider_header("")},
        {"custom:provider": provider_header("Authorization")},
        {"custom:provider": provider_header("Keep-Alive")},
        {"custom:provider": provider_header("Proxy-Connection")},
        {"custom:provider": provider_header("Bad Header")},
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


def test_invalid_runtime_values_fail_closed_before_lookup(monkeypatch: Any) -> None:
    lookup_calls: list[str] = []
    callback = registered_callback(
        load_plugin(monkeypatch, lambda base_url: lookup_calls.append(base_url) or "custom:thunder-forge")
    )
    invalid_values = [
        ("", "agent-better"),
        (None, "agent-better"),
        (7, "agent-better"),
        ("session", ""),
        ("session", None),
        ("session\nforged", "agent-better"),
        ("session", "coder\rbeter"),
        ("session with space", "agent-better"),
        ("session", "агент"),
        ("s" * 511, "m"),
    ]

    for session_id, model in invalid_values:
        assert callback(request={}, session_id=session_id, model=model, base_url="http://tf/v1") is None
    assert lookup_calls == []


def test_maximum_header_value_length_is_accepted(monkeypatch: Any) -> None:
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge"))

    result = callback(request={}, session_id="s" * 510, model="m", base_url="http://tf/v1")

    assert result is not None
    assert len(result["request"]["extra_headers"]["X-Olla-Session-ID"]) == 512


def test_explicit_header_is_preserved_case_insensitively(monkeypatch: Any) -> None:
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge"))
    headers = {"x-OLLA-session-id": "caller-session", "X-Trace-ID": "trace-1"}
    request = {"extra_headers": headers}

    assert callback(request=request, session_id="s1", model="m1", base_url="http://tf/v1") is None
    assert request["extra_headers"] is headers


def test_invalid_request_or_extra_headers_fails_closed(monkeypatch: Any) -> None:
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge"))

    assert callback(request=[], session_id="s1", model="m1", base_url="http://tf/v1") is None
    request = {"extra_headers": ["invalid"]}
    assert callback(request=request, session_id="s1", model="m1", base_url="http://tf/v1") is None
    assert request == {"extra_headers": ["invalid"]}


def test_value_is_scoped_by_session_and_model(monkeypatch: Any) -> None:
    callback = registered_callback(load_plugin(monkeypatch, lambda _: "custom:thunder-forge"))

    def value(session_id: str, model: str) -> str:
        result = callback(request={}, session_id=session_id, model=model, base_url="http://tf/v1")
        assert result is not None
        return result["request"]["extra_headers"]["X-Olla-Session-ID"]

    session_id = "20260715_210001_a1b2c3"
    agent = value(session_id, "agent-better")
    coder = value(session_id, "coder-better")

    assert agent == f"{session_id}:agent-better"
    assert coder == f"{session_id}:coder-better"
    assert agent == value(session_id, "agent-better")
    assert agent != coder
    assert agent != value("20260715_210002_d4e5f6", "agent-better")
    assert re.fullmatch(r"[0-9]{8}_[0-9]{6}_[0-9a-f]{6}:agent-better", agent)
