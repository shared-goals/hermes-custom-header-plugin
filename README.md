# Hermes Custom Header Plugin

[![CI](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/shared-goals/hermes-custom-header-plugin/actions/workflows/ci.yml)

A small [Hermes Agent](https://github.com/NousResearch/hermes-agent) middleware
plugin that adds privacy-preserving, computed request headers to explicitly
configured custom LLM providers.

The header name and value recipe are configuration, not provider-specific code.
It can be used with any inference provider or topology whose HTTP endpoint or
fronting gateway assigns meaning to the configured header.
[Olla](https://github.com/thushan/olla) sticky sessions through
[Thunder Forge](https://github.com/shared-goals/thunder-forge) are one tested
sample, not a hard-coded dependency or required architecture.

Version 0.1.0 is deliberately narrow and fail-closed:

- only exact canonical `custom:<name>` provider identities can be configured;
- no provider is enabled by default, and wildcards and URLs are rejected;
- every header value is a SHA-256 digest of `session_id` and optional `model`;
- the header name, input scope, prefix, and digest length are configurable;
- raw templates, arbitrary Python, environment values, and API keys are not
  supported;
- existing caller headers are preserved case-insensitively;
- authentication and HTTP transport headers are rejected.

## Configuration contract

Each provider has one or more computed header rules:

```yaml
plugins:
  entries:
    hermes-custom-header-plugin:
      providers:
        custom:local-inference:
          headers:
            X-Conversation-Affinity:
              strategy: sha256
              inputs:
                - session_id
              prefix: conversation-
              digest_length: 32
```

For `inputs: [session_id, model]`, the digest input is
`session_id + NUL + model`. Input order is significant. `session_id` is required
in every rule; `model` is optional. `digest_length` must be between 8 and 64.
The prefix must contain only printable ASCII and can be empty.

Configuration is validated as a whole when Hermes registers the plugin. A
missing or malformed plugin entry, provider identity, header name, or value rule
disables all injection. Restart the consuming Hermes process after changing the
configuration.

The plugin copies `request` and `extra_headers` rather than mutating them. It
never overwrites a configured header already present under any capitalization.
It also refuses standard authentication and transport headers including
`Authorization`, `Cookie`, `Host`, `Content-Length`, and `Connection`.

Static header values belong in Hermes' native `model.extra_headers` or
`custom_providers[].extra_headers`. This plugin is for values derived from
request context.

## Tested sample: Olla through Thunder Forge

Olla can use an explicit session key such as `X-Olla-Session-ID`. The following
configuration was tested with the `custom:thunder-forge` sample provider:

```yaml
plugins:
  entries:
    hermes-custom-header-plugin:
      providers:
        custom:thunder-forge:
          headers:
            X-Olla-Session-ID:
              strategy: sha256
              inputs:
                - session_id
                - model
              prefix: hermes-
              digest_length: 32
```

For a given Hermes conversation and effective model alias, this produces:

```text
hermes-<first 32 lowercase hex characters of sha256(session_id + NUL + model)>
```

Replace `custom:thunder-forge` with the exact identity of your own named custom
provider when using another Olla deployment. Thunder Forge is simply a public
example of an edge and inference-cluster setup that preserves the header.

The responsibilities remain separate:

```text
Hermes Agent -> custom provider edge -> Olla -> inference endpoints
     |                                  |
     `- supplies opaque session key     `- owns pinning and repinning
```

Olla must have sticky sessions enabled with `session_header` among its key
sources, and every proxy hop must preserve `X-Olla-Session-ID`. Endpoint
affinity improves the chance of prompt/KV-cache reuse; it does not guarantee a
cache hit after eviction, restart, TTL expiry, failover, or incompatible prompt
prefixes.

## Requirements and compatibility

- Hermes Agent with user plugins and `llm_request` middleware support.
- A named custom provider whose resolved `base_url` maps to a canonical
  `custom:<name>` identity.
- Any downstream service that understands the configured header.

The plugin was verified against Hermes Agent 0.18.2 and the relevant Hermes
`main` contracts on 2026-07-15. It depends on these runtime contracts:

- a plugin exports `register(ctx)` beside `plugin.yaml`;
- `ctx.register_middleware("llm_request", callback)` registers middleware;
- the callback receives `request`, `session_id`, `model`, and `base_url`;
- middleware may return `{"request": updated, "source": ...}`;
- `hermes_cli.config.load_config()` exposes
  `plugins.entries.hermes-custom-header-plugin.providers`;
- `hermes_cli.runtime_provider.find_custom_provider_identity(base_url)` maps
  the resolved endpoint to a canonical provider identity.

`find_custom_provider_identity` is a Hermes runtime API, not a generic plugin
context field. Re-check the contract before upgrading Hermes. Independent
clients that bypass the main `llm_request` middleware are outside scope,
including model discovery/probes, MoA reference clients, auxiliary inference,
and provider authentication setup.

This scope matters when triaging Hermes issues:

- static headers should use native `extra_headers`;
- computed headers on the main conversation request fit this plugin;
- headers required by every Hermes HTTP path still require an upstream Hermes
  fix.

## Install

Review the repository, then install and enable it:

```bash
hermes plugins install shared-goals/hermes-custom-header-plugin --enable
hermes plugins list
```

Merge explicit provider rules into `~/.hermes/config.yaml`, then start a new
CLI session or restart a running gateway after checking its state:

```bash
hermes gateway status
hermes gateway restart
hermes gateway status
```

The plugin needs no secret or endpoint variable. Keep provider credentials in
the environment variable referenced by the custom-provider entry, for example:

```yaml
custom_providers:
  - name: thunder-forge
    base_url: http://gateway.example:40116/v1
    key_env: TF_USER_HERMES
    api_mode: chat_completions
    models:
      - name: coder-better
      - name: agent-better
      - name: memory
```

Keep the real value in `~/.hermes/.env`; do not put it in `config.yaml`, plugin
configuration, logs, or this repository.

Select a provider explicitly when it is not the default:

```bash
hermes --provider custom:thunder-forge -m agent-better -z 'Reply exactly: ok'
```

Inside an existing session:

```text
/model custom:thunder-forge:coder-better
```

## Verify the Olla recipe

Use a safe conversation while the selected endpoint remains healthy and within
Olla's sticky-session idle TTL:

1. Send the first turn and record only a redacted sticky key, selected endpoint,
   and sticky outcome.
2. Send a second turn with the same Hermes session and model.
3. Confirm the key and endpoint remain stable and Olla changes from `miss` to
   `hit`.
4. Start another Hermes session and confirm the key changes.
5. Switch the effective model alias and confirm the key changes.
6. Measure inference-cache reuse separately from routing affinity.

Useful evidence includes Thunder Forge access JSONL, Olla routing logs,
`GET /internal/stats/sticky`, and the `X-Olla-Endpoint`,
`X-Olla-Sticky-Session`, and `X-Olla-Sticky-Key-Source` response headers. Do not
publish full session keys, provider credentials, or private endpoints.

## Other local inference setups

The following are compatibility notes and starting points, not verified recipes.
They were researched from current upstream documentation on 2026-07-15, but
were not exercised against a live oMLX, llama.cpp, Ollama, or LiteLLM deployment
for this release. Confirm the exact header name and behavior at your own gateway
before enabling a rule.

The common configuration shape for a gateway in front of any local runtime is:

```yaml
plugins:
  entries:
    hermes-custom-header-plugin:
      providers:
        custom:my-local-inference:
          headers:
            X-Session-ID:  # replace with the header your gateway consumes
              strategy: sha256
              inputs:
                - session_id
                - model
              prefix: hermes-
              digest_length: 32
```

- **[oMLX](https://github.com/jundot/omlx)** has an OpenAI-compatible API and
  an upstream Hermes integration. Direct single-server oMLX does not need
  backend affinity, and the reviewed upstream source does not document a
  session-affinity request header. Use the plugin only when a proxy or router
  in front of one or more oMLX servers explicitly consumes your chosen header.
- **[llama.cpp `llama-server`](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)**
  exposes OpenAI-compatible endpoints, parallel slots, prompt-similarity reuse,
  and a multi-model router. Its current server documentation does not define a
  custom request header for session-to-slot or backend affinity. A header recipe
  therefore belongs to an external load balancer or gateway contract, not to
  `llama-server` itself.
- **[Ollama](https://github.com/ollama/ollama)** exposes OpenAI-compatible chat
  and responses endpoints, documented in its
  [OpenAI compatibility guide](https://docs.ollama.com/api/openai-compatibility).
  The reviewed API docs do not define header-based session affinity; Ollama's
  `keep_alive` is a request-body option for model residency and is outside this
  header-only plugin. Use a recipe only if an external Ollama gateway documents
  the header it consumes.
- **[LiteLLM](https://github.com/BerriAI/litellm)** provides an
  OpenAI-compatible proxy and router for many inference providers. Current
  upstream code can forward client `x-*` headers to backend model calls when
  `forward_client_headers_to_llm_api` is explicitly enabled, but the reviewed
  documentation does not define a built-in session-affinity header. Use this
  plugin to supply a value for a downstream router or custom LiteLLM hook that
  documents the header; do not assume the stock LiteLLM router will become
  sticky merely because it receives `X-Session-ID`. Header forwarding is broad,
  so review which client headers may reach upstream providers before enabling
  it.

Sending an unknown header directly to a runtime may be harmless, rejected, or
ignored, but it does not create affinity by itself. The downstream component
must document and implement the semantics.

## Update and rollback

```bash
hermes plugins update hermes-custom-header-plugin
hermes gateway restart
hermes gateway status
```

To remove it completely:

```bash
hermes plugins disable hermes-custom-header-plugin
hermes plugins remove hermes-custom-header-plugin
hermes gateway restart
hermes gateway status
```

No provider, router, or inference-runtime rollback is required because the
plugin changes only the main LLM request's headers.

## Development

Hermes Agent is deliberately not a test dependency. The
[pytest](https://github.com/pytest-dev/pytest) harness installs small fake
`hermes_cli` modules and loads the plugin against isolated provider lookup
stubs. Development dependencies are managed with
[uv](https://github.com/astral-sh/uv), and linting/format checks use
[Ruff](https://github.com/astral-sh/ruff).

```bash
uv sync --frozen
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

## Versioning

This project follows [Semantic Versioning](https://semver.org/). Hermes reads
the version from `plugin.yaml`; `pyproject.toml` mirrors it, and tests require
both values to match.

For a release, update both version fields, move pending changelog entries under
the dated version, run all validation, create a reviewed `v<version>` tag, and
publish the GitHub release. Do not tag an unreviewed or untested diff.

## Related projects and documentation

- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) —
  the agent and plugin runtime.
- [Hermes plugin documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/plugins.md)
  — discovery, installation, enablement, and lifecycle.
- [thushan/olla](https://github.com/thushan/olla) — the router used by the
  documented sticky-session recipe.
- [Olla sticky-session documentation](https://github.com/thushan/olla/blob/main/docs/content/concepts/sticky-sessions.md)
  — session headers, key sources, and observability.
- [shared-goals/thunder-forge](https://github.com/shared-goals/thunder-forge) —
  one tested Olla edge and inference-cluster sample.
- [jundot/omlx](https://github.com/jundot/omlx) — an OpenAI-compatible local
  inference server with an upstream Hermes integration.
- [ggml-org/llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
  — OpenAI-compatible local inference and server/router behavior.
- [ollama/ollama](https://github.com/ollama/ollama) and its
  [OpenAI compatibility guide](https://docs.ollama.com/api/openai-compatibility)
  — supported OpenAI-compatible endpoints and fields.
- [BerriAI/litellm](https://github.com/BerriAI/litellm) and its
  [official documentation](https://docs.litellm.ai/) — OpenAI-compatible proxy,
  provider routing, and optional client-header forwarding.
- [astral-sh/uv](https://github.com/astral-sh/uv),
  [pytest-dev/pytest](https://github.com/pytest-dev/pytest), and
  [astral-sh/ruff](https://github.com/astral-sh/ruff) — development validation
  tools used by this repository.
- [NousResearch/hermes-example-plugins](https://github.com/NousResearch/hermes-example-plugins)
  — standalone Hermes plugin examples.

## License

[MIT](LICENSE)
