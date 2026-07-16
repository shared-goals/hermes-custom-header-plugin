# Hermes Custom Header Plugin

[![CI](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/ci.yml)
[![Hermes main canary](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/hermes-canary.yml/badge.svg)](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/hermes-canary.yml)

## About

A small [Hermes Agent](https://github.com/NousResearch/hermes-agent)
`llm_request` middleware plugin. For explicitly configured named custom
providers, it adds one request header whose value is:

```text
<session_id>:<model>
```

The header name is configurable.

The receiving gateway defines what the header means. The tested setup uses
`X-Olla-Session-ID` for [Olla](https://github.com/thushan/olla) sticky sessions
through [Thunder Forge](https://github.com/shared-goals/thunder-forge).

## Quick start

### 1. Install the plugin

```bash
hermes plugins install shared-goals/hermes-custom-header-plugin --enable
```

### 2. Configure the provider and header

Keep model selection in Hermes' provider configuration. The plugin configuration
only maps the exact named provider identity to a header name:

```yaml
model:
  provider: custom:thunder-forge
  default: agent
  coding: coder

custom_providers:
  - name: thunder-forge
    base_url: http://gateway.example:40116/v1
    key_env: TF_USER_HERMES
    api_mode: chat_completions
    models:
      - name: agent
        model: Qwen3.6-35B-A3B-mxfp8
      - name: coder
        model: Qwen3-Coder-Next-mxfp8

plugins:
  entries:
    hermes-custom-header-plugin:
      providers:
        custom:thunder-forge:
          header: X-Olla-Session-ID
```

If the named provider already exists, keep it and add only the plugin entry.
Provider credentials remain in `~/.hermes/.env`; this plugin has no environment
variables of its own.

The key under the plugin's `providers` mapping is the
canonical `custom:<name>` identity returned by
`hermes_cli.runtime_provider.find_custom_provider_identity()`. This is
independent of how the provider is selected: `model.provider` may be
`thunder-forge` or `custom:thunder-forge`, but the plugin key is
`custom:thunder-forge` in both cases.

### 3. Restart Hermes

```bash
hermes gateway restart
hermes gateway status
```

Long-running Hermes processes must restart to reload the plugin and
configuration. A new CLI process loads them on startup.

## Model behavior

Hermes passes the effective model to the middleware. The plugin does not select,
alias, or configure models.

For one session, different model aliases produce separate sticky keys:

```text
20260715_210001_a1b2c3:agent
20260715_210001_a1b2c3:coder
```

Switch explicitly with:

```text
/model custom:thunder-forge:agent
/model custom:thunder-forge:coder
```

The same session and model always produce the same value. Changing either one
changes the value.

## Configuration and middleware contract

Each plugin provider entry has exactly one field:

```yaml
custom:provider-name:
  header: X-Session-ID
```

The plugin:

- loads and validates its configuration when Hermes registers it;
- resolves the request URL through
  `hermes_cli.runtime_provider.find_custom_provider_identity()`;
- injects only for an exact configured identity;
- fails closed when different named providers share one normalized URL;
- requires non-empty `session_id` and `model` strings;
- accepts only visible ASCII in the generated value and limits it to 512
  characters;
- copies `request` and `extra_headers` instead of mutating them;
- never overwrites an existing header under any capitalization;
- rejects authentication, framing, and hop-by-hop header names such as
  `Authorization`, `Cookie`, `Host`, `Content-Length`, and `Connection`.

Configuration failures produce redacted warnings. Runtime lookup or validation
failures leave the request unchanged.

Static values belong in the provider's native `extra_headers`. This plugin is
only for the active Hermes session and model.

## Tested Olla recipe

Olla can use `X-Olla-Session-ID` as a sticky-session key. Enable sticky sessions
with `session_header` among its key sources and preserve the header through every
proxy hop.

```text
Hermes Agent -> Thunder Forge edge -> Olla -> inference endpoints
     |                              |
     `- sends session:model         `- owns pinning and repinning
```

Verify with a safe conversation while the chosen endpoint remains healthy:

1. Send a turn with `agent`; expect a sticky miss.
2. Send another turn in the same Hermes session and model; expect a hit.
3. Switch the same session to `coder`; expect a different sticky key and
   a separate miss followed by hits.
4. Start another Hermes session; expect another key.

Useful evidence includes Olla routing logs, `GET /internal/stats/sticky`, and
the `X-Olla-Endpoint`, `X-Olla-Sticky-Session`, and
`X-Olla-Sticky-Key-Source` response headers.

Affinity improves the chance of prompt/KV-cache reuse. It does not guarantee a
cache hit after eviction, restart, TTL expiry, failover, or incompatible prompt
prefixes.

## Compatibility

The plugin is gateway-agnostic. Unknown headers may be ignored or rejected, and
a header does not create affinity unless the receiving component implements it.

- **Olla** is the tested sticky-session recipe.
- **oMLX**, **llama.cpp**, and **Ollama** need an external router that explicitly
  consumes the header.
- **LiteLLM** can forward client headers, but forwarding alone does not make its
  router sticky.

See [docs/compatibility.md](docs/compatibility.md) for details.

## Requirements

- Hermes Agent 0.18.2 or newer with user plugins and `llm_request` middleware.
- A named custom provider whose resolved URL maps to one canonical identity.
- A downstream component that implements the configured header.
- Python 3.11 or newer, matching tested Hermes Agent 0.18.2 installations.

CI runs the real discovery and middleware smoke test against Hermes Agent 0.18.2.
A scheduled canary repeats it against Hermes `main`.

Middleware covers the main conversation request path. Model discovery, probes,
MoA reference clients, auxiliary inference, and authentication paths that bypass
`llm_request` remain outside its scope.

## Updating from 0.3.x

Version 0.4.0 has a breaking configuration and value-format change. Replace each
computed rule:

```yaml
providers:
  custom:thunder-forge:
    headers:
      X-Olla-Session-ID:
        # 0.3.x recipe fields
```

with:

```yaml
providers:
  custom:thunder-forge:
    header: X-Olla-Session-ID
```

Do not change the provider's `model`, `default`, `coding`, or `models` fields.
The new value creates a new Olla sticky key on the first request.

Update and restart:

```bash
hermes plugins update hermes-custom-header-plugin
hermes gateway restart
hermes gateway status
```

Hermes' installer follows the default branch. Record the installed commit and
use release tags as rollback points:

```bash
git -C ~/.hermes/plugins/hermes-custom-header-plugin rev-parse HEAD
git -C ~/.hermes/plugins/hermes-custom-header-plugin tag --sort=-version:refname
```

Version `v0.3.0` preserves the previous recipe-based implementation.

To remove the plugin:

```bash
hermes plugins disable hermes-custom-header-plugin
hermes plugins remove hermes-custom-header-plugin
hermes gateway restart
```

## Development

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run coverage run --branch -m pytest -m 'not integration'
uv run coverage report --fail-under=90
uv pip install 'hermes-agent==0.18.2'
uv run pytest tests/test_hermes_integration.py -m integration
git diff --check
```

## Versioning and security

The project follows [Semantic Versioning](https://semver.org/). Hermes reads the
version from `plugin.yaml`; `pyproject.toml` mirrors it, and tests require both
values and the changelog to agree.

Report vulnerabilities according to [SECURITY.md](SECURITY.md).

## Related documentation

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [Hermes middleware contract](https://github.com/NousResearch/hermes-agent/blob/main/docs/middleware/README.md)
- [Hermes plugin documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugins/index.md)
- [Olla sticky sessions](https://github.com/thushan/olla/blob/main/docs/content/concepts/sticky-sessions.md)
- [Thunder Forge](https://github.com/shared-goals/thunder-forge)

## License

[MIT](LICENSE)
