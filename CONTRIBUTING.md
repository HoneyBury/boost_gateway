# Contributing to BoostGateway

Start with the [developer onboarding guide](docs/ONBOARDING.md) for the supported
toolchain, Conan setup, build commands, and test layers. By participating, you agree to
follow the repository [governance policy](GOVERNANCE.md), [security policy](SECURITY.md),
and [support policy](SUPPORT.md).

## Pull requests and review

- Create a focused branch from `main` and open a pull request back to `main`.
- Do not push directly to `main`. The required `linux-build-and-test` check must pass.
- Obtain at least one independent approval. A pull request author cannot approve their own
  change; requested changes and unresolved review conversations remain blocking.
- Request the owners in [CODEOWNERS](CODEOWNERS) for sensitive or cross-boundary changes.
- Keep unrelated refactors, generated output, and operational evidence out of the change.

The protected-branch settings that enforce these rules are external GitHub state and must
be verified separately as described in [GOVERNANCE.md](GOVERNANCE.md).

## Required validation

Build and run the relevant unit and integration suites before opening a pull request. The
hosted pull-request workflow performs a bounded Release build, complete CTest run, and
repository governance checks. Add focused tests for changed behavior and report the exact
commands and results in the pull request template.

For a normal local change, begin with `python3.12 scripts/dev.py doctor`, use
`python3.12 scripts/dev.py test <layer> --build-dir <dir>` during development, and run
`.venv/dev/bin/python scripts/dev.py check` before pushing after creating the development
environment documented in `docs/ONBOARDING.md`. The facade is intentionally local-only;
release and fixed-runner workflows continue to call explicit governed entrypoints.
Use `python3.12 scripts/dev.py commands --domain <domain>` to find the supported command
and its authoritative runbook before invoking an internal gate or tool directly.

Long soak, capacity, Redis live, native platform, and pre-production results require their
admitted runners. Do not represent a local or hosted smoke run as fixed-runner evidence.

## Sensitive changes

Security, identity, protocol/schema, dependency lockfile, release, deployment, monitoring,
and governance changes require an explicit rollback or compatibility plan. Do not place
credentials, private keys, production data, or vulnerability details in a pull request or
public issue. Follow [SECURITY.md](SECURITY.md) for confidential disclosure.

Emergency changes use the documented pull-request and rollback path in
[GOVERNANCE.md](GOVERNANCE.md); urgency does not waive checks or independent review.

## Documentation and commits

Update current documentation and `CHANGELOG.md` when behavior, configuration, public API,
deployment, or support boundaries change. Use the repository
[commit convention](.github/COMMIT_CONVENTION.md). Archived documents are historical
evidence and must not be edited to redefine current behavior.
