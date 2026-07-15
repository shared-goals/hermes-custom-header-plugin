# Security policy

## Supported versions

Security fixes are provided for the latest tagged release. Older versions may
receive a patch when a safe backport is practical.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or include provider
credentials or private endpoints in a report.

Use GitHub's private vulnerability reporting for this repository. Include the
affected version, impact, minimal reproduction, and a redacted description of
the relevant Hermes and provider configuration. Maintainers will acknowledge a
complete report as soon as practical and coordinate disclosure after a fix is
available.

## Scope

The plugin creates outbound request headers inside the Hermes process. Reports
about header injection, provider-selection confusion, configuration validation
bypass, or unintended request mutation are in scope.
Downstream gateway behavior that is independent of the generated header belongs
to that project's security process.
