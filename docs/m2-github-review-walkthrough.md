# M2 two-employee GitHub review proof

This walkthrough demonstrates Issue #712 with the existing AI Office engine. Two
distinct employees turn a supplied, fictional GitHub Issue/PR/CI snapshot into a
traceable Japanese review report. It adds no engine Phase, adapter, wrapper, GitHub
integration, or production runtime code.

## Read-only gap assessment

The existing public CLI and behavioral tests were inspected from baseline
`5fb58edf4220fe56b99a0895250defb4ae60d22c`.

- Already supported: validated text Employee/Workflow definitions, immutable per-Run
  input, explicit one-step-at-a-time `start` and `continue`, immediate-predecessor
  output handoff with provenance, request-bound business/execution approvals, durable
  state/events/evidence, stop-on-failure behavior, final Markdown Artifact creation,
  provider-free reads, no-overwrite export, and explicit recovery.
- Documentation/sample gap: there was no checked-in two-role GitHub review scenario,
  sanitized snapshot, operator walkthrough, or sample-specific offline proof.
- Runtime gap: none found. Production code does not change.
- Authorization gap: real model quality, provider behavior, repository contents, PR
  diff, and live CI state cannot be verified from the offline fixture.
- Out of scope: GitHub API/tool execution, search, automatic retry/fallback, implicit
  continuation, GUI work, publication, and a generic GitHub adapter.

## Definitions and data boundaries

- `employees/general-researcher.yaml` is Employee A. Its reusable system policy
  separates confirmed facts from assumptions.
- `employees/japanese-document-writer.yaml` is Employee B. Its reusable system policy
  produces review-ready Japanese Markdown without inventing facts.
- `workflows/review-supplied-github-change.yaml` contains only this scenario's analysis
  and report-writing instructions.
- `examples/m2-github-review/supplied-snapshot.md` is fictional per-Run business input.
- The second-step preview exposes the exact first-step output and provenance under
  `upstream_inputs`. The report step is instructed to use that handoff as its review
  basis rather than fabricate or replace it.

The checked-in test replaces credential loading and provider transport with in-process
synthetic functions. A successful test is a simulation, not a provider-backed result or
evidence that GitHub/CI was inspected live.

## 1. Validate and inspect definitions (offline)

From the repository root:

```bash
python -m pip install -e '.[dev]'
ai-office employees validate
ai-office workflows validate
ai-office workflows plan review-supplied-github-change
ai-office workflows request review-supplied-github-change 1
ai-office workflows request review-supplied-github-change 2
```

These commands do not create a Run, load credentials, contact a provider, or contact
GitHub.

## 2. Preview Employee A (offline)

Use a fresh Run ID and keep generated evidence outside the repository:

```bash
RUN_ID=m2-github-review-001
RUN_ROOT=/tmp/ai-office-m2-runs
RUN_INPUT="$(<examples/m2-github-review/supplied-snapshot.md)"

ai-office workflows start review-supplied-github-change \
  --run-id "$RUN_ID" \
  --run-input "$RUN_INPUT" \
  --run-store "$RUN_ROOT" \
  --preview-only
```

Save the JSON output. Confirm `step_id=analyze-supplied-snapshots`,
`employee_id=general-researcher`, the intended execution target, and the request
fingerprint. Preview freezes the would-be Manifest only in memory and writes nothing.

## 3. Execute Employee A (requires separately authorized live run)

Do not run this section during offline validation. It may load a credential, contact the
configured provider, and incur a charge. After separate human authorization, bind both
approvals to the exact saved preview:

```bash
ai-office workflows start review-supplied-github-change \
  --run-id "$RUN_ID" \
  --run-input "$RUN_INPUT" \
  --run-store "$RUN_ROOT" \
  --approve-business \
  --business-approved-by '<business approver>' \
  --business-approval-id '<business approval record>' \
  --approve-execution \
  --execution-approved-by '<execution approver>' \
  --execution-approval-id '<execution approval record>' \
  --expected-step-id '<preview step_id>' \
  --expected-step-index '<preview step_index>' \
  --expected-employee-id '<preview employee_id>' \
  --expected-request-fingerprint '<preview request_fingerprint>'
```

A success returns `prepare_next_step`; it does not execute Employee B implicitly.

## 4. Preview the exact handoff (offline after step 1 exists)

```bash
ai-office workflows continue "$RUN_ID" \
  --run-store "$RUN_ROOT" \
  --preview-only
```

