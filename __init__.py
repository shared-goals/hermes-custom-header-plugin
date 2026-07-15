"""Hermes middleware for session-and-model custom-provider headers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from hermes_cli.config import load_config
from hermes_cli.runtime_provider import find_custom_provider_identity

PLUGIN_NAME = "hermes-custom-header-plugin"
CONFIG_KEY = "providers"
MAX_HEADER_VALUE_LENGTH = 512
IDENTITY_PATTERN = re.compile(r"custom:[a-z0-9][a-z0-9._-]*")
HEADER_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
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
class PluginSettings:
    """Validated runtime settings captured when the plugin is registered."""

    provider_headers: dict[str, str]
    ambiguous_base_urls: frozenset[str]


class ConfigurationError(ValueError):
    """A redaction-safe plugin configuration error."""


def _valid_header_name(name: Any) -> bool:
    if not isinstance(name, str) or HEADER_PATTERN.fullmatch(name) is None:
        return False
    if name.casefold() in BLOCKED_HEADERS:
        return False
    return True


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
    """Load exact provider headers, failing closed with redaction-safe errors."""

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

    provider_headers: dict[str, str] = {}
    for identity, raw_provider in raw_providers.items():
        if not isinstance(identity, str) or IDENTITY_PATTERN.fullmatch(identity) is None:
            raise ConfigurationError("providers contains an invalid custom provider identity")
        if not isinstance(raw_provider, dict) or set(raw_provider) != {"header"}:
            raise ConfigurationError("providers contains an invalid provider entry")
        header = raw_provider["header"]
        if not _valid_header_name(header):
            raise ConfigurationError("providers contains an invalid header name")
        provider_headers[identity] = header

    owners = _provider_url_owners(config)
    ambiguous_base_urls = frozenset(base_url for base_url, identities in owners.items() if len(identities) > 1)
    return PluginSettings(
        provider_headers=provider_headers,
        ambiguous_base_urls=ambiguous_base_urls,
    )


def _render_value(*, session_id: Any, model: Any) -> str | None:
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(model, str) or not model:
        return None
    value = f"{session_id}:{model}"
    if len(value) > MAX_HEADER_VALUE_LENGTH:
        return None
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        return None
    return value


def register(ctx: Any) -> None:
    """Register fail-closed session-header middleware for named providers."""

    try:
        settings = _configured_settings()
    except ConfigurationError as exc:
        logger.warning("%s disabled: %s", PLUGIN_NAME, exc)
        settings = PluginSettings(provider_headers={}, ambiguous_base_urls=frozenset())
    except Exception as exc:  # pragma: no cover - defensive host boundary
        logger.warning("%s disabled: unexpected configuration error (%s)", PLUGIN_NAME, type(exc).__name__)
        settings = PluginSettings(provider_headers={}, ambiguous_base_urls=frozenset())

    if settings.ambiguous_base_urls:
        logger.warning(
            "%s found %d shared custom-provider base URL(s); injection will fail closed for those endpoints",
            PLUGIN_NAME,
            len(settings.ambiguous_base_urls),
        )

    def add_session_model_header(
        *,
        request: dict[str, Any],
        session_id: Any = "",
        model: Any = "",
        base_url: str = "",
        **_: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(request, dict) or not settings.provider_headers:
            return None

        value = _render_value(session_id=session_id, model=model)
        if value is None:
            return None

        if _normalize_base_url(base_url) in settings.ambiguous_base_urls:
            return None

        try:
            identity = find_custom_provider_identity(base_url)
        except Exception:
            logger.debug("%s could not resolve the custom provider identity", PLUGIN_NAME)
            return None

        header = settings.provider_headers.get(identity or "")
        if not header:
            return None

        existing_headers = request.get("extra_headers")
        if existing_headers is None:
            headers: dict[str, Any] = {}
        elif isinstance(existing_headers, dict):
            headers = dict(existing_headers)
        else:
            return None

        existing_names = {str(name).casefold() for name in headers}
        if header.casefold() in existing_names:
            return None
        headers[header] = value

        updated_request = dict(request)
        updated_request["extra_headers"] = headers
        return {
            "request": updated_request,
            "source": PLUGIN_NAME,
        }

    ctx.register_middleware("llm_request", add_session_model_header)
