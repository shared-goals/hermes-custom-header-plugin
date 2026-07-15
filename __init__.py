"""Hermes middleware for computed custom-provider request headers."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from hermes_cli.config import get_env_value, load_config
from hermes_cli.runtime_provider import find_custom_provider_identity

PLUGIN_NAME = "hermes-custom-header-plugin"
CONFIG_KEY = "providers"
HMAC_KEY_ENV = "HERMES_CUSTOM_HEADER_HMAC_KEY"
MIN_HMAC_KEY_BYTES = 32
IDENTITY_PATTERN = re.compile(r"custom:[a-z0-9][a-z0-9._-]*")
HEADER_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
NAMESPACE_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
ALLOWED_INPUTS = frozenset({"session_id", "model"})
ALLOWED_STRATEGIES = frozenset({"sha256", "hmac-sha256"})
logger = logging.getLogger(__name__)
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
        "keep-alive",
        "proxy-authorization",
        "proxy-connection",
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
    strategy: str
    namespace: str
    inputs: tuple[str, ...]
    prefix: str
    digest_length: int


@dataclass(frozen=True)
class PluginSettings:
    """Validated runtime settings captured when the plugin is registered."""

    provider_rules: dict[str, tuple[HeaderRule, ...]]
    ambiguous_base_urls: frozenset[str]
    hmac_key: bytes | None = field(repr=False)


class ConfigurationError(ValueError):
    """A redaction-safe plugin configuration error."""


def _parse_rule(name: Any, raw_rule: Any) -> HeaderRule | None:
    if not isinstance(name, str) or HEADER_PATTERN.fullmatch(name) is None:
        return None
    if name.casefold() in BLOCKED_HEADERS:
        return None
    if not isinstance(raw_rule, dict) or set(raw_rule) != {
        "strategy",
        "namespace",
        "inputs",
        "prefix",
        "digest_length",
    }:
        return None
    strategy = raw_rule["strategy"]
    if not isinstance(strategy, str) or strategy not in ALLOWED_STRATEGIES:
        return None

    namespace = raw_rule["namespace"]
    if not isinstance(namespace, str) or NAMESPACE_PATTERN.fullmatch(namespace) is None:
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
    if prefix.startswith(" "):
        return None

    digest_length = raw_rule["digest_length"]
    if not isinstance(digest_length, int) or isinstance(digest_length, bool):
        return None
    if not 16 <= digest_length <= 64:
        return None

    return HeaderRule(
        name=name,
        strategy=strategy,
        namespace=namespace,
        inputs=inputs,
        prefix=prefix,
        digest_length=digest_length,
    )


def _normalize_base_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _normalize_provider_name(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "-")


def _provider_url_owners(config: dict[str, Any]) -> dict[str, frozenset[str]]:
    """Return known base URL owners across current and legacy Hermes schemas."""

    owners: dict[str, set[str]] = {}

    def add(identity: str, base_url: Any) -> None:
        normalized_url = _normalize_base_url(base_url)
        if normalized_url:
            owners.setdefault(normalized_url, set()).add(identity)

    providers = config.get("providers")
    if isinstance(providers, dict):
        for name, entry in providers.items():
            if not isinstance(entry, dict):
                continue
            identity = f"custom:{_normalize_provider_name(name)}"
            add(identity, entry.get("api") or entry.get("url") or entry.get("base_url"))

    custom_providers = config.get("custom_providers")
    if isinstance(custom_providers, list):
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue
            identity = f"custom:{_normalize_provider_name(entry.get('name'))}"
            add(identity, entry.get("base_url") or entry.get("url") or entry.get("api"))

    return {base_url: frozenset(identities) for base_url, identities in owners.items()}


def _configured_settings() -> PluginSettings:
    """Load exact provider rules, failing closed with redaction-safe errors."""

    try:
        config = load_config()
    except Exception as exc:
        raise ConfigurationError("Hermes configuration could not be loaded") from exc

    if not isinstance(config, dict):
        raise ConfigurationError("Hermes configuration root must be a mapping")
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        raise ConfigurationError("plugins must be a mapping")
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        raise ConfigurationError("plugins.entries must be a mapping")
    plugin_config = entries.get(PLUGIN_NAME)
    if not isinstance(plugin_config, dict) or CONFIG_KEY not in plugin_config:
        raise ConfigurationError(f"plugins.entries.{PLUGIN_NAME}.{CONFIG_KEY} is missing")
    raw_providers = plugin_config.get(CONFIG_KEY)
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise ConfigurationError(f"plugins.entries.{PLUGIN_NAME}.{CONFIG_KEY} must be a non-empty mapping")

    providers: dict[str, tuple[HeaderRule, ...]] = {}
    needs_hmac_key = False
    for identity, raw_provider in raw_providers.items():
        if not isinstance(identity, str) or IDENTITY_PATTERN.fullmatch(identity) is None:
            raise ConfigurationError("providers contains an invalid custom provider identity")
        if not isinstance(raw_provider, dict) or set(raw_provider) != {"headers"}:
            raise ConfigurationError("providers contains an invalid provider entry")
        raw_headers = raw_provider["headers"]
        if not isinstance(raw_headers, dict) or not raw_headers:
            raise ConfigurationError("providers contains an empty or invalid headers mapping")

        rules: list[HeaderRule] = []
        names: set[str] = set()
        for name, raw_rule in raw_headers.items():
            rule = _parse_rule(name, raw_rule)
            if rule is None or rule.name.casefold() in names:
                raise ConfigurationError("providers contains an invalid or duplicate header rule")
            names.add(rule.name.casefold())
            rules.append(rule)
            needs_hmac_key = needs_hmac_key or rule.strategy == "hmac-sha256"
        providers[identity] = tuple(rules)

    hmac_key: bytes | None = None
    if needs_hmac_key:
        raw_hmac_key = get_env_value(HMAC_KEY_ENV) or ""
        hmac_key = raw_hmac_key.encode()
        if len(hmac_key) < MIN_HMAC_KEY_BYTES:
            raise ConfigurationError(
                f"{HMAC_KEY_ENV} must contain at least {MIN_HMAC_KEY_BYTES} UTF-8 bytes for hmac-sha256 rules"
            )

    owners = _provider_url_owners(config)
    ambiguous_base_urls = frozenset(base_url for base_url, identities in owners.items() if len(identities) > 1)
    return PluginSettings(
        provider_rules=providers,
        ambiguous_base_urls=ambiguous_base_urls,
        hmac_key=hmac_key,
    )


def _render_value(rule: HeaderRule, *, session_id: str, model: str, hmac_key: bytes | None) -> str | None:
    values = {"session_id": session_id, "model": model}
    selected = [rule.namespace, *(values[name] for name in rule.inputs)]
    if any(not isinstance(value, str) or not value or "\0" in value for value in selected):
        return None
    payload = "\0".join(selected).encode()
    if rule.strategy == "hmac-sha256":
        if hmac_key is None:
            return None
        digest = hmac.new(hmac_key, payload, hashlib.sha256).hexdigest()
    else:
        digest = hashlib.sha256(payload).hexdigest()
    return f"{rule.prefix}{digest[: rule.digest_length]}"


def register(ctx: Any) -> None:
    """Register fail-closed computed-header middleware for named providers."""

    try:
        settings = _configured_settings()
    except ConfigurationError as exc:
        logger.warning("%s disabled: %s", PLUGIN_NAME, exc)
        settings = PluginSettings(provider_rules={}, ambiguous_base_urls=frozenset(), hmac_key=None)
    except Exception as exc:  # pragma: no cover - defensive host boundary
        logger.warning("%s disabled: unexpected configuration error (%s)", PLUGIN_NAME, type(exc).__name__)
        settings = PluginSettings(provider_rules={}, ambiguous_base_urls=frozenset(), hmac_key=None)

    if settings.ambiguous_base_urls:
        logger.warning(
            "%s found %d shared custom-provider base URL(s); injection will fail closed for those endpoints",
            PLUGIN_NAME,
            len(settings.ambiguous_base_urls),
        )

    def add_computed_headers(
        *,
        request: dict[str, Any],
        session_id: str = "",
        model: str = "",
        base_url: str = "",
        **_: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(request, dict) or not session_id or not settings.provider_rules:
            return None

        if _normalize_base_url(base_url) in settings.ambiguous_base_urls:
            return None

        try:
            identity = find_custom_provider_identity(base_url)
        except Exception:
            logger.debug("%s could not resolve the custom provider identity", PLUGIN_NAME)
            return None

        rules = settings.provider_rules.get(identity or "")
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
            value = _render_value(rule, session_id=session_id, model=model, hmac_key=settings.hmac_key)
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
