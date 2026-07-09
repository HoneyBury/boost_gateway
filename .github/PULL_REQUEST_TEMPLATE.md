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

- [ ] Local build passes: `cmake --build --preset <preset> --parallel`
- [ ] Relevant ctest suites pass: `ctest --preset <preset> -R <target>`
- [ ] Governance gates pass (if applicable)
- [ ] New tests added for new/changed code

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
