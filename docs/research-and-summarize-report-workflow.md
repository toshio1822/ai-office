# Research-and-summarize technical report Workflow

`research-and-summarize` accepts a supplied evidence brief, asks
`general-researcher` to organize source-bounded findings, then asks
`japanese-document-writer` for a review-ready Japanese Markdown report. On
successful completion of the final Step, the existing Artifact mechanism stores
the exact `text/markdown` output with Run and execution-evidence provenance.
The Workflow is topic-neutral: product-specific facts and constraints belong in
the Run input, not in the shared definition.

## Validate and inspect

```console
ai-office employees validate
ai-office workflows validate
ai-office workflows plan research-and-summarize
ai-office workflows request research-and-summarize 1
ai-office workflows request research-and-summarize 2
```

Use a new Run ID and an evidence file kept outside the repository when the input
contains private material. A preview reads the input but does not create a Run,
load credentials, or contact a provider:

```console
ai-office workflows start research-and-summarize \
  --run-id <new-run-id> \
  --run-input-file <evidence-file> \
  --run-store <private-run-store> \
  --execution-target <approved-target> \
  --execution-destinations <approved-destination-registry> \
  --execution-model <allowlisted-model> \
  --preview-only
```

Review the exact input, step, employee, model, destination, and request
fingerprint. Only proceed after separate Business and Execution Approvals have
been granted for this exact Step preview. The CLI approval flags record those
decisions; they do not obtain or imply approval. Pass the preview's exact
`step_id`, `step_index`, `employee_id`, and `request_fingerprint` as the
`--expected-*` values. A changed input, definition, destination, or model
requires a fresh preview and approvals.

The execution form for Step 1 is:

```console
ai-office workflows start research-and-summarize \
  --run-id <same-run-id> \
  --run-input-file <same-evidence-file> \
  --run-store <private-run-store> \
  --execution-target <same-approved-target> \
  --execution-destinations <same-approved-destination-registry> \
  --execution-model <same-allowlisted-model> \
  --approve-business \
  --business-approved-by <business-approver> \
  --business-approval-id <step-1-business-approval-id> \
  --approve-execution \
  --execution-approved-by <execution-approver> \
  --execution-approval-id <step-1-execution-approval-id> \
  --expected-step-id <step-1-preview-step-id> \
  --expected-step-index <step-1-preview-index> \
  --expected-employee-id <step-1-preview-employee-id> \
  --expected-request-fingerprint <step-1-preview-fingerprint>
```

## Run one approved Step at a time

Execute Step 1 once using the same Run ID, input, target, registry, model, and
approved preview values. Do not retry or switch provider/model on failure.
Continue only if it succeeds and reports that the next Step is ready.

Preview Step 2 with the persisted Run:

```console
ai-office workflows continue <run-id> \
  --run-store <private-run-store> \
  --execution-target <same-approved-target> \
  --execution-destinations <same-approved-destination-registry> \
  --preview-only
```

Check that the preview's `upstream_inputs` contain the actual Step 1 output and
its provenance. Obtain new Business and Execution Approvals for this Step 2
preview; do not reuse Step 1's approvals. Then execute Step 2 exactly once with
those approvals and the preview's exact expected values. Do not pass a new input
or silently replace the predecessor output.

```console
ai-office workflows continue <run-id> \
  --run-store <private-run-store> \
  --execution-target <same-approved-target> \
  --execution-destinations <same-approved-destination-registry> \
  --approve-business \
  --business-approved-by <business-approver> \
  --business-approval-id <step-2-business-approval-id> \
  --approve-execution \
  --execution-approved-by <execution-approver> \
  --execution-approval-id <step-2-execution-approval-id> \
  --expected-step-id <step-2-preview-step-id> \
  --expected-step-index <step-2-preview-index> \
  --expected-employee-id <step-2-preview-employee-id> \
  --expected-request-fingerprint <step-2-preview-fingerprint>
```

On a failure or ambiguous provider outcome, stop and inspect the persisted
result and execution/approval evidence. Recovery, if appropriate, is a separate
explicit decision with its own required approval; it is not a retry. No Step is
executed automatically.

## Inspect and export the Artifact

After successful completion, inspect the result and verified Artifact metadata:

```console
ai-office workflows result <run-id> --run-store <private-run-store>
ai-office workflows artifacts <run-id> --run-store <private-run-store>
```

Read the selected Artifact or export it once to a new destination that does not
already exist:

```console
ai-office workflows artifact <run-id> <artifact-id> \
  --run-store <private-run-store>
ai-office workflows artifact-export <run-id> <artifact-id> \
  --run-store <private-run-store> \
  --output <new-report.md>
```

Review the report against the supplied evidence before relying on it. An
Artifact proves which successful output was recorded; it does not independently
verify the report's claims.
