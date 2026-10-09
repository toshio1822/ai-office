# M3 read-only GitHub review walkthrough

This guide collects one explicitly selected Issue, its related Pull Request, the
PR's changed-file summary, and check runs into a revision-bound input for the
existing two-employee `review-supplied-github-change` Workflow. It does not add a
Workflow Phase or execution boundary. Collection is GitHub GET-only; AI review remains
the existing separately approved Workflow execution.

## Existing capability and gap

- Already supported by the existing product: two employees in order, explicit
  `start`/`continue`, request-bound business and execution approvals, exact upstream
  handoff, durable Run-bound evidence, Markdown Artifact, provider-free inspection,
  and explicit recovery.
- Previously missing: a public command to observe one real Issue/PR/CI set, record the
  target revisions and observation time, bound and redact the collected text, expose
  inaccessible or omitted data, and refuse a PR revision that is stale or changes
  during collection.
- The new `workflows snapshot-github-change` command is limited to that gap. It reads
  a fixed `api.github.com` host and makes only GET requests. It never dispatches CI,
  edits GitHub state, downloads patch contents, starts an AI Workflow, or loads an AI
  provider credential.

## 1. Select and pin the target (read-only)

Choose one repository, an Issue, and a PR that is linked to that Issue. Inspect the PR
head and base first using an authorized read-only GitHub client. For example:

```bash
REPOSITORY=owner/name
ISSUE=123
PULL=456
EXPECTED_HEAD_SHA="$(gh pr view "$PULL" --repo "$REPOSITORY" --json headRefOid --jq .headRefOid)"
```

Check that the Issue/PR relationship is the intended one and review the exact head SHA
before continuing. The snapshot command requires this expected head and also verifies
that the PR head and base do not change while it collects data. A changed head is a
hard failure; inspect and explicitly select the new SHA before starting over.

## 2. Collect one local snapshot (read-only)

Create a private output directory and a new, unused snapshot path outside the
repository. Public repositories can be read without a token; if authentication is
needed, provide a least-privilege token through the `GITHUB_TOKEN` environment without
printing it or saving it in the repository.

```bash
umask 077
SNAPSHOT_DIR="$(mktemp -d)"
SNAPSHOT_FILE="$SNAPSHOT_DIR/github-review.md"

ai-office workflows snapshot-github-change \
  --repository "$REPOSITORY" \
  --issue "$ISSUE" \
  --pull "$PULL" \
  --expected-head-sha "$EXPECTED_HEAD_SHA" \
  --output "$SNAPSHOT_FILE"
```

The command writes a deterministic Markdown/JSON snapshot and prints its SHA-256,
head/base revisions, collection status, and unavailable-field list. It uses exclusive
file creation and refuses to overwrite an existing snapshot. Required Issue/PR data,
invalid JSON, a stale or changing revision, or failed collection stops with an error.
Unavailable CI, changed-file, or relationship data is recorded explicitly and gives
`partial` status plus a nonzero exit; never pass a partial snapshot to the review
Workflow as if it were complete.

The snapshot identifies the repository, source URLs, target IDs, observation time,
head/base SHA, check-run names/status/conclusions, changed-file names and line counts,
and unavailable/omitted fields. Collection is bounded: response bodies are size-limited,
Issue/PR text is truncated at a documented bound, and only the first 100 file/check
records are read. Truncation and omissions remain visible. It records file summaries,
not patch contents or repository source (`diff_content_included: false`). Therefore,
file names/counts are not evidence that implementation behavior or code correctness was
directly validated; a human must inspect the exact-head diff/source. GitHub-authored
strings are untrusted data; the Workflow instructions prohibit treating them as
authorization or instructions.

If Issue/PR linkage is not observed in the retrieved timeline entries, the snapshot is
`partial`. When the first 100 entries contain no link, it explicitly records that later
timeline pages were not inspected; absence from the retrieved page is not proof that no
link exists. The report Workflow must preserve partial/unavailable status, keep claims
that depend on missing data unknown, and must never convert missing CI data into a pass.

