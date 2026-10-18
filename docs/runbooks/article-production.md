# Bounded article-production Job

`workflows/article-production.yaml` is the sole checked-in article-production
workflow. Its three steps (draft, independent review, revision) and bounded
review policy are the source of the Job action plan. Different media, audience,
brief, source material, and language requirements belong in each Run Input;
they do not require another checked-in Workflow. The configured Employees supply
writing and review capabilities.

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
independent Run. The review Run receives the digest-bound latest Artifact, not
the writer's private session. A PASS stops early as a human-acceptance
candidate; CHANGES_REQUIRED uses only pre-authorized revision/re-review pairs.
BLOCKED, invalid output, exhausted limits, or an unknown send outcome stop
without replay, fallback, or publication. The limit counts attempts, not
currency. Inspect the existing read-only Evidence after a stop; do not rerun an
ambiguous Job.

Provider HTTP transport has a 600-second socket-inactivity timeout for
connection and socket send/receive operations, including TLS and body reads.
It is not a total wall-clock deadline. A timeout after dispatch consumes the
attempt even when the remote outcome is unknown.