Save this new JSON output. Confirm `step_id=write-japanese-review-report`,
`employee_id=japanese-document-writer`, and exactly one `upstream_inputs` item naming
Employee A's step. Inspect its `output_text` and `sha256`; this is the actual persisted
Employee A result included in Employee B's request fingerprint. Preview performs no
credential or provider call.

## 5. Execute Employee B (requires separately authorized live run)

Do not run this section during offline validation. After separately authorizing the
second business step and provider call, bind execution to the step-2 preview:

```bash
ai-office workflows continue "$RUN_ID" \
  --run-store "$RUN_ROOT" \
  --approve-business \
  --business-approved-by '<business approver>' \
  --business-approval-id '<business approval record>' \
  --approve-execution \
  --execution-approved-by '<execution approver>' \
  --execution-approval-id '<execution approval record>' \
  --expected-step-id '<step-2 preview step_id>' \
  --expected-step-index '<step-2 preview step_index>' \
  --expected-employee-id '<step-2 preview employee_id>' \
  --expected-request-fingerprint '<step-2 preview request_fingerprint>'
```

Missing approvals, changed handoff content, or preview mismatch stops before transport.
An accepted success returns `workflow_complete` and creates the configured Markdown
Artifact from the final step only.

## 6. Inspect evidence and Artifact (provider-free)

```bash
ai-office workflows result "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows approval-evidence "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows execution-evidence "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows artifacts "$RUN_ID" --run-store "$RUN_ROOT"

ARTIFACT_ID='<artifacts artifact_id>'
ai-office workflows artifact "$RUN_ID" "$ARTIFACT_ID" --run-store "$RUN_ROOT"
ai-office workflows artifact-export "$RUN_ID" "$ARTIFACT_ID" \
  --run-store "$RUN_ROOT" \
  --output "/tmp/${RUN_ID}.md"
```

The evidence should show two execution attempts and four approvals (business and
execution for each step). Artifact metadata links the final report to its Manifest,
attempt, normalized/raw response evidence, and terminal success event. Reads never
execute again, and export refuses to overwrite an existing file.

## 7. Failure stop and recovery

If Employee A fails, `result` reports `persisted_failure`; `continue` does not start
Employee B and no final Artifact exists. There is no implicit retry, fallback, or
continuation.

```bash
ai-office workflows result "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows execution-evidence "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows artifacts "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows recovery "$RUN_ID" --run-store "$RUN_ROOT"
```

`recovery` is read-only. Any `recover` action is separate, explicitly approved, bound to
the current evidence digest, and may require a fresh execution approval. Preserve the Run
directory for investigation when recovery is unsafe or unavailable.

## Offline proof command

```bash
pytest -q tests/test_m2_github_review.py
```

The focused test validates the checked-in definitions, proves incomplete approval makes
zero credential/transport calls, executes both steps through the public CLI with exactly
two synthetic transport calls, inspects the exact handoff, verifies approvals/evidence,
reads and exports the final Artifact without re-execution, verifies no-overwrite export,
and proves an Employee-A failure cannot reach Employee B or a successful Artifact.

## M2 acceptance matrix

| Acceptance item | Status | Evidence |
| --- | --- | --- |
| Two distinct roles run in order; Employee B consumes Employee A's actual output | **verified (offline)** | `start`/`continue --preview-only`; focused test asserts exact persisted `upstream_inputs` and two ordered synthetic calls |
| Japanese report identifies Issue/PR, requirements, changes, test evidence, risks, unknowns, and source references | **verified (offline)** | final synthetic Markdown Artifact assertions and fixture-linked source identifiers in the focused test |
| Missing/invalid upstream evidence or failed first step stops safely | **verified (offline)** | existing handoff compatibility tests plus focused first-step-failure simulation; zero Employee-B call and zero Artifact |
| Approval boundaries, execution evidence, handoff, Artifact read/export remain observable | **verified (offline)** | focused CLI proof: four approvals, two attempts, provenance-linked Artifact, provider-free reads, no-overwrite export |
| Existing M1 behavior and regressions remain compatible | **verified (offline)** | M1 focused test, related workflow/handoff/Artifact suites, and full pytest |
| Offline operation and remaining live gaps are documented | **verified (offline)** | this walkthrough and its separately labeled live-only sections |
| Real model output quality and provider behavior | **requires authorized live run** | Sections 3 and 5; not performed |
| Actual GitHub Issue/PR diff, repository content, branch protection, and live CI state | **requires authorized live run** | requires separately authorized GitHub access; the fixture explicitly marks these unknown |

No paid provider request or live GitHub scenario request is part of this proof.