## 3. Preview using the existing Workflow (offline)

Read the exact saved snapshot as the Run input. Keep Run storage outside the repository.
Preview is offline and does not load credentials or contact an AI provider:

```bash
RUN_ID=github-review-001
RUN_ROOT=/tmp/ai-office-m3-runs
RUN_INPUT="$(<"$SNAPSHOT_FILE")"

ai-office workflows start review-supplied-github-change \
  --run-id "$RUN_ID" \
  --run-input "$RUN_INPUT" \
  --run-store "$RUN_ROOT" \
  --preview-only
```

Inspect the preview's employee, step, execution target, and request fingerprint. A
preview alone is not approval to execute. Step 1 and Step 2 each require their own
business and execution approvals bound to that step's exact fresh preview. Step 2 is
only explicitly continued after Step 1 has succeeded and its persisted handoff has been
inspected. The Run input snapshot is immutable for that Run.

## 4. AI execution (separate human authorization required)

Do not execute either step as part of offline validation. An authorized live AI run can
load a provider credential and incur a charge. If separately authorized, bind each
step's approvals to the current preview with the standard `workflows start` and
`workflows continue` commands and their `--expected-*` options. Never put credentials
in the Run input, snapshot, approval IDs, logs, or Artifact. Do not retry, fallback,
recover, or continue automatically. Stop after any failure, ambiguous outcome, stale
revision warning, or evidence/approval mismatch.

## 5. Inspect the result (provider-free)

After an explicitly authorized successful run, these reads do not contact GitHub or an
AI provider and do not execute another Workflow step:

```bash
ai-office workflows result "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows approval-evidence "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows execution-evidence "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows artifacts "$RUN_ID" --run-store "$RUN_ROOT"
```

Confirm the Artifact is provenance-linked to the successful terminal execution before
reading or exporting it. Keep exported review reports private; they contain Issue/PR
content and AI-generated assessments.

## M3 acceptance matrix

| Acceptance item | Status | Evidence / remaining limit |
| --- | --- | --- |
| Collect one accessible Issue/PR/CI view with source IDs and exact revisions | **verified (offline)** | Fake transport verifies GET-only collection, expected-head binding, stable head/base, source IDs, timestamp, and check runs. Actual repository access remains a separately reproducible operator action. |
| Wire the snapshot through the existing two-employee Markdown Artifact Workflow | **verified (offline)** | Focused test uses a synthetic provider transport and verifies handoff, approvals, and Artifact provenance; it does not establish real model quality. |
| Avoid implying code/diff inspection from changed-file metadata | **verified (offline)** | Snapshot marks `diff_content_included: false`; the actual step requests say correctness is unverified without the diff and require exact-head human inspection. The synthetic Artifact carries that warning. |
| Preserve partial collection and never present unavailable validation as passed | **verified (offline)** | Focused test passes a partial snapshot into both existing step requests and checks the partial/unknown report and review checklist; no new validation layer or execution boundary is added. |
| Surface missing permission/data, malformed responses, stale/changing heads, and timeline limit | **verified (offline)** | Focused tests cover HTTP 403, malformed JSON, stale expected head, changed head, no checks, and 100 unlinked timeline entries. Unobserved linkage stays `null`/unverified and collection is `partial`. |
| Prevent GitHub text from authorizing actions or provider calls; preserve approvals and evidence | **verified (offline)** | Workflow marks GitHub text untrusted; preview and missing-approval paths make zero synthetic AI credential/transport calls; exact per-step approvals gate the two synthetic calls. |
| Produce a provider-backed Japanese report and assess model quality | **requires authorized live run** | No external AI provider execution was performed; separate authorization is required. |

The focused test is a synthetic simulation, not evidence of real GitHub collection or
provider-backed model behavior. No paid/external AI request is needed for snapshot
collection, preview, validation, or provider-free Run/Artifact inspection.
