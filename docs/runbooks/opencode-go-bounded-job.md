# OpenCode Go bounded document Job operator runbook

This is a Gate B procedure only. The command below is deliberately not run by
the offline implementation, tests, CI, or OpenClaw. Gate A proves the fixed
Job with fake transport only. Do not treat this document or an
`approved_by` label as human authorization.

## Shared-identity security limits

The CLI permits the same OS identity as OpenClaw, including UID 1001. It only
rejects root, mismatched real/effective UIDs, and non-TTY stdin; these checks
are not authentication or proof of human identity. A confirmation prompt and
`approved_by` value are operator intent signals, not an authorization or
isolation boundary. When OpenClaw and AI Office share an OS identity, there is
no strong OS-level separation of the API key or Job-launch authority from
OpenClaw. Do not claim otherwise.

For production or any Job whose evidence must remain available for later
audit, set `--run-store` to a persistent, owner-only location outside temporary
or automatically-cleaned directories. Operator-managed backups and retention
must preserve this evidence for the required audit period; do not assume the
Run store is backed up or durable when it is placed under `/tmp` or similar.

Keep the Run store and registry private to the operator, while recognizing
that filesystem permissions under a shared identity do not create separation
from OpenClaw. Provide `OPENCODE_API_KEY` through the approved secret
manager/environment injection; never put it in a command argument, file in the
repository, Run store, Issue, CI, or logs.

The human operator must also confirm the current OpenCode Go model entitlement
and available included allowance. `Use balance OFF` is already operator-
confirmed for this Issue; do not recheck it as part of Gate A. If the live
provider reports exhaustion or an ambiguous result, preserve evidence and
stop. Do not retry, recover, switch endpoint/model, or use another billing
path.

## One-shot procedure

1. Prepare a short, nonsensitive code-related investigation assignment in a
   private file. It should name only repository evidence the operator intends
   to provide. Do not include customer or personal data.
2. Verify the installed AI Office source matches the Draft PR head approved
   after ChatGPT review. Keep the configured destination registry
   administrator-owned and unchanged during the Job.
3. Provide the credential from the operator's secret manager to this process
   without echoing it, then run exactly once:

   ```console
   ai-office workflows bounded-job-run JOB_ID \
     --run-id RUN_ID \
     --assignment-file /private/path/code-investigation.txt \
     --run-store /private/path/ai-office-runs \
     --directory /path/to/ai-office/workflows \
     --employees-directory /path/to/ai-office/employees \
     --execution-target opencode-go-glm-chat \
     --execution-destinations /private/path/admin-openai-compatible-destinations.yaml \
     --execution-model glm-5.3-flash
   ```

4. Check the confirmation summary: endpoint must be
   `https://opencode.ai/zen/go/v1/chat/completions`, protocol
   `openai-chat-completions`, model `glm-5.3-flash`, provider
   `opencode-go-glm-chat`, exactly five pinned action slots, five-attempt cap,
   and one-revision cap. Type the exact Job ID only after deciding to authorize
   its Business grant, then type the same Job ID again to authorize the
   separate Execution grant and start this one Job.
5. Allow the command to finish once. A PASS after the initial review is a
   human-acceptance candidate, not publication approval. On any stop or
   ambiguous outcome, inspect the existing read-only evidence command; do not
   rerun the Job.

The synchronous provider HTTP transport applies a 30-second socket timeout to
connection and socket send/receive operations, including TLS and response-body
reads. This is an inactivity limit per socket operation, not a total wall-clock
deadline: DNS/public-address lookup happens before the HTTPS socket is opened,
and operating-system name resolution or a peer that continually makes progress
may take longer. If the timeout is reached after dispatch, the remote outcome
is unknown. The Attempt remains consumed; no retry, fallback, or recovery is
performed, and no complete response or Artifact is inferred from missing data.

The fixed sequence is creator (2 attempts), independent reviewer (1), then
only for a persisted evidence-grounded `CHANGES_REQUIRED`, revision (1) and
fresh independent reviewer (1). All actions pin the same destination
fingerprint. A provider attempt is not a currency cap; do not describe it as
one. No automated publication, repository write, device operation, or generic
tool execution is part of this Job.

Passing the CLI's UID/TTY checks does not prove human identity or isolate
credentials. The human operator owns credential injection, final endpoint and
entitlement checks, exact non-sensitive assignment, and separate
authorization for the single live Job.
