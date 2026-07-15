# Changelog

All notable changes to this project are documented in this file. The project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-07-15

### Changed

- Replaced configurable hash recipes with one transparent header value:
  `<session_id>:<model>`.
- Simplified each provider rule to one required `header` field while preserving
  exact provider lookup, duplicate-URL fail-closed behavior, caller-header
  precedence, and immutable request updates.
- Updated the quick start around the existing Hermes `custom_providers` model
  configuration used by Chez: `agent-better` by default and `coder-better` for
  coding work.
- Extended declared and tested compatibility through Python 3.14, matching the
  live Chez Hermes Agent runtime.

### Removed

- Removed namespace, prefix, input-list, digest-length, SHA-256, and HMAC
  configuration.
- Removed `HERMES_CUSTOM_HEADER_HMAC_KEY`; version 0.4.0 has no plugin-specific
  secret.

## [0.3.0] - 2026-07-15

### Added

- Added keyed `hmac-sha256` header derivation using the installation-scoped
  `HERMES_CUSTOM_HEADER_HMAC_KEY` secret.
- Added redacted configuration diagnostics, duplicate-provider-URL detection,
  real Hermes Agent 0.18.2 integration coverage, and a weekly Hermes `main`
  compatibility canary.
- Added dependency update automation, immutable workflow action pins, an
  installation guide, runtime compatibility notes, and a security policy.

### Changed

- Raised the minimum digest length from 8 to 16 hexadecimal characters.
- Reject prefixes beginning with whitespace and runtime inputs containing NUL
  so every generated header has an unambiguous, valid wire representation.
- Ignore unrelated Hermes-managed plugin entry fields while keeping the
  plugin-owned provider and rule schema strict.
- Document SHA-256 values as deterministic pseudonyms and recommend HMAC for
  protection against offline input guessing.

## [0.2.0] - 2026-07-15

### Added

- Required per-rule `namespace` values that isolate independently configured
  Hermes installations sharing the same provider and runtime session IDs.

### Changed

- SHA-256 recipes now hash `namespace` before the configured runtime inputs.
- Version 0.1.0 configurations must add a namespace before restarting with
  version 0.2.0; missing or malformed namespaces fail closed.

## [0.1.0] - 2026-07-15

### Added

- Configurable computed request headers for exact named Hermes custom
  providers, with Olla sticky sessions as a documented recipe.
- SHA-256 value recipes built from `session_id` and optional `model`, with
  configurable header name, prefix, digest length, and input scope.
- Fail-closed configuration and provider lookup, reserved-header protection,
  and case-insensitive preservation of explicit caller headers.
- Isolated contract tests with no Hermes Agent package dependency.
- Installation, verification, compatibility, rollback, and release guidance.

[Unreleased]: https://github.com/shared-goals/hermes-custom-header-plugin/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/shared-goals/hermes-custom-header-plugin/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/shared-goals/hermes-custom-header-plugin/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/shared-goals/hermes-custom-header-plugin/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shared-goals/hermes-custom-header-plugin/releases/tag/v0.1.0
