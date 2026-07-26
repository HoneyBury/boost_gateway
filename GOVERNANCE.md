# Repository Governance

This policy defines the repository-side contract. [CODEOWNERS](CODEOWNERS),
[CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and
[SUPPORT.md](SUPPORT.md) define the associated ownership, contribution, disclosure, and
maintenance boundaries.

## Ownership

`@HoneyBury` is the current primary maintainer and default code owner. Code ownership means
responsibility for review routing and maintained facts; it does not permit an author to
self-approve. Before GitHub required-review enforcement is enabled, the repository must
have at least one additional trusted human who is eligible to approve maintainer-authored
pull requests.

## Normal change path

All changes target `main` through a focused pull request. The hosted
`linux-build-and-test` check must pass, at least one independent reviewer must approve, and
all review conversations must be resolved. Changes to `.github/`, governance gates,
security, release, configuration, or deployment surfaces require the relevant code owner
and an explicit rollback or compatibility note.

Direct pushes, force pushes, branch deletion, approval of one's own change, and lowering a
gate to merge a change are prohibited.

## Emergency change path

A P0 security, data-loss, or active production outage may use an expedited change:

1. Open an incident or issue that identifies impact, owner, candidate SHA, and rollback.
2. Prefer reverting to the last verified release or configuration before writing a new fix.
3. Open the smallest possible pull request and label the incident in its summary.
4. Run the same hosted required check, obtain one independent approval, and resolve all
   review conversations.
5. After merge, record deployment/rollback evidence and open follow-up work for any deferred
   root-cause or hardening action.

No silent administrator bypass is permitted. If GitHub itself is unavailable, use the
documented operational rollback procedure for an already verified immutable release; do
not introduce an unreviewed source release.

## Release governance

Official releases must use an annotated immutable `v*` tag whose commit belongs to the
governed `main` history and whose required candidate evidence is complete. The release
workflow, tag ruleset, platform evidence, and online asset verification are one transaction;
manual asset replacement or tag movement is prohibited. The detailed release evidence
contract remains in [`docs/release-governance.md`](docs/release-governance.md).

## External GitHub settings

Branch protection, rulesets, required checks, review requirements, conversation resolution,
administrator enforcement, private vulnerability reporting, Actions permissions, and
collaborator eligibility are GitHub state outside this repository. Repository files do not
prove that these settings are active.

Before closing a governance TODO or starting a formal validation window, read the settings
back through the GitHub API and retain the result with the issue evidence. At minimum,
`main` must require `linux-build-and-test`, one independent approval, and conversation
resolution, with force push/deletion disabled and no silent administrator bypass. Immutable
`v*` tag protection remains independently required.
