# Configure computed headers

1. Generate a secret containing at least 32 bytes and save it as
   `HERMES_CUSTOM_HEADER_HMAC_KEY` in `~/.hermes/.env`.
2. Add an exact `custom:<name>` rule under
   `plugins.entries.hermes-custom-header-plugin.providers` in
   `~/.hermes/config.yaml`.
3. Ensure every named custom provider has a unique `base_url`.
4. Restart the Hermes gateway or start a new CLI session.

Copy the complete configuration and verification recipe from the repository
[README](https://github.com/shared-goals/hermes-custom-header-plugin#quick-start).
