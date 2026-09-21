# Phase Implementation Contract

This contract is the reusable development agreement for future Phase Issues and
implementation batches. The GitHub Issue remains the source of truth for
phase-specific requirements. This document defines shared implementation,
validation, Git, and reporting rules.

A Phase is a unit of planned change and review. A Phase number is development
history; it is not an architectural layer, a runtime state, or a reason to
create a new production boundary.

## Architecture and simplicity checkpoint

Before adding a new production module, boundary, adapter, wrapper, helper,
public model, or dependency seam, answer these questions:

1. What new responsibility does it own?
2. Is that responsibility observable outside the implementation?
3. Is it a state-transition owner, persistence owner, external side-effect
   boundary, human-approval boundary, trust boundary, or genuinely reusable
   domain abstraction?
4. Can an existing stable boundary be extended, composed, or reused without
   weakening the required invariants?
5. Would the new structure exist only to preserve a previous Phase call graph
   or to make internal calls mockable?

If the last answer is yes and there is no independent responsibility, prefer
not to add the new structure.

Do not justify a design solely because an earlier Phase or existing focused
test already has the same shape. Existing structure and tests may themselves be
technical debt.

## Public contract rule

Phase-specific requirements should describe primarily:

- purpose and externally observable behavior;
- accepted inputs and returned outcomes;
- state-transition and persistence invariants;
- external side effects and their authorization gates;
- restart/recovery semantics;
- stop conditions;
- public error/safety guarantees;
- explicit non-goals.

Avoid specifying internal function decomposition, private call order, helper
names, argument order between internal functions, dependency-call counts, or
object identity unless those facts are themselves required external contracts.

A public API should be added only when callers are expected to depend on it
across changes. Internal Phase boundaries should not automatically become
public exports.

## Dependency injection rule

Prefer dependency injection for actual ports of nondeterminism or side effects,
for example:

- provider / transport calls;
- filesystem or durable-store ports when substitutability is required;
- clock, randomness, UUID, environment access;
- explicitly pluggable provider or adapter implementations.

Do not add public injectable function parameters solely to assert which
trusted internal helper is called, how many times it is called, or in what
argument order.

## Branch creation and synchronization

For a new Issue branch, perform these operations in order:

1. Run `git fetch origin`.
2. Verify that the worktree is clean and that no unexpected changes are present.
3. Create the branch directly from the latest `origin/main`, never from a
   potentially stale local `main`.
4. Use a name beginning with `codex/issue-<issue-number>-` for Issue branches.

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

Also verify the existing public contracts and production owners relevant to the
Issue. Record:

- starting `origin/main` SHA;
- starting HEAD SHA;
- merge-base;
- base collect count as a diagnostic;
- relevant existing public APIs and tests;
- whether equivalent behavior already exists.

Stop before implementation when the worktree contains unexpected changes, the
branch is not based on the required predecessor, a required public contract is
missing, or the Issue cannot be satisfied safely within scope.

## Collect count is diagnostic, not a target

The collected-test count is useful for detecting unexpected changes, but it is
not an acceptance target and must not drive test creation.

Report the base and final collect counts and explain material differences.
Do not require:

```text
final collect count = base collect count + a predeclared exact number
```

Do not add tests merely to satisfy an expected count.

## Requirement-to-verification mapping

Before implementation, map each material acceptance criterion to the best
verification method:

```text
acceptance criterion
    -> observable test / existing contract test / static inspection / documentation review
```

Not every Issue bullet requires a unique test. Related invariants should be
covered by the smallest clear behavioral test or parameterized matrix.

A structural constraint that has no runtime behavior may be checked by review
or static inspection. Do not turn internal implementation choices into public
contracts merely to make them testable.

For documentation-only work, document review is sufficient; no application
test is added solely for documentation.

## Implementation and validation order

Use this order:

1. Read the Issue, `AGENTS.md`, this contract, current architecture guidance,
   and direct public dependencies.
2. Identify the existing owner of each required responsibility.
3. Decide whether the change can be made without adding a new architectural
   boundary.
4. Identify the observable acceptance criteria and existing regression coverage.
5. Add or update only the focused tests needed for changed behavior or newly
   required invariants.
6. Implement the minimum change.
7. Run focused and directly related behavioral tests.
8. Self-review the diff for unnecessary structure and implementation-locking
   tests.
9. Run the full validation suite after focused validation passes.
10. Commit, push, create a Draft PR, and inspect latest-head CI when authorized.

Do not repeatedly run the full pytest suite when focused feedback is sufficient.

## Test contract: what to verify

Tests should prefer observable contracts.

### Verify by default when relevant

- public inputs and outputs;
- workflow/domain state transitions;
- durable state/event/artifact contents and lineage;
- approval and authorization gates;
- external provider or publication side-effect count;
- at-most-once / exactly-once semantics at real side-effect boundaries;
- zero external side effects when authorization or recovery rules prohibit them;
- no implicit retry, replay, fallback, or automatic continuation;
- crash/restart/recovery behavior from durable evidence;
- append-only / no-overwrite rules;
- deterministic serialization and digest behavior when those bytes are durable
  or externally audited;
- rollback/compensation behavior when the component actually owns a multi-write
  durable operation;
- safe public error classification and secret non-disclosure.

### Do not verify by default

Do not freeze these implementation details unless the Issue explains why they
are externally significant:

- which trusted internal function is called;
- exact call count of pure/internal helpers;
- positional argument order between internal functions;
- Python object identity for immutable value results;
- identity of a default injected dependency function;
- source-code string counts;
- AST shape, absence of loops, or a particular helper decomposition;
- private helper names;
- exact module layering inherited from earlier Phases;
- exact runtime type/subclass rejection where ordinary validated value
  semantics are sufficient.

Source or AST audits are appropriate only for a narrowly justified property
that cannot be verified behaviorally, such as proving a forbidden import or
ambient dependency is absent. Prefer dependency/lint tooling when available.

## Internal mutation and compensation tests

Mutation/rollback tests are required where a component owns durable mutation or
crosses an untrusted/external boundary.

Do not require every read-only wrapper around trusted internal code to snapshot
and restore the same state merely to defend against its own dependency. Prefer
one clear owner for mutation and compensation.

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
- concise acceptance-criterion-to-verification mapping;
- focused and related behavioral test results;
- base and final collect counts as diagnostics;
- full pytest result;
- compileall, `ruff check .`, `git diff --check`, and CLI-help results when
  relevant;
- scope exclusions respected;
- confirmation that no unintended provider/network/paid side effect occurred.

Update the body whenever later commits make these values stale. Keep the PR in
Draft state until ChatGPT review approval; do not mark it Ready or merge it into
`main` without that approval.

## Completion report

The ChatGPT-facing completion report must include:

- Issue number and branch;
- starting `origin/main` SHA, starting HEAD SHA, and merge-base;
- changed files;
- significant design/simplicity decisions;
- acceptance-criterion-to-verification mapping;
- focused and related tests;
- base and final collect counts;
- full validation results;
- commit SHA;
- Draft PR number, URL, and PR head SHA when a PR was requested;
- latest-head CI state when applicable;
- confirmation that there are no out-of-scope changes;
- confirmation that prohibited Git operations were not used.

For documentation-only work, report the manual review performed and the absence
of runtime behavior changes.

The final report should be concise. Do not reproduce internal call graphs unless
they are necessary to explain an external contract or a blocker.
