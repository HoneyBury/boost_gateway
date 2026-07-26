# Support Policy

## Supported requests

Use GitHub Issues for reproducible defects, documentation errors, and focused enhancement
requests concerning the current supported release and platform matrix. Include the full
version or candidate SHA, OS/architecture, dependency profile, relevant configuration
with secrets removed, reproduction steps, and bounded logs or summaries.

## Unsupported requests

The project does not promise private consulting, emergency operational response, arbitrary
cloud or distribution support, capacity guarantees outside recorded evidence, multi-node
HA, or support for modified release assets. Experimental gRPC and archived v1/v2 surfaces
are not part of the default production support contract. See
[`docs/current-state.md`](docs/current-state.md) for authoritative boundaries.

Do not use the support path for vulnerabilities, credentials, or production user data;
follow [SECURITY.md](SECURITY.md) instead.

## Where to ask

- Reproducible bug: use the repository bug-report issue template.
- Planned work or an operational gap: use a project task linked to the maintained TODO
  board.
- Security concern: use the private contact in [SECURITY.md](SECURITY.md).
- Contribution or review question: use the pull request and [CONTRIBUTING.md](CONTRIBUTING.md).

GitHub Discussions is not a maintained support surface for this repository.

## Maintenance expectations

Maintainers triage by security and data-loss risk first, then production regressions,
supported release defects, and enhancements. Acceptance of an issue does not commit to a
release date. Fixes must preserve tests, documentation, rollback boundaries, and release
evidence; support pressure is not a reason to lower gates or rewrite historical evidence.
