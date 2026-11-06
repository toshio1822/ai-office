# Verified Artifact → Draft PR

`ai-office workflows draft-pr` delivers the terminal Artifact of an existing
bounded Job. It is a normal local CLI feature and does not depend on OpenClaw.
It reuses the Job's all-reviewers-PASS terminal record, exact Run Artifact,
local-verification authorization and Evidence. It does not run a Workflow,
call a model, or invoke another publisher service.

## Preconditions

- Finish a bounded Job with status `candidate` or `reviewed` and verify its
  final `file-change-set.v1` Artifact through the existing local-effect flow.
- Use a local Git checkout matching the repository path pinned in that Run's
  assignment. The checkout must contain the pinned base commit and a Git remote
  whose push URL is the requested `github.com` repository.
- Authenticate with the locally installed GitHub CLI (`gh auth login`) before
  running this command. The command only checks `gh auth status`; it never
  starts login or creates credentials.

## Deliver

```bash
ai-office workflows draft-pr JOB_ID \
  --run-store /path/to/runs \
  --git-repository /path/to/checkout \
  --repository OWNER/REPOSITORY \
  --base-ref main \
  --branch ai-office/JOB_ID
```

For an engineering Issue Job created by `bounded-job-run --issue-url`, the Job
and its pinned repository/base are resolved from the Issue URL. This avoids
hand-copying Job, repository, base, branch, or checkout values:

```bash
ai-office workflows draft-pr \
  --issue-url https://github.com/OWNER/REPOSITORY/issues/NUMBER \
  --run-store /path/to/issue-job-runs
```

This Issue mode still requires the same explicit interactive confirmation (or
the deliberate `--yes` operator-intent flag) before Git/GitHub writes. The
Issue's repository identity, exact existing Job, Artifact, review Artifact,
verification Evidence, and pinned checkout must agree; missing or ambiguous
Job evidence stops before effects.

The CLI re-reads the final Job/Artifact/Evidence, verifies the exact diff
against the pinned base, checks repository identity and base SHA, and checks
that the target branch and PR do not already exist. It then displays the
repository, base SHA, new branch, protected-branch status, effect summary and
verified diff. Confirm interactively to proceed.

For an explicitly scripted invocation, provide all target fields and pass
`--yes`. This records operator intent only; it is not an identity or security
boundary. The OS user running the command retains the GitHub write authority
of its local Git/`gh` credentials. If OpenClaw or an AI process runs as that
same OS user and can access those credentials, this feature does not isolate
GitHub permissions from it.

The command creates a commit from the Artifact bytes with a temporary Git
index; it does not check out or alter the existing worktree or index. It uses
standard `git push` to create a new branch, then `gh pr create --draft`. It
never updates an existing remote branch, force-pushes, rebases, makes the PR
ready, merges, or closes an Issue. Git hooks and external diff/text conversion
are disabled for the delivery Git commands. No credential value is copied to
the Run store, PR body, or command output.

The PR and Run store carry the Job, Artifact, review Artifact, verification
Evidence, base, commit/tree, changed paths and diff digests. A minimal attempt
record is written before the first remote write. If an effect result is
ambiguous, repeat invocation performs read-only comparison only; it returns a
Draft PR only when the exact branch, commit and PR can be proven, otherwise it
stops without resending. Inspect `<JOB_ID>.draft-pr-delivery.attempt.json`,
`<JOB_ID>.draft-pr-delivery.result.json`, or
`<JOB_ID>.draft-pr-delivery.stop.json` in the Run store when diagnosing a
stopped attempt.

If an attempt exists without a result, status reports `uncertain_no_retry`.
Invoking the same Issue-based `draft-pr` command again performs the existing
read-only reconciliation against the pinned branch, commit, and PR; it does
not resend the push or PR-create effect. If reconciliation cannot prove the
exact result, delivery remains stopped for human diagnosis.

Branch protection is observed through GitHub's read API and reported in the
confirmation summary and PR body. If the base is `protected=false`, GitHub
does not enforce the local CLI safeguards; configure branch protection
separately if required. This command does not change repository settings.

For the Issue → Draft PR → CI status view, use the read-only aggregation
command after the delivery attempt:

```bash
ai-office workflows engineering-issue-status \
  --issue-url https://github.com/OWNER/REPOSITORY/issues/NUMBER \
  --run-store /path/to/issue-job-runs
```

It reports the existing candidate/stop evidence and, once delivered, verifies
the stored delivery result against the same Issue, PR, exact HEAD, same-repo
base/head, Draft/open state, body markers, and changed paths. Check runs are
read from that exact PR HEAD; pending, failed, partial, or unavailable results
are not represented as CI success. Before separate GitHub approval, status
remains `awaiting_separate_github_approval`. It never creates or changes a PR.

## Offline validation

Tests use temporary Git repositories/remotes and a fake `gh` executable. They
do not perform GitHub writes, invoke a provider API, or use real credentials.
The product command must not be used as a live canary without separate human
approval of the exact repository, base, branch, Artifact and effects.
