"""Hermes middleware for computed custom-provider request headers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from hermes_cli.config import load_config
from hermes_cli.runtime_provider import find_custom_provider_identity

PLUGIN_NAME = "hermes-custom-header-plugin"
CONFIG_KEY = "providers"
IDENTITY_PATTERN = re.compile(r"custom:[a-z0-9][a-z0-9._-]*")
HEADER_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
ALLOWED_INPUTS = frozenset({"session_id", "model"})
BLOCKED_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "authorization",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "proxy-authorization",
        "set-cookie",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
    }
)


@dataclass(frozen=True)
class HeaderRule:
    """Validated rule for one computed request header."""

    name: str
    inputs: tuple[str, ...]
    prefix: str
    digest_length: int


def _parse_rule(name: Any, raw_rule: Any) -> HeaderRule | None:
    if not isinstance(name, str) or HEADER_PATTERN.fullmatch(name) is None:
        return None
    if name.casefold() in BLOCKED_HEADERS:
        return None
    if not isinstance(raw_rule, dict) or set(raw_rule) != {
        "strategy",
        "inputs",
        "prefix",
        "digest_length",
    }:
        return None
    if raw_rule["strategy"] != "sha256":
        return None

    raw_inputs = raw_rule["inputs"]
    if not isinstance(raw_inputs, list) or not raw_inputs:
        return None
    if any(not isinstance(value, str) or value not in ALLOWED_INPUTS for value in raw_inputs):
        return None
    inputs = tuple(raw_inputs)
    if len(set(inputs)) != len(inputs) or "session_id" not in inputs:
        return None

    prefix = raw_rule["prefix"]
    if not isinstance(prefix, str) or len(prefix) > 128:
        return None
    if any(ord(character) < 32 or ord(character) > 126 for character in prefix):
        return None

    digest_length = raw_rule["digest_length"]
    if not isinstance(digest_length, int) or isinstance(digest_length, bool):
        return None
    if not 8 <= digest_length <= 64:
        return None

    return HeaderRule(
        name=name,
        inputs=inputs,
        prefix=prefix,
        digest_length=digest_length,
    )


def _configured_provider_rules() -> dict[str, tuple[HeaderRule, ...]]:
    """Load exact provider rules, failing closed on any invalid config."""

    try:
        config = load_config()
    except Exception:
        return {}

    if not isinstance(config, dict):
        return {}
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        return {}
    plugin_config = entries.get(PLUGIN_NAME)
    if not isinstance(plugin_config, dict) or CONFIG_KEY not in plugin_config:
        return {}
    if not set(plugin_config) <= {CONFIG_KEY, "allow_tool_override"}:
        return {}
    if "allow_tool_override" in plugin_config and not isinstance(plugin_config["allow_tool_override"], bool):
        return {}
    raw_providers = plugin_config.get(CONFIG_KEY)
    if not isinstance(raw_providers, dict) or not raw_providers:
        return {}

    providers: dict[str, tuple[HeaderRule, ...]] = {}
    for identity, raw_provider in raw_providers.items():
        if not isinstance(identity, str) or IDENTITY_PATTERN.fullmatch(identity) is None:
            return {}
        if not isinstance(raw_provider, dict) or set(raw_provider) != {"headers"}:
            return {}
        raw_headers = raw_provider["headers"]
        if not isinstance(raw_headers, dict) or not raw_headers:
            return {}

        rules: list[HeaderRule] = []
        names: set[str] = set()
        for name, raw_rule in raw_headers.items():
            rule = _parse_rule(name, raw_rule)
            if rule is None or rule.name.casefold() in names:
                return {}
            names.add(rule.name.casefold())
            rules.append(rule)
        providers[identity] = tuple(rules)

    return providers


def _render_value(rule: HeaderRule, *, session_id: str, model: str) -> str | None:
    values = {"session_id": session_id, "model": model}
    selected = [values[name] for name in rule.inputs]
    if any(not isinstance(value, str) or not value for value in selected):
        return None
    digest = hashlib.sha256("\0".join(selected).encode()).hexdigest()[: rule.digest_length]
    return f"{rule.prefix}{digest}"


def register(ctx: Any) -> None:
    """Register fail-closed computed-header middleware for named providers."""

    provider_rules = _configured_provider_rules()

    def add_computed_headers(
        *,
        request: dict[str, Any],
        session_id: str = "",
        model: str = "",
        base_url: str = "",
        **_: Any,
    ) -> dict[str, Any] | None:
        if not session_id or not provider_rules:
            return None

        try:
            identity = find_custom_provider_identity(base_url)
        except Exception:
            return None

        rules = provider_rules.get(identity or "")
        if not rules:
            return None

        existing_headers = request.get("extra_headers")
        if existing_headers is None:
            headers: dict[str, Any] = {}
        elif isinstance(existing_headers, dict):
            headers = dict(existing_headers)
        else:
            return None

        rendered: list[tuple[str, str]] = []
        for rule in rules:
            value = _render_value(rule, session_id=session_id, model=model)
            if value is None:
                return None
            rendered.append((rule.name, value))

        existing_names = {str(name).casefold() for name in headers}
        added = False
        for name, value in rendered:
            if name.casefold() in existing_names:
                continue
            headers[name] = value
            added = True

        if not added:
            return None

        updated_request = dict(request)
        updated_request["extra_headers"] = headers
        return {
            "request": updated_request,
            "source": PLUGIN_NAME,
        }

    ctx.register_middleware("llm_request", add_computed_headers)
