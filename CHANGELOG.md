# Changelog

All notable changes to this project are documented in this file. The project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-07-15

### Added

- Conversation- and model-scoped `X-Olla-Session-ID` middleware for the named
  Hermes providers explicitly allowed in plugin configuration.
- Fail-closed configuration and provider lookup, with case-insensitive
  preservation of explicit sticky-session headers.
- Isolated contract tests with no Hermes Agent package dependency.
- Installation, verification, compatibility, rollback, and release guidance.

[Unreleased]: https://github.com/shared-goals/hermes-olla-sticky-sessions/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/shared-goals/hermes-olla-sticky-sessions/releases/tag/v0.1.0
