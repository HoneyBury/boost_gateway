## Summary

<!-- One-sentence summary of the change. Link to any related issues. -->

## Type of Change

<!-- Check the relevant category. -->

- [ ] **bugfix** — backwards-compatible defect fix
- [ ] **feature** — backwards-compatible new capability
- [ ] **refactor** — code change with no external behavior change
- [ ] **docs** — documentation only
- [ ] **test** — test addition or improvement
- [ ] **perf** — performance change
- [ ] **governance** — CI, dependency, build system, or process change
- [ ] **breaking** — backwards-incompatible change (requires migration plan)

## Test Evidence

<!-- Describe what was tested and how. At minimum: -->

- [ ] Contributor readiness passes: `.venv/dev/bin/python scripts/dev.py ready`
- [ ] Focused tests for the changed behavior pass
- [ ] Governance gates pass (if applicable)
- [ ] New tests added for new/changed code
- [ ] Required `linux-build-and-test` check passes

<!-- Paste the exact focused command(s) and result. Use
     `python3.12 scripts/run_tests.py --recommend` when selecting a local layer. -->

## Review and Risk

- [ ] At least one independent reviewer approved
- [ ] All review conversations are resolved
- [ ] Rollback or compatibility impact is documented for sensitive changes
- [ ] Emergency changes link the incident and follow `GOVERNANCE.md`

## Documentation

- [ ] `docs/` updated if behavior, configuration, or API surface changed
- [ ] `CHANGELOG.md` entry added for user-facing changes

## Compatibility

<!-- Describe any migration concerns. -->

- [ ] No breaking changes to public API, SDK ABI, or wire protocol
- [ ] Migration path documented if breaking

---

<!--
PRs require at least one reviewer. Use GitHub's "Request review" to assign.
-->
