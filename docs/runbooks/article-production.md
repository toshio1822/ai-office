# Bounded article-production Job

`workflows/article-production.yaml` is the sole checked-in article-production
workflow. Its `bounded_review` policy declares a finite, ordered reviewer set
plus the draft and revision steps; that declaration is the source of the Job
action plan. The shipped workflow declares **two** independent reviewers
(article quality/requirement fit via `document-reviewer`, and author-attributed
evidence/fact audit via `evidence-auditor`), but the number, order and
specialties are configuration only. The shared engine reads the reviewer count
from the declared set and fixes no reviewer number in code, so adding or
removing reviewers is a Workflow/Employee change with no Python change.
Different media, audience, brief, source material, and language requirements
belong in each Run Input; they do not require another checked-in Workflow. The
configured Employees supply the writing and review capabilities.

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
assignment digest, revision budget, and provider-attempt cap. An `approved_by`
string and the CLI's UID/TTY checks are intent signals, not identity proof or
an OS-level security boundary when OpenClaw and AI Office share one UID. Supply
credentials through the approved secret manager/environment, not CLI arguments,
repository files, Run store, Issues, or logs. Check the current entitlement and
billing setting before authorizing a live Job.

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
  --execution-model APPROVED_MODEL \
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
