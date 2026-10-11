# Revise Reviewed Artifact Workflow

`revise-reviewed-artifact` is a generic, one-step limited-revision Workflow.
It reuses `japanese-document-writer`, normal Run Input and approval gates, and
the existing `text/markdown` Artifact persistence. It does not research,
review, continue to another step, or loop. The Workflow is reusable and
contains no Issue-specific identity or private Artifact content.

## Prepare a private Run Input

Use read-only Artifact and Run readers to recover the exact immutable source
report and Review Artifact. Verify their IDs, content types, byte counts and
digests against their saved records before preparing the input. Include:

- source Run and source Report Artifact IDs/digests plus the exact report text;
- independent review Run and Review Artifact IDs/digests plus its accepted
  findings;
- original requirements and a clear indication of which accepted findings
  are actionable report-content corrections;
- evidence-package identity/digest and only the relevant frozen evidence
  contents, preserving provenance and limitations.

Keep the Run Input owner-private and outside the repository. The existing Run
input and Artifact records provide the lineage from source report and review
to the new revision Artifact; do not add private identifiers to the published
report merely for tracking. Do not change or re-export over either source
Artifact. If an identity, digest, or evidence binding does not match, stop.

The Writer should preserve supported content and make only the minimum
accepted, evidence-supported content correction. For evidence gaps, keep the
claim unresolved. In particular, do not infer call paths, ownership, runtime
success, or root cause and do not recommend broad or destructive cleanup.
Evaluation/reporting and operational criteria are not report edits.

## Validate and preview

Use a new Run ID, an exact private input file, and the selected destination and
model. Example preview command (replace the private input path and Run ID):

```console
ai-office employees validate
ai-office workflows validate
ai-office workflows plan revise-reviewed-artifact
ai-office workflows request revise-reviewed-artifact 1
ai-office workflows start revise-reviewed-artifact \
  --run-id issue-revision-preview-20261011 \
  --run-input-file /tmp/private-revision/revision-input.json \
  --run-store /tmp/private-revision/preview-runs \
  --execution-target opencode-go \
  --execution-destinations docs/examples/openai-compatible-destinations.yaml \
  --execution-model gpt-6-luna \
  --preview-only
```

Replace the example Run ID and private path with a fresh ID and the
owner-only input/output locations created for the evaluation; the example
paths are placeholders, not checked-in data.

Confirm the preview retains the complete intended Run Input and reports the
new Run ID, `revise` step, `japanese-document-writer`, destination/model,
Business Approval requirement, request fingerprint and manifest identity.
The existing execution path separately requires Execution Approval. A
preview creates neither a persisted Run nor Artifact and does not call a
provider. Before any live execution, obtain distinct Business and Execution
Approvals bound to the exact fresh preview and authorized provider/model/cost
scope. This Workflow does not authorize a live call, retry, fallback, model
switch, automatic continuation, re-review, or publication.

An offline synthetic-transport test demonstrates input delivery and existing
new-Artifact persistence only; it does not establish that a live model will
preserve report quality or make a correct revision.
