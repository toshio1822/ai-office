# Independent Artifact review Workflow

`review-supplied-artifact` is a one-step, domain-neutral reviewer for an
immutable deliverable supplied as Run Input. It reuses `general-researcher`,
the normal one-step Run/preview and Business/Execution Approval path, and the
existing `text/markdown` Artifact persistence. The Run Input carries the
requirements, exact Artifact content and digest, and independently
attributable evidence; reusable Workflow YAML contains no project-specific
review data.

## Validate and preview the #731 input

Use the exact private input prepared by #731; do not copy it into the
repository or replace its original evidence. The following preview uses the
existing sample destination registry and makes no credential read or provider
request:

```console
ai-office employees validate
ai-office workflows validate
ai-office workflows plan review-supplied-artifact
ai-office workflows request review-supplied-artifact 1
ai-office workflows start review-supplied-artifact \
  --run-id issue-732-review-preview-20261010 \
  --run-input-file /tmp/ai-office-issue731-review-preflight-20261010/review-input.json \
  --run-store /tmp/ai-office-issue732-preview-runs \
  --execution-target opencode-go \
  --execution-destinations docs/examples/openai-compatible-destinations.yaml \
  --execution-model gpt-6-luna \
  --preview-only
```

Check that the preview names the `review` step and `general-researcher`,
retains the exact Run Input and its Artifact/evidence identifiers, and reports
the selected destination/model, Business and Execution Approval requirements,
and request fingerprint. The current preview JSON exposes
`business_approval_required`; the existing execution path separately requires
Execution Approval for this Step. A preview is not a review result and does
not persist a Run or Artifact.

## Isolation boundary

The existing invocation renders the supplied Run Input with the employee and
step instructions into a new request. It does not import the creator's
conversation transcript, hidden reasoning, or a previous provider response
identifier. This definition asks for no tools and the new Run has its own
identity; with the existing Run-scoped destination session header, that
identity also differs from the source Run. This is evidence about the request
AI Office constructs, not a guarantee about undocumented provider-side
retention or model behavior. The preview cannot establish review quality.

The Workflow performs review only. It does not revise the source Artifact,
start another Run, or loop into a correction/re-review cycle. Such a live
review or revision round requires a separate decision and approval.
