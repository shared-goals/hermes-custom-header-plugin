"""Keep pytest collection independent from an installed Hermes runtime."""

from __future__ import annotations

import sys
from types import ModuleType

hermes_cli = ModuleType("hermes_cli")
hermes_cli.__path__ = []  # type: ignore[attr-defined]
runtime_provider = ModuleType("hermes_cli.runtime_provider")
runtime_provider.find_custom_provider_identity = lambda _: None  # type: ignore[attr-defined]
config = ModuleType("hermes_cli.config")
config.load_config = lambda: {}  # type: ignore[attr-defined]

sys.modules.setdefault("hermes_cli", hermes_cli)
sys.modules.setdefault("hermes_cli.runtime_provider", runtime_provider)
sys.modules.setdefault("hermes_cli.config", config)
