# Bounded Engineering Issue Investigation → Draft PR (Issues #828–#829)

The Issue #828 workflow prepares a bounded investigation Run and then constructs
the existing `bounded-change-review` assignment and a matching local-effect
policy from its successful result. It reuses ordinary Workflow Runs, immutable
Run Manifests and execution history, GitHub's existing GET-only transport,
fixed-base Git blobs, and `LocalExecutionPolicy`. It adds no Job engine, model
loop, delivery path, or second state store.

The byte budgets cover the serialized product Run Input and its finite
continuation inputs, not billed Provider tokens or hidden protocol overhead.
Actual Provider execution remains subject to the existing per-Step preview,
Business/Execution Approval, and configured Provider limits.

Issue #828 does **not** combine all operations into one command. The operator
selects and confirms the fixed base, search/read/provider-send/write patterns,
source/provider byte budgets, finite investigation count, and a named
preconfigured Oracle profile. Existing Workflow approval controls still apply
to every actual model Step; the separate local-effect authorization is still
required before any bounded local verification. GitHub write approval and Draft
PR delivery are outside this feature and remain separate.

## Prepare the first Run Input

Use a clean checkout at the exact fixed base. Set pattern scopes and budgets to
the smallest useful values. Search, local read, Provider send, and write scopes
are separate; only paths inside all required scopes can be supplied to the
investigator. Issue title/body are also provider-bound input and are included
only with the explicit `--send-issue-body` flag.

The operator chooses a registered policy profile from the trusted application
catalog; the AI cannot select or approve its own verification conditions. The
shipped `python-syntax` profile uses only the standard library to parse the
selected Python sources; it does not run project tests or prove behavior. Its
exact argv and resource limits are reviewed configuration, never model output.
The selected profile digest is pinned in the research Run Input and checked
again when bounded Job inputs are prepared. No policy JSON or command argv has
to be assembled by the operator. Do not put secrets in the Issue, source
scope, Run Input, or policy. Output directories must already exist, be mode
`0700` or stricter, and be outside the repository.

```sh
umask 077
mkdir -m 700 /tmp/issue-828-output
base="$(git -C /path/to/clean/ai-office rev-parse HEAD)"
ai-office workflows prepare-engineering-issue \
  --issue-url https://github.com/OWNER/REPO/issues/NUMBER \
  --repository OWNER/REPO \
  --repository-path /path/to/clean/ai-office \
  --base-revision "$base" \
  --search-pattern 'src/**/*.py' \
  --search-pattern 'tests/**/*.py' \
  --read-pattern 'src/**/*.py' \
  --read-pattern 'tests/**/*.py' \
  --send-pattern 'src/**/*.py' \
  --send-pattern 'tests/**/*.py' \
  --write-pattern 'src/**/*.py' \
  --initial-files 2 \
  --max-files 12 \
  --max-source-bytes 300000 \
  --max-provider-input-bytes 900000 \
  --max-provider-total-bytes 3600000 \
  --max-provider-attempts 3 \
  --oracle-profile python-syntax \
  --output-directory /tmp/issue-828-output \
  --send-issue-body
```

The command performs a single GitHub Issue GET and only local read-only Git
operations. It writes `engineering-issue-round-1.json` exclusively with mode
`0600`; the output summary reports base, hashes, selected path names, and byte
counts, not source contents. Candidate discovery walks Git path metadata matching
the approved search patterns, then intersects it with read and Provider-send
scopes. The `--max-files` shortlist reserves half its slots (rounding up) for
write-authorized sources and half (rounding down) for read-only sources such as
tests. Within each group, Issue title/body path-token overlap ranks above
lexical path order; unused slots are reallocated first to remaining
write-authorized paths, then read-only paths. The summary and Run Input report
the counts for both groups, search/read/send patterns, omissions, each offered
path's selection reason, and any remaining limit. Omitted or individually
over-budget file contents were not read or sent, so the AI must not claim the
candidate set is exhaustive. A missing or invalid Issue, secret-like text,
dirty/moved base, unapproved path, or byte/attempt budget excess still stops
before output.
Omitting `--send-issue-body` deliberately stops preparation before the GET.

## Run the investigation and continue finitely

Start the configured one-step workflow through the normal preview and explicit
Business/Execution Approval process. This is the point where an operator may
authorize a real Provider call. The implementation proof and tests use no real
Provider API or model Job.

```sh
ai-office workflows start engineering-issue-investigation \
  --run-id issue-828-round-1 \
  --run-input-file /tmp/issue-828-output/engineering-issue-round-1.json \
  --run-store /private/path/to/ai-office-runs
ai-office workflows result issue-828-round-1 --run-store /private/path/to/ai-office-runs
```

