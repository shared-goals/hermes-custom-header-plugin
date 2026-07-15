# Changelog

All notable changes to this project are documented in this file. The project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/shared-goals/hermes-custom-header-plugin/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/shared-goals/hermes-custom-header-plugin/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shared-goals/hermes-custom-header-plugin/releases/tag/v0.1.0
