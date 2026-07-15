# Hermes Custom Header Plugin

[![CI](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/ci.yml)
[![Hermes main canary](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/hermes-canary.yml/badge.svg)](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/hermes-canary.yml)

A small [Hermes Agent](https://github.com/NousResearch/hermes-agent) middleware
plugin that adds deterministic, pseudonymous request headers to explicitly
configured custom LLM providers.

The header name and value recipe are configuration, not provider-specific code.
It works with any endpoint or gateway that assigns documented semantics to the
configured header. [Olla](https://github.com/thushan/olla) sticky sessions
through [Thunder Forge](https://github.com/shared-goals/thunder-forge) are one
tested recipe, not a hard-coded dependency.

## Quick start

Install and enable the plugin:

```bash
hermes plugins install shared-goals/hermes-custom-header-plugin --enable
```

Generate a secret containing at least 32 bytes and store it in
`~/.hermes/.env` as `HERMES_CUSTOM_HEADER_HMAC_KEY`:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Add a named provider and an exact plugin rule to `~/.hermes/config.yaml`:

```yaml
providers:
  thunder-forge:
    api: http://gateway.example:40116/v1
    key_env: TF_USER_HERMES
    transport: chat_completions
    default_model: agent-better

plugins:
  entries:
    hermes-custom-header-plugin:
      providers:
        custom:thunder-forge:
          headers:
            X-Olla-Session-ID:
              strategy: hmac-sha256
              namespace: installation-a
              inputs:
                - session_id
                - model
              prefix: hermes-
              digest_length: 32
```

Restart long-running Hermes processes after configuration changes:

```bash
hermes gateway status
hermes gateway restart
hermes gateway status
```

## Configuration contract

Every provider entry has one or more computed header rules. The provider key
must be the exact canonical `custom:<name>` identity; wildcards and URLs are
rejected. Every named custom provider must also have a unique normalized
`base_url`. If two identities share one URL, the plugin cannot distinguish them
from Hermes' request context and fails closed for that endpoint.

Supported strategies:

- `hmac-sha256` is recommended. It derives an opaque value using
  `HERMES_CUSTOM_HEADER_HMAC_KEY`, a required non-secret namespace, and the
  configured runtime inputs. The key is never emitted or logged.
- `sha256` remains available for legacy routing compatibility. It is an
  unkeyed deterministic pseudonym, not privacy protection; anyone who can guess
  the inputs can verify guesses offline.

For `namespace: installation-a` and `inputs: [session_id, model]`, the payload
is `installation-a + NUL + session_id + NUL + model`. Runtime values containing
NUL are rejected. Input order is significant, `session_id` is required, and
`model` is optional.

`digest_length` is the number of lowercase hexadecimal characters and must be
between 16 and 64. The default examples use 32 characters (128 bits). A prefix
can be empty and may contain printable ASCII, but cannot begin with whitespace;
the complete value is therefore valid HTTP field content.

The namespace is a stable, non-secret installation identifier containing 1-64
ASCII letters, digits, dots, underscores, or hyphens. Use a different namespace
and HMAC key for each independent Hermes installation sharing a downstream
session-key space. Rotating either starts a new affinity namespace.

Configuration is validated as a whole when Hermes registers the plugin. A
missing secret for an HMAC rule, malformed rule, ambiguous provider URL, or
invalid identity disables the affected injection path and emits a redacted
warning. No endpoint, header value, session identifier, model, or secret is
included in plugin diagnostics.

The plugin copies `request` and `extra_headers` instead of mutating them. It
never overwrites a caller header under any capitalization. Authentication,
content-framing, and hop-by-hop headers such as `Authorization`, `Cookie`,
`Host`, `Content-Length`, and `Connection` are rejected.

Static header values belong in Hermes' native provider `extra_headers`. This
plugin is only for values derived from request context.

## Tested Olla recipe

Olla can use `X-Olla-Session-ID` as a sticky-session key. The quick-start rule
produces a stable value for one Hermes conversation and model without exposing
the raw inputs. Olla must have sticky sessions enabled with `session_header`
among its key sources, and every proxy hop must preserve the configured header.

```text
Hermes Agent -> custom provider edge -> Olla -> inference endpoints
     |                                  |
     `- supplies opaque session key     `- owns pinning and repinning
```

Affinity improves the chance of prompt/KV-cache reuse; it does not guarantee a
cache hit after eviction, restart, TTL expiry, failover, or incompatible prompt
prefixes.

Verify with a safe conversation while the selected endpoint remains healthy:

1. Send the first turn and record only a redacted sticky key, endpoint, and
   outcome.
2. Send a second turn in the same Hermes session and model.
3. Confirm the key and endpoint are stable and Olla changes from `miss` to
   `hit`.
4. Start another Hermes session and confirm the key changes.
5. Switch the model alias and confirm the key changes when `model` is an input.
6. Measure inference-cache reuse separately from routing affinity.

Useful evidence includes Olla routing logs, `GET /internal/stats/sticky`, and
the `X-Olla-Endpoint`, `X-Olla-Sticky-Session`, and
`X-Olla-Sticky-Key-Source` response headers. Never publish full session keys,
provider credentials, private endpoints, or the HMAC key.

## Requirements and compatibility

- Hermes Agent 0.18.2 or newer with user plugins and `llm_request` middleware.
- A named custom provider whose resolved URL maps to one unambiguous canonical
  identity.
- A downstream component that explicitly implements the configured header.
- Python 3.11-3.13, matching Hermes Agent 0.18.2's supported range.

CI runs an end-to-end plugin discovery and middleware test against Hermes Agent
0.18.2. A scheduled canary repeats it against Hermes `main`. The plugin depends
on `hermes_cli.config.load_config()` and
`hermes_cli.runtime_provider.find_custom_provider_identity()`; check the canary
before upgrading a production Hermes installation.

Middleware only covers the main conversation request path. Model discovery,
probes, MoA reference clients, auxiliary inference, and provider authentication
that bypass `llm_request` remain outside scope.

See [docs/compatibility.md](docs/compatibility.md) for oMLX, llama.cpp, Ollama,
LiteLLM, and gateway-specific guidance.

## Update and rollback

Before updating from 0.2.x:

- change any `digest_length` below 16;
- add `HERMES_CUSTOM_HEADER_HMAC_KEY` before changing a rule to
  `hmac-sha256`;
- confirm no two named custom providers share a normalized URL.

Existing `sha256` rules with a namespace and digest length of at least 16 remain
compatible. Restart Hermes after updating:

```bash
hermes plugins update hermes-custom-header-plugin
hermes gateway restart
hermes gateway status
```

Hermes' plugin installer clones the default branch and `plugins update` pulls
that branch. Production operators should record the installed commit and use
release tags as rollback points:

```bash
git -C ~/.hermes/plugins/hermes-custom-header-plugin rev-parse HEAD
git -C ~/.hermes/plugins/hermes-custom-header-plugin tag --sort=-version:refname
```

To remove the plugin:

```bash
hermes plugins disable hermes-custom-header-plugin
hermes plugins remove hermes-custom-header-plugin
hermes gateway restart
```

## Development

Fast unit tests use isolated `hermes_cli` stubs. The integration test is skipped
unless Hermes Agent is installed; CI installs the pinned host version in a
dedicated job.

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

The project follows [Semantic Versioning](https://semver.org/). Hermes reads
the version from `plugin.yaml`; `pyproject.toml` mirrors it, and tests require
the values and changelog to agree. Every release is tagged and published on
GitHub after CI succeeds.

Report vulnerabilities according to [SECURITY.md](SECURITY.md). Workflow
actions are pinned to immutable commits, dependency updates are monitored for
both uv and GitHub Actions, and the repository uses GitHub secret scanning and
push protection.

## Related documentation

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [Hermes middleware contract](https://github.com/NousResearch/hermes-agent/blob/main/docs/middleware/README.md)
- [Hermes plugin documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/plugins/index.md)
- [Hermes example plugins](https://github.com/NousResearch/hermes-example-plugins)
- [Olla](https://github.com/thushan/olla)
- [Olla sticky sessions](https://github.com/thushan/olla/blob/main/docs/content/concepts/sticky-sessions.md)
- [Thunder Forge](https://github.com/shared-goals/thunder-forge)

## License

[MIT](LICENSE)
