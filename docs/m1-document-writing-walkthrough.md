# M1 document-writing proof

This walkthrough proves the Milestone 1 document-writing path with the existing AI
Office engine. It does not add an engine Phase or a new runtime boundary.

## Read-only gap assessment

Before changing the repository, the existing public CLI and behavioral tests were
inspected from baseline `805798bf8502bafd0e912f6943fd65f0353ba681`.

- Already supported: strict Employee/Workflow validation, immutable Run identity and
  pinned definitions/input, read-only preview with request identity and execution target,
  separate business/execution approvals, single-step execution, durable evidence,
  terminal result reconstruction, Markdown Artifact provenance, provider-free read/export,
  and explicit recovery without automatic retry.
- Documentation/sample gap: there was no realistic document-writing definition or one
  operator walkthrough connecting those existing commands.
- Runtime gap: none found for this proof, so production code does not change.
- Authorization gap: actual provider behavior and output quality cannot be verified
  offline and require a separately authorized live run.
- Out of scope: external tools, web search, GUI, and multiple AI employees.

## Scope and safety

- `employees/japanese-document-writer.yaml` contains reusable writing policy.
- `workflows/write-internal-ai-guide.yaml` contains the task-specific step.
- `--run-input` is the business brief for one immutable, independently identified Run.
- Validation and preview are offline. They do not load a credential, contact a provider,
  or incur a charge.
- The execution command shown below is **not approved by this document**. It may contact
  the configured OpenAI endpoint and incur a charge. Run it only after separate explicit
  authorization from the responsible human.
- The checked-in test uses a synthetic in-process transport. Its successful result is a
  simulation, not evidence of a real provider-backed run.

## 1. Validate the reusable definitions (offline)

From the repository root:

```bash
python -m pip install -e '.[dev]'
ai-office employees validate
ai-office workflows validate
ai-office workflows plan write-internal-ai-guide
ai-office workflows request write-internal-ai-guide 1
```

The Employee instructions and Workflow step instructions are displayed separately. No
Run is created by these commands.

## 2. Create an independent Run preview (offline)

Choose a new Run ID for every independent assignment. Keep the Run store outside the
repository so generated evidence is not mistaken for source:

```bash
RUN_ID=m1-ai-guide-001
RUN_ROOT=/tmp/ai-office-m1-runs
BRIEF='対象は全社員。顧客の秘密情報と個人情報は入力禁止。下書き・要約・翻訳は利用可。外部公開前に部門責任者の確認が必要。事故時は情報システム部へ報告する。'

ai-office workflows start write-internal-ai-guide \
  --run-id "$RUN_ID" \
  --run-input "$BRIEF" \
  --run-store "$RUN_ROOT" \
  --preview-only
```

Save the one-line JSON output. Confirm at least:

- `mode` is `preview` and `status` is `step_ready`;
- `run_id`, `run_input`, `workflow_id`, `step_id`, and `employee_id` are correct;
- `execution_target` names the intended endpoint and credential environment variable;
- `request_fingerprint` is present;
- `business_approval_required` is `true`;
- reusable `system_instructions` and task-specific `task_instructions` are distinct.

Preview freezes the would-be Run Manifest in memory to calculate the displayed identity,
but writes nothing. The approved execution path durably commits that exact Manifest before
credential loading or provider transport, then creates state/events. A second assignment
must use a different Run ID; reusing an ID with different definitions or input fails closed.

## 3. Explicit execution approval (requires separately authorized live run)

Do not run this section during offline validation. After a responsible human separately
authorizes both the business step and the paid provider call, copy the exact `step_id`,
`step_index`, `employee_id`, and `request_fingerprint` from the saved preview:

```bash
# OPENAI_API_KEY must already be set by an approved secret-handling method.

ai-office workflows start write-internal-ai-guide \
  --run-id "$RUN_ID" \
  --run-input "$BRIEF" \
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

Missing approvals, incomplete approval metadata, or any mismatch with the preview stops
before credential loading or transport. There is no implicit approval, retry, or
continuation. The sample has one step, so an accepted success is terminal.

## 4. Inspect durable status, evidence, result, and Artifact (provider-free)

These commands read the persisted Run and do not call a provider:

```bash
ai-office workflows result "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows approval-evidence "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows execution-evidence "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows artifacts "$RUN_ID" --run-store "$RUN_ROOT"
```

Take `artifact_id` from `artifacts`, then verify the Markdown and export its exact bytes to
a new path:

```bash
ARTIFACT_ID='<artifacts artifact_id>'
ai-office workflows artifact "$RUN_ID" "$ARTIFACT_ID" --run-store "$RUN_ROOT"
ai-office workflows artifact-export "$RUN_ID" "$ARTIFACT_ID" \
  --run-store "$RUN_ROOT" \
  --output "/tmp/${RUN_ID}.md"
```

`artifacts` reports the content digest and links the Artifact to the Run Manifest,
execution attempt, normalized result, raw-response digests, and terminal success event.
`artifact-export` refuses to overwrite an existing destination.

## 5. Failure and recovery

A provider/API/transport failure is persisted as a failed result and is not retried.
Inspect it without execution:

```bash
ai-office workflows result "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows execution-evidence "$RUN_ID" --run-store "$RUN_ROOT"
ai-office workflows recovery "$RUN_ID" --run-store "$RUN_ROOT"
```

`recovery` is read-only and returns a decision bound to the current durable evidence.
`recover` is a separate explicit action requiring the exact decision digest and explicit
recovery approval; some recovery routes that execute again also require a fresh execution
approval. Never infer success or retry an ambiguous attempt. Preserve the Run directory
for investigation when recovery reports it is unsafe or unavailable.

## Offline proof command

The focused M1 test runs the checked-in sample through the public CLI with a synthetic
Markdown response and zero network access. It verifies preview identity, approval
separation, one transport call, persisted completion, approval/execution evidence, and
provider-free Artifact read/export:

```bash
pytest -q tests/test_m1_document_writing.py
```

## M1 acceptance matrix

| Acceptance item | Status | Evidence |
| --- | --- | --- |
| Reusable Employee/Workflow definitions and distinct per-Run input | **verified (offline)** | `employees validate`, `workflows validate`, `workflows plan`, `workflows request`, and the focused M1 test |
| New independent Run, execution-target/request preview, and explicit authorization | **verified (offline)** | `workflows start --preview-only`; the focused test proves incomplete approval makes zero credential/transport calls |
| Persisted Markdown Artifact with safe read/export | **verified (offline)** | focused fake-transport test plus `artifacts`, `artifact`, and `artifact-export` |
| Real model quality and provider-backed end-to-end result | **requires authorized live run** | Section 3; requires separately approved credential use and possible charge |
| Status, approval/execution evidence, result, and Artifact inspection | **verified (offline)** | Section 4 commands and focused M1 test |
| Failure stops without retry; recovery is explicit | **verified (offline)** | existing CLI/runtime tests and Section 5; no live failure injection is needed |
| External tools, web search, GUI, and multiple employees | **blocked (out of scope)** | Explicitly excluded by Issue #704; the sample has `allowed_tools: []` and one employee |

There is no engine capability gap for this M1 proof. The only unverified item is actual
provider execution and output quality, which intentionally remains behind separate human
authorization.