If the successful result says `needs_more_evidence`, prepare exactly one next
Run Input from that persisted Run. Only listed, unsent candidates are eligible;
the program verifies the pinned Git blob and rechecks read/send scopes and
cumulative budgets. Start it as a normal new Run. The Run count cannot exceed
the approved maximum.

```sh
ai-office workflows prepare-next-engineering-issue issue-828-round-1 \
  --run-store /private/path/to/ai-office-runs \
  --output-directory /tmp/issue-828-output
```

An unsuccessful, ambiguous, out-of-scope, over-budget, or non-terminal Run
cannot be continued. A `blocked` result stops; it does not trigger an automatic
retry or permission expansion.

## Prepare existing bounded-change-review inputs

After a successful `ready` research result, construct an assignment and
run-bound local policy from exact selected source bytes at the fixed base:

```sh
ai-office workflows prepare-engineering-job-input issue-828-round-2 \
  --run-store /private/path/to/ai-office-runs \
  --output-directory /tmp/issue-828-output
```

The command verifies exact evidence quotes and line ranges, that selected
read/test/write paths are authorized, that every selected byte still matches
the Git blob at the fixed clean base, and that the selected registered Oracle
profile digest has not changed. It writes `assignment-<digest>.json` and
`local-effect-policy-<digest>.json` exclusively with mode `0600`. The assignment
has the existing `request`, `repository`, `allowed_paths`, `acceptance_criteria`,
`source_context`, and `source_sha256` shape expected by the bounded Workflow;
candidate-search coverage is preserved for the Job, and the policy is still
only a proposal. Existing `bounded-job-run` Business, Execution, and
local-effect approval remains necessary. Use the exact fixed base checkout; do
not write candidate changes into it during preparation.

## Start the existing bounded Job by Issue URL

Issue #829 connects that prepared investigation to the existing bounded Job.
After the latest investigation Run for the Issue has completed successfully
with a `ready` result, the operator can start `bounded-change-review` without
copying its Run ID, assignment JSON, policy JSON, source hashes, or Job ID.
The command resolves the unique latest research round from the selected
research Run store, revalidates its Manifest/history/source bytes, and uses a
GET-only GitHub repository read to pin its numeric identity, full name, and
default branch into the existing Job assignment. A missing, ambiguous,
non-terminal, non-ready, stale-base, or mismatched-repository result stops
before Job creation.

Use a fresh, private Job Run store for this Issue and the already configured
execution-destination registry. This command retains the existing separate
Business, Execution, and local-effect confirmation prompts; the selected
Workflow, Oracle policy, and finite reviewer/revision plan remain authoritative.
The `bounded-change-review` Job continues to use the independently configured
reviewers and bounded Oracle. Job-internal pytest/Ruff and test-failure-driven
autofix are not part of this first-stage path.

```sh
ai-office workflows bounded-job-run \
  --issue-url https://github.com/OWNER/REPO/issues/NUMBER \
  --research-run-store /private/path/to/research-runs \
  --run-store /private/path/to/issue-job-runs \
  --workflow bounded-change-review \
  --execution-target APPROVED_DESTINATION \
  --execution-destinations /private/path/to/destinations.yaml
```

The launch preview reports the Issue URL, selected research Run, pinned
repository identity/default branch, assignment digest, Workflow and finite
Step plan before the existing approvals. A failed/stopped Job is visible
without creating a PR. A successful `candidate` or `reviewed` result is not
GitHub authorization.

## Separately approve Draft PR and inspect exact-HEAD CI

After candidate review, the existing Draft PR command resolves the sole Job
for that Issue by URL. It still performs the existing explicit confirmation
before creating a local commit, pushing a new branch, and opening a Draft PR.
`--yes` is only an operator-intent flag; use the interactive confirmation for
normal operation. It never marks the PR ready, merges, or closes the Issue.

```sh
ai-office workflows draft-pr \
  --issue-url https://github.com/OWNER/REPO/issues/NUMBER \
  --run-store /private/path/to/issue-job-runs

ai-office workflows engineering-issue-status \
  --issue-url https://github.com/OWNER/REPO/issues/NUMBER \
  --run-store /private/path/to/issue-job-runs
```

The status command aggregates the existing Job inspection and delivery records,
then performs only read-only GitHub GETs. It accepts a PR only when the stored
attempt/result, Issue marker, Artifact/review/Evidence, changed paths, target
repository/base, open Draft state, and PR HEAD all remain bound to the same
candidate. CI is fetched for that exact HEAD SHA; failure, pending, missing, or
inaccessible checks are reported distinctly, never as success. Before a Draft
PR exists the command reports that separate GitHub approval is still pending.
An ambiguous delivery result is inspect-only and never automatically retried.

The end-to-end proof uses fake Provider transport, temporary local Git data,
and fake GitHub/`gh`; these tests do not exercise a real Provider API, model
Job, or product GitHub delivery. The development PR for #829 is also separate
from, and does not authorize, such a live product run.
