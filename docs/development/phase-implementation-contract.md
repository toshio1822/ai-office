# Phase Implementation Contract

This contract is the reusable development agreement for future phase Issues and
Codex implementation batches. The GitHub Issue remains the source of truth for
phase-specific requirements. This document defines only shared implementation,
validation, Git, and reporting rules.

A phase Issue defines only its goal, input/output contract, dependency, public
API, expected changed files, phase-specific tests, exclusions, and predecessor
requirements. An Issue overrides this contract only when it explicitly names
the conflicting rule and replacement behavior.

## Branch creation and synchronization

For a new Issue branch, perform these operations in order:

1. Run `git fetch origin`.
2. Verify that the worktree is clean and that no unexpected changes are present.
3. Create the branch directly from the latest `origin/main`, never from a
   potentially stale local `main`.
4. Use a name beginning with `codex/issue-<issue-number>-`.

The intended command form is:

```bash
git switch -c codex/issue-<issue-number>-<slug> origin/main
```

If an existing Issue branch is behind `origin/main`, synchronize it with a
normal merge into the working branch:

```bash
git merge origin/main
```

This synchronization merge is distinct from merging the PR into `main`. Stop
and report if the synchronization merge conflicts. Cherry-pick is not an
implicit replacement for branch synchronization.

## Mandatory preflight

Before implementation, record and verify:

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git merge-base HEAD origin/main
git diff --name-only origin/main...HEAD
.venv/bin/pytest --collect-only -q
```

Also verify that the predecessor implementation, focused-test files, and any
public dependency required by the Issue exist. Record these values:

- starting `origin/main` SHA;
- starting HEAD SHA;
- merge-base;
- base collect count;
- predecessor and focused-test file checks.

Stop before implementation when the worktree contains unexpected changes, the
branch is not based on the required predecessor, a required file or public
dependency is missing, or the Issue cannot be satisfied within its allowed
change set. A collect-count difference alone is not a stop condition when it
is explained by known new tests or an updated base.

## Collect-count rule

Do not validate collection only against a hard-coded expected total. Use this
invariant:

```text
final collect count = recorded base collect count + newly added collected test cases
```

The PR and completion reports must explain every difference from the immediately
preceding phase's reported count, including changes caused by an updated base.

## Requirement-to-test matrix

Before implementation, create a working matrix in this form:

```text
Issue requirement → implementation location → focused test name or parameterized matrix
```

The final report and Draft PR body must include a concise version of the same
mapping. No required contract may be reported complete without either a direct
focused test or a named existing shared contract test covering it. For a
documentation-only Issue, the document's manual checklist is the focused
validation evidence, and no application test is added solely for documentation.

## Implementation and validation order

Use this order for phase implementation:

1. Read the Issue, `AGENTS.md`, this contract, the primary reference
   implementation, and direct dependencies.
2. Prepare the requirement-to-test matrix.
3. Add or update focused tests first.
4. Implement the minimum change.
5. Run focused tests.
6. Run directly related predecessor and dependency tests.
7. Self-review the diff against the Issue and the matrix.
8. Run the full validation suite once after focused validation passes.
9. Commit, push, create a Draft PR, and inspect latest-head CI.

Do not repeatedly run the full pytest suite during normal development when
focused tests provide the needed feedback. Full pytest follows focused and
related validation and is normally run exactly once.

## Reusable strict-boundary test categories

For strict read-only bridge or boundary phases, the Issue may refer to these
standard categories rather than repeating every case. The Issue must still
state which categories apply and identify every phase-specific addition or
exception.

- normal routes and stop routes;
- exact type, subclass, and attribute-compatible substitute rejection;
- workflow and explicit-target validation;
- dependency callability and exact argument identity;
- dependency return type, field, discriminator, and object-identity validation;
- state, event, and both-target mutation using replace, delete, truncate, and
  append where applicable;
- safe error, unexpected error, and malformed return;
- no-mutation zero-write behavior;
- compensation restoration of both targets after detected mutation;
- state rollback failure, event rollback failure, both rollback failures, and
  both restoration attempts;
- safe-error identity preservation;
- unexpected-error sanitization;
- no retry and exact dependency call count;
- no sensitive details in public errors.

Do not introduce shared production helpers or shared test helpers through a
documentation-only contract change.

## Git operation meanings and prohibitions

The following operations have distinct meanings:

- `git merge origin/main` into the working branch is allowed for synchronization;
- marking a Draft PR Ready is allowed only after ChatGPT review approval;
- merging a PR into `main` is allowed only after ChatGPT review approval;
- the PR merge method is a merge commit unless a later Issue explicitly changes
  project policy.

Always prohibit:

- direct commit or push to `main`;
- amend, rebase, squash, force push, and `--force-with-lease`;
- reset, clean, or stash used to hide unexpected changes;
- branch deletion unless explicitly authorized;
- Issue closure or PR Ready/merge before approval.

## Draft PR timing and required contents

Create the Draft PR after local validation so its initial body contains current
results. Include at least:

- Issue number;
- starting `origin/main` SHA, starting HEAD SHA, and merge-base;
- current head SHA;
- changed files;
- the requirement-to-implementation-to-test mapping;
- focused and related test results;
- base and final collect counts, with the count invariant and any difference
  from the preceding phase explained;
- full pytest result;
- compileall, `ruff check .`, `git diff --check`, and CLI-help results when
  required by the Issue;
- scope exclusions respected;
- confirmation that no real paid provider call was made.

Update the body whenever later commits make these values stale. Keep the PR in
Draft state until ChatGPT review approval; do not mark it Ready or merge it into
`main` without that approval.

## Completion report

The ChatGPT-facing completion report must include:

- Issue number and branch;
- starting `origin/main` SHA, starting HEAD SHA, merge-base, and all preflight
  results;
- changed files;
- the requirement-to-implementation-to-test mapping;
- focused tests and related tests;
- base and final collect counts;
- full validation results;
- commit SHA;
- Draft PR number, URL, and PR head SHA;
- latest-head CI state;
- confirmation that the Issue remains open and the PR remains Draft;
- confirmation that there are no out-of-scope changes and the branch was not
  deleted.

For documentation-only work, explicitly report the manual review of required
sections and Markdown headings, code blocks, and lists, plus the absence of
runtime behavior, public API, application-code, test, and CI changes.

The final line must be:

```text
PR #<PR-number>をレビューしてください。
```

