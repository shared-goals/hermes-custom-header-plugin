# Proposed Olla documentation integration

This is a draft for an upstream pull request to
[`thushan/olla`](https://github.com/thushan/olla). Submit it only after this
plugin has a reviewed public release URL.

## Suggested pull request

Title:

```text
docs: add Hermes Agent sticky sessions integration
```

Suggested changes:

1. Add `docs/content/integrations/frontend/hermes-agent.md`.
2. Add **Hermes Agent** under **Integrations → Frontends** in
   `docs/mkdocs.yml`.
3. Add Hermes Agent to the **Frontend Support** section of
   `docs/content/integrations/overview.md`.

## Suggested overview text

```markdown
### Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) can provide an
explicit, conversation-scoped `X-Olla-Session-ID` through the community
[Hermes Olla Sticky Sessions](https://github.com/shared-goals/hermes-olla-sticky-sessions)
plugin. This gives multi-turn conversations stable Olla affinity while keeping
endpoint selection, health checks, and repinning in Olla.

The plugin activates only for canonical Hermes custom-provider identities in an
explicit, fail-closed allow-list. Thunder Forge is the validated reference
stack for the current release. See the integration guide for configuration,
scope, and verification details.
```

## Suggested integration guide outline

- Explain that the plugin derives an opaque key from Hermes `session_id` and
  effective model alias.
- Link to Olla's existing **Sticky Sessions** concept instead of duplicating
  its routing and configuration documentation.
- Require `proxy.sticky_sessions.enabled: true` and `session_header` in
  `key_sources`.
- Show the installation command:

  ```bash
  hermes plugins install shared-goals/hermes-olla-sticky-sessions --enable
  ```

- Require an explicit `provider_identities` allow-list and use
  `custom:thunder-forge` as the validated example.
- Verify the first request as `miss`, a repeated turn as `hit`, and inspect
  `X-Olla-Endpoint`, `X-Olla-Sticky-Session`, and
  `X-Olla-Sticky-Key-Source`.
- Avoid claiming guaranteed inference-cache hits; sticky routing only improves
  cache locality.

## Maintainer-facing rationale

The integration belongs under **Frontends** because Hermes is the client that
supplies Olla's explicit session key. The plugin does not modify Olla, replace
its balancer, or introduce another sticky-session store.
