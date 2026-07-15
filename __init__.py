"""Hermes middleware for conversation-scoped Olla affinity."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from hermes_cli.config import load_config
from hermes_cli.runtime_provider import find_custom_provider_identity

HEADER = "X-Olla-Session-ID"
SOURCE = "hermes-olla-sticky-sessions"
CONFIG_KEY = "provider_identities"
IDENTITY_PATTERN = re.compile(r"custom:[a-z0-9][a-z0-9._-]*")


def _configured_provider_identities() -> frozenset[str]:
    """Load the explicit provider allow-list, failing closed on invalid config."""

    try:
        config = load_config()
    except Exception:
        return frozenset()

    if not isinstance(config, dict):
        return frozenset()
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return frozenset()
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        return frozenset()
    plugin_config = entries.get(SOURCE)
    if not isinstance(plugin_config, dict):
        return frozenset()
    raw_identities = plugin_config.get(CONFIG_KEY)
    if not isinstance(raw_identities, list):
        return frozenset()

    identities: set[str] = set()
    for identity in raw_identities:
        if not isinstance(identity, str) or IDENTITY_PATTERN.fullmatch(identity) is None:
            return frozenset()
        identities.add(identity)
    return frozenset(identities)


def register(ctx: Any) -> None:
    """Register the provider-allow-listed LLM request middleware."""

    provider_identities = _configured_provider_identities()

    def add_sticky_session(
        *,
        request: dict[str, Any],
        session_id: str = "",
        model: str = "",
        base_url: str = "",
        **_: Any,
    ) -> dict[str, Any] | None:
        if not session_id or not provider_identities:
            return None

        try:
            identity = find_custom_provider_identity(base_url)
        except Exception:
            return None

        if identity not in provider_identities:
            return None

        headers = dict(request.get("extra_headers") or {})
        if any(str(name).casefold() == HEADER.casefold() for name in headers):
            return None

        digest = hashlib.sha256(f"{session_id}\0{model}".encode()).hexdigest()[:32]
        headers[HEADER] = f"hermes-{digest}"

        updated_request = dict(request)
        updated_request["extra_headers"] = headers
        return {
            "request": updated_request,
            "source": SOURCE,
        }

    ctx.register_middleware("llm_request", add_sticky_session)
