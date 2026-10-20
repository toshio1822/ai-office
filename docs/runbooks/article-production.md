# Bounded article-production Job

`workflows/article-production.yaml` is the sole checked-in article-production
workflow. Its `bounded_review` policy declares a finite, ordered reviewer set
plus the draft and revision steps; that declaration is the source of the Job
action plan. It currently selects two independent reviewers:

| Employee | Responsibility |
| --- | --- |
| `document-reviewer` | Whole-document writing quality and assignment fit: organization, reasoning, reader/medium fit, expression, omissions, and contradictions. It does not repeat an exhaustive source audit. |
| `evidence-auditor` | Claim-by-claim semantic audit of author-, speaker-, organization-, and source-attributed facts against the supplied immutable material. |

The Workflow selects the number, order, and specialties and carries the shared
review verdict contract. Each Employee supplies a reusable specialty. Run Input
supplies task-specific facts and requirements. The shared engine enforces
mechanical contracts and finite execution; it does not encode article-specific
truth rules or select a fixed reviewer count. Different media, audience, brief,
source material, and language belong in Run Input.

## What the mechanical checks establish

The shared review path validates the verdict and finding/check structure,
whether a cited assignment line and quoted Artifact text exist in the bound
inputs, and the Artifact/Run lineage. The bounded Job also enforces approvals,
send/revision limits, evidence, and safe stopping.

These are integrity and process guarantees, not semantic entailment. A real
quote, valid line reference, successful parser result, or model `PASS` does
not prove that the evidence supports the full subject, predicate, scope,
frequency, quantity, or outcome of a claim. Human acceptance remains necessary.

## Offline evaluation examples

The examples below are expected review judgments, not model responses and not
rules copied into the operational prompts. The offline contract test feeds
canned, source-linked review results through the existing mechanical
`_review_verdict` validator. It checks that valid positive and negative
findings can be represented and that exact citations/quotes are enforced; it
does **not** ask a model to judge the cases.

| Case | Expected judgment | Why |
| --- | --- | --- |
| Brief explicitly supplies the author's 20 years of engineering experience; the article states it. | PASS candidate | The attributed fact is supported. |
| Brief asks the article to explain a four-part way to classify unknowns; the article describes that method generally. | PASS candidate | A general explanation satisfies the topic without inventing biography. |
| The #764 failure case turns that same requested method into a statement that the author routinely uses it. | CHANGES_REQUIRED | The brief supports explaining the method, not the author's habit. The two saved reviews both returned PASS; the whole-document human review identified the unsupported attribution. |
| A clearly labeled hypothetical team example remains hypothetical. | PASS candidate | A hypothetical is not a claim about the author's history. |
| The same scenario is rewritten as a specific past client success not present in the source. | CHANGES_REQUIRED | A hypothetical does not support an autobiographical outcome. |
| A press release says downtime declined, but the draft adds a percentage absent from its source. | CHANGES_REQUIRED | The quantitative claim exceeds the supplied evidence. |
| A reader-facing guide contains supported facts but omits a required first action. | CHANGES_REQUIRED | This is a whole-document requirement omission for the quality reviewer. |

The unchanged #764 brief, draft, and both saved review Artifacts were used locally
as read-only root-cause material. Their stored content lengths and SHA-256
digests matched; none was rewritten or copied into this repository. Running the
existing parser against the two actual review Artifacts returns PASS for both.
That negative control is expected: the current parser checks mechanical
structure and exact text existence, not meaning.

The offline examples and existing fake-transport tests can validate prompt
assembly, format, citation binding, reviewer routing, bounded revision, and
stopping without provider requests. They cannot show that a real model now
detects the unsupported habit, nor that it avoids over-rejecting supported
first-person facts. **Improved real-model semantic quality remains unproven**
until a separately authorized evaluation measures both false negatives and
false positives.

## Reviewer count and send budget

For `N` declared reviewers (`N >= 1`) and at most `R` revisions, the normal
provider-send budget is `(R + 1) * (N + 1)`: one draft, `N` first-round
independent reviews, then `R` rounds of one revision plus `N` re-reviews. With
the shipped `R = 3` cap this is `8 / 12 / 16 / 20` sends for `N = 1 / 2 / 3 / 4`,
and more for larger sets. The count is a finite attempt budget, not a currency
limit. Every reviewer in a round evaluates the same immutable Run Input and the
same Artifact id/digest in its own independent Run; a single `PASS` never
cancels another reviewer's grounded findings. Only an all-`PASS` round yields a
`candidate`; any grounded `CHANGES_REQUIRED` aggregates into one revision, and
the new Artifact is re-reviewed by all declared reviewers.

## Offline preparation

1. Read the assignment and select the destination and model in a private,
   operator-managed registry. Never commit the registry or credentials.
2. Inspect the definition with `ai-office workflows plan article-production`
   and keep the Run store at a persistent owner-only path. A temporary Run store
   is not durable evidence.
3. Build a non-sensitive assignment file and choose a finite `--max-revisions`
   no larger than the definition's cap. The Job derives all action slots from
   the same source definition and fixes them in the Job scope before execution.
4. Use offline fake-transport tests for implementation verification. These do
   not prove model judgment or authorize a live provider call.

## Live-operation boundary

A human operator must separately authorize the Business purpose and the exact
Execution preview, including destination fingerprint, model, Run Input,
assignment digest, revision budget, and provider-attempt cap. An
`approved_by` string and the CLI's UID/TTY checks are intent signals, not
identity proof or an OS-level security boundary when OpenClaw and AI Office
share one UID. Supply credentials through the approved secret manager/environment,
not CLI arguments, repository files, Run store, Issues, or logs. Check the
current entitlement and billing setting before authorizing a live Job.

The operator command shape is:

```console
ai-office workflows bounded-job-run JOB_ID \
  --run-id RUN_ID \
  --workflow article-production \
  --assignment-file /private/path/assignment.txt \
  --run-store /private/path/ai-office-runs \
  --directory /path/to/ai-office/workflows \
  --employees-directory /path/to/ai-office/employees \
  --execution-target APPROVED_DESTINATION \
  --execution-destinations /private/path/destinations.yaml \
  --step-model write-draft=APPROVED_DRAFT_MODEL \
  --step-model review-quality=APPROVED_QUALITY_REVIEW_MODEL \
  --step-model review-evidence=APPROVED_EVIDENCE_REVIEW_MODEL \
  --step-model revise=APPROVED_REVISION_MODEL \
  --max-revisions 3
```

This document does not authorize or execute that command. The CLI records the
full source Workflow snapshot and derives a one-step execution view for each
independent Run. Every declared reviewer step gets its own digest-bound Run and
receives the latest immutable Artifact, not the writer's or another reviewer's
private session. Only an all-reviewer PASS stops early as a human-acceptance
candidate; any grounded CHANGES_REQUIRED uses only the pre-authorized finite
revision/re-review rounds. BLOCKED, invalid output, exhausted limits, or an
unknown send outcome stop without replay, fallback, or publication. The limit
counts attempts, not currency. Inspect the existing read-only Evidence after a
stop; do not rerun an ambiguous Job.

Provider HTTP transport has a 600-second socket-inactivity timeout for
connection and socket send/receive operations, including TLS and body reads.
It is not a total wall-clock deadline. A timeout after dispatch consumes the
attempt even when the remote outcome is unknown.
