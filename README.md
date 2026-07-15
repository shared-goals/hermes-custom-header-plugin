# Hermes Agent ↔ Olla Sticky Sessions

[![CI](https://github.com/shared-goals/hermes-olla-sticky-sessions/actions/workflows/ci.yml/badge.svg)](https://github.com/shared-goals/hermes-olla-sticky-sessions/actions/workflows/ci.yml)

A small [Hermes Agent](https://github.com/NousResearch/hermes-agent) client
integration that supplies an explicit, privacy-preserving conversation key for
[Olla](https://github.com/thushan/olla) sticky sessions in custom LLM inference
stacks.

Version 0.1.0 is validated with the
[Thunder Forge](https://github.com/shared-goals/thunder-forge) reference stack
and [oMLX](https://github.com/jundot/omlx) inference endpoints. Its provider
selector activates only for canonical `custom:<name>` identities explicitly
allowed in Hermes configuration. No provider identity is enabled by default.

The plugin adds `X-Olla-Session-ID` to each eligible main-conversation LLM
request. Olla can then keep follow-up turns on the same healthy inference
endpoint, improving the opportunity for prompt/KV-cache reuse without adding a
second routing layer.

## What it does

For the same Hermes session and effective model alias, the plugin derives:

```text
hermes-<first 32 lowercase hex characters of sha256(session_id + NUL + model)>
```

It injects that value only when Hermes resolves the request's existing
`base_url` to a provider identity in the plugin's explicit allow-list.

The plugin:

- uses the effective `base_url` already supplied by Hermes;
- introduces no duplicate endpoint setting such as `THUNDER_FORGE_BASE_URL`;
- fails closed when configuration is missing or malformed, Hermes cannot
  identify the provider, or the identity is not explicitly allowed;
- accepts no wildcard, URL, or implicit all-provider configuration;
- preserves an explicit `X-Olla-Session-ID` case-insensitively;
- preserves unrelated `extra_headers`;
- copies the request and headers instead of mutating the originals;
- exposes no raw session name, model name, endpoint, API key, or user identity.

A static provider header is not a substitute. One account-wide session value
would pin unrelated conversations together, reducing both cluster balance and
useful cache locality.

## Responsibilities

This is client-side integration glue:

```text
Hermes Agent → Thunder Forge edge → Olla → inference endpoints
     │                               │
     └─ supplies conversation key    └─ owns pinning, health and repinning
```

- Hermes supplies the missing conversation identity.
- Thunder Forge preserves the caller's header and owns authentication and
  request attribution.
- Olla owns endpoint selection, sticky-session state, health checks, retries,
  and repinning.
- The inference runtime owns model loading and cache behavior.

Endpoint affinity improves the chance of cache reuse; it cannot guarantee a
cache hit after cold loads, eviction, restarts, TTL expiry, failover, or
incompatible prompt prefixes.

## Requirements

- Hermes Agent with user plugins and `llm_request` middleware support.
- At least one named Hermes custom provider with a canonical `custom:<name>`
  identity explicitly allowed in the plugin configuration.
- Olla sticky sessions enabled with `session_header` among its key sources.
- A proxy path, such as Thunder Forge edge, that preserves
  `X-Olla-Session-ID`.

The plugin was verified against Hermes Agent 0.18.2 and the relevant Hermes
`main` contracts on 2026-07-15. See [Compatibility](#compatibility) before
upgrading Hermes.

## Install

Review the repository, then install and enable it with Hermes:

```bash
hermes plugins install shared-goals/hermes-olla-sticky-sessions --enable
hermes plugins list
```

Merge an explicit provider allow-list into `~/.hermes/config.yaml`. This
example enables the validated Thunder Forge reference stack:

```yaml
plugins:
  entries:
    hermes-olla-sticky-sessions:
      provider_identities:
        - custom:thunder-forge
```

`provider_identities` must be a non-empty YAML list of canonical
`custom:<name>` identities. Any missing or malformed value disables header
injection for the whole plugin. Providers sharing one `base_url` are not
supported because Hermes' reverse lookup cannot distinguish them reliably.

Hermes loads plugins when the process starts. Start a new CLI session, or
restart a gateway only after checking its current status:

```bash
hermes gateway status
hermes gateway restart
hermes gateway status
```

No plugin-specific API key or endpoint variable is required. Keep the
Thunder Forge credential in the environment variable referenced by the named
provider entry, for example:

```yaml
custom_providers:
  - name: thunder-forge
    base_url: http://gateway.example:40116/v1
    key_env: TF_USER_HERMES
    api_mode: chat_completions
    models:
      agent: {}
      coder: {}
```

Keep the real key in `~/.hermes/.env`; do not put it in `config.yaml` or this
repository.

Select the provider explicitly when Thunder Forge is not the default:

```bash
hermes --provider custom:thunder-forge -m agent -z 'Reply exactly: ok'
```

Inside an existing Hermes session:

```text
/model custom:thunder-forge:coder
```

## Verify routing

Use a safe test conversation while the selected endpoint remains healthy and
within Olla's sticky-session idle TTL:

1. Send the first turn and record the redacted sticky key, selected endpoint,
   and sticky outcome.
2. Send a second turn in the same Hermes session and model.
3. Confirm the key and endpoint remain the same and Olla changes from `miss`
   to `hit`.
4. Start a new Hermes session and confirm the key changes.
5. Switch the effective model alias and confirm the key changes.
6. Measure inference-cache reuse separately from routing affinity.

Useful evidence includes:

- Thunder Forge edge access JSONL;
- Olla routing logs;
- `GET /internal/stats/sticky`;
- `X-Olla-Endpoint`, `X-Olla-Sticky-Session`, and
  `X-Olla-Sticky-Key-Source` response headers.

## Update and rollback

Update the source-controlled plugin, then restart the consuming Hermes process:

```bash
hermes plugins update hermes-olla-sticky-sessions
hermes gateway restart
hermes gateway status
```

To roll back the integration completely:

```bash
hermes plugins disable hermes-olla-sticky-sessions
hermes plugins remove hermes-olla-sticky-sessions
hermes gateway restart
hermes gateway status
```

No Thunder Forge, Olla, or inference-runtime rollback is required because this
plugin changes only the client request headers.

## Compatibility

The current implementation depends on these Hermes runtime contracts:

- user plugins expose `register(ctx)` from a directory containing
  `plugin.yaml` and `__init__.py`;
- `ctx.register_middleware("llm_request", callback)` registers request
  middleware;
- the callback receives `request`, `session_id`, `model`, and `base_url`;
- middleware may return `{"request": updated, "source": ...}`;
- `hermes_cli.config.load_config()` exposes the plugin entry under
  `plugins.entries.hermes-olla-sticky-sessions.provider_identities`;
- `hermes_cli.runtime_provider.find_custom_provider_identity(base_url)` maps
  the resolved endpoint to a canonical `custom:<name>` identity.

`find_custom_provider_identity` is a Hermes runtime API, not a generic plugin
context field. Re-check this contract before upgrading across Hermes releases.
Paths that bypass the main `llm_request` middleware, such as independent
auxiliary clients, are outside this plugin's scope.

## Development

Hermes Agent is deliberately not a runtime or test dependency. The pytest
harness installs a small fake `hermes_cli.runtime_provider` module before
collection, and each test loads the plugin against an isolated lookup stub.

```bash
uv sync --frozen
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

## Versioning

This project follows [Semantic Versioning](https://semver.org/). The version in
`plugin.yaml` is the plugin version read by Hermes; `pyproject.toml` mirrors it
for development tooling, and tests require both values to match.

For a release:

1. update both version fields;
2. move the pending entries in `CHANGELOG.md` under the dated version;
3. run the complete validation commands;
4. create a reviewed Git tag named `v<version>`;
5. publish the GitHub release from that tag.

No release tag should be created from an unreviewed or untested diff.

## Related projects and documentation

- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) —
  the agent and plugin runtime.
- [Hermes plugin documentation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/plugins.md)
  — discovery, installation, enablement, and lifecycle.
- [thushan/olla](https://github.com/thushan/olla) — the router that owns sticky
  affinity, endpoint health, and repinning.
- [Olla sticky-session documentation](https://github.com/thushan/olla/blob/main/docs/content/concepts/sticky-sessions.md)
  — `X-Olla-Session-ID`, key sources, response headers, and observability.
- [shared-goals/thunder-forge](https://github.com/shared-goals/thunder-forge) —
  the validated edge and cluster reference stack whose provider identity is
  currently enabled.
- [jundot/omlx](https://github.com/jundot/omlx) — the validated Apple Silicon
  inference backend in the Thunder Forge reference stack. The plugin itself is
  client-side and does not depend on a particular Olla backend.
- [NousResearch/hermes-example-plugins](https://github.com/NousResearch/hermes-example-plugins)
  — standalone Hermes plugin examples.

## License

[MIT](LICENSE)
