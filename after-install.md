# Configure the session-and-model header

1. Add an exact `custom:<name>` rule under
   `plugins.entries.hermes-custom-header-plugin.providers` in
   `~/.hermes/config.yaml`.
2. Set its `header` field to the request header understood by the downstream
   gateway, for example `X-Olla-Session-ID`.
3. Ensure every named custom provider has a unique `base_url`.
4. Restart the Hermes gateway or start a new CLI session.

The plugin sends `<session_id>:<model>` as the header value. It does not require
an additional secret and does not configure the provider's models.

Copy the complete configuration and verification recipe from the repository
[README](https://github.com/shared-goals/hermes-custom-header-plugin#quick-start).
