"""Compatibility smoke test against an installed Hermes Agent runtime."""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "hermes-custom-header-plugin"

pytestmark = pytest.mark.integration


def test_real_hermes_plugin_discovery_and_middleware(tmp_path: Path) -> None:
    try:
        hermes_version = importlib.metadata.version("hermes-agent")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("hermes-agent is not installed")

    hermes_home = tmp_path / "hermes-home"
    plugin_dir = hermes_home / "plugins" / PLUGIN_NAME
    plugin_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "__init__.py", plugin_dir / "__init__.py")
    shutil.copy2(ROOT / "plugin.yaml", plugin_dir / "plugin.yaml")

    (hermes_home / "config.yaml").write_text(
        """\
custom_providers:
  - name: thunder-forge
    base_url: http://gateway.example:40116/v1
    key_env: TF_USER_HERMES
    api_mode: chat_completions
    models:
      - name: agent-better
        model: agent-better
      - name: coder-better
        model: coder-better
plugins:
  enabled:
    - hermes-custom-header-plugin
  disabled: []
  entries:
    hermes-custom-header-plugin:
      providers:
        custom:thunder-forge:
          header: X-Olla-Session-ID
""",
        encoding="utf-8",
    )

    script = """\
from hermes_cli.middleware import apply_llm_request_middleware
from hermes_cli.plugins import discover_plugins, get_plugin_manager

discover_plugins(force=True)
manager = get_plugin_manager()
loaded = manager._plugins.get("hermes-custom-header-plugin")
assert loaded is not None, manager._plugins
assert loaded.error is None, loaded.error
assert loaded.middleware_registered == ["llm_request"]

result = apply_llm_request_middleware(
    {"messages": []},
    session_id="20260715_165700_abcdef",
    model="agent-better",
    base_url="http://gateway.example:40116/v1",
)
assert result.changed is True
assert result.trace[-1]["source"] == "hermes-custom-header-plugin"
value = result.payload["extra_headers"]["X-Olla-Session-ID"]
assert value == "20260715_165700_abcdef:agent-better"
"""
    env = os.environ.copy()
    env.update(
        {
            "HERMES_HOME": str(hermes_home),
        }
    )
    env.pop("HERMES_SAFE_MODE", None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"Hermes {hermes_version} integration failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
