# Independent Artifact review Workflow

`review-supplied-artifact` is a one-step, domain-neutral reviewer for an
immutable deliverable supplied as Run Input. It reuses `general-researcher`,
the normal one-step Run/preview and Business/Execution Approval path, and the
existing `text/markdown` Artifact persistence. The Run Input carries the
requirements, exact Artifact content and digest, and independently
attributable evidence; reusable Workflow YAML contains no project-specific
review data.

## Separate requirement scope from Artifact verdict

Keep the original Issue body and exact Artifact/evidence identities unchanged
in the private Run Input. Merge the top-level `criterion_scope` property from
[`review-scope-issue-726.json`](../examples/review-scope-issue-726.json)
directly into that input; do not flatten its `criteria` array or rename the
property. This is the same shape used by the offline handoff test. The map is
guidance for the existing reviewer prompt, not a new engine schema or
validator. The example classifies the original #726 criteria:

- AC2 is the Japanese engineering-report content evaluated by the Artifact
  verdict.
- AC1 and AC3 are evaluation/reporting conditions. Check them against
  separately attributable Run/evaluation records when supplied; do not require
  the technical report to repeat its approvals or critique its own generating
  Workflow.
- AC4 is an operational condition. Check it against execution, stop, and
  publication evidence, not against report sections.

The example preserves the original criterion text and exact #726 Run, Artifact,
and evidence-package identities, but intentionally omits the private full
Artifact, evidence contents, and execution records. Add its `criterion_scope`
value to the original private review input without changing that input's
original Issue body, Artifact bytes, or evidence package. If an
evaluation/operational record is absent, report that criterion as unverified;
do not turn that gap into an Artifact-content finding. Preserve ambiguous
scope rather than retroactively rewriting the source Issue.

## Validate and preview the #731 input

Use a private copy of the exact input prepared by #731 with the
`criterion_scope` property from the example merged at the top level. Keep the
original Issue body, Artifact bytes, and evidence package exact; do not check
the full input into the repository or replace its evidence. The following
preview uses the existing sample destination registry and makes no credential
read or provider request:

```console
ai-office employees validate
ai-office workflows validate
ai-office workflows plan review-supplied-artifact
ai-office workflows request review-supplied-artifact 1
ai-office workflows start review-supplied-artifact \
  --run-id issue-734-review-scope-preview-20261010 \
  --run-input-file /tmp/ai-office-issue734-review-scope-20261010/review-input.json \
  --run-store /tmp/ai-office-issue734-preview-runs \
  --execution-target opencode-go \
  --execution-destinations docs/examples/openai-compatible-destinations.yaml \
  --execution-model gpt-6-luna \
  --preview-only
```

Check that the preview names the `review` step and `general-researcher`,
retains the exact augmented Run Input, the per-criterion scope map, and the
original Artifact/evidence identifiers, and reports
the selected destination/model, Business and Execution Approval requirements,
and request fingerprint. The current preview JSON exposes
`business_approval_required`; the existing execution path separately requires
Execution Approval for this Step. A preview is not a review result and does
not persist a Run or Artifact.

The Artifact verdict applies to content requirements and evidence-grounded
content defects. Evaluation/reporting and operational criteria belong in a
separate assessment section, backed only by supplied attributable records.
Missing run evidence is unverified, not a defect in the Artifact, and does not
by itself change its verdict. `BLOCKED` is for cases where the Artifact review
itself cannot be completed reliably from the supplied content/evidence.

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
