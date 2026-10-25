# Offline Local Change Proof

This proof composes the existing immutable Workflow Run Manifest, provider
execution evidence, and Artifact with one narrow Linux local-effect boundary.
It does not add a software-job engine, tool-dispatch loop, language branch, or
special employee identity. Reviewer count and reviewer roles live in
[`workflows/bounded-change-review.yaml`](../../workflows/bounded-change-review.yaml)
and the referenced Employee definitions.

## Boundary

- The author step returns one canonical `file-change-set.v1` JSON Artifact.
  It names an exact Git base revision, relative existing file paths, SHA-256
  preimages, and UTF-8 replacement bytes. It is data, never a command; deletion
  and mode changes are not part of the contract.
- An operator reviews a JSON policy and must pass
  `--approve-local-effect` with a distinct approval ID. The durable grant binds
  the Run Manifest digest, Artifact ID and digest, repository path and base
  revision, exact read/write paths, exact command argv, finite resource bounds,
  and expiry. This is separate from any provider execution approval.
- The one-use attempt claim is persisted before launch. Duplicate, resumed,
  ambiguous, or expired attempts fail closed and are not replayed.
- The required `/usr/bin/bwrap` runner creates fresh user, PID, IPC, UTS, and
  network namespaces; disables further user namespaces; drops capabilities;
  exposes only read-only `/usr` and the selected source files; and gives the
  candidate an empty, size-limited `/workspace` and `/tmp`. Resource limits,
  wall-clock timeouts, and bounded/redacted output are enforced. There is no
  host-execution fallback, dependency download, or network route.
- Read-only Git revision/status preflight uses the same namespace boundary with
  repository and Git metadata mounted read-only. It also requires
  `/usr/bin/prlimit` and applies finite CPU, address-space, file-size, process,
  descriptor, and wall-clock bounds; unavailable boundaries fail closed.
- The runner never applies the candidate to the host repository. It returns an
  exact diff and before/after digests from disposable tmpfs. The immutable
  Evidence is available only for a separate reviewer Run when status is
  `verified`.

## Operator flow

First create the candidate Artifact through the normal configured Workflow
Run. Fix the Run Input before provider execution and include the request,
repository identity/base SHA, exact readable/writable path sets, acceptance
criteria, and exact UTF-8 source context needed for the bounded change. For
example:

```json
{
  "request": "Change answer() to return 2 without changing the test.",
  "repository": {
    "identity": "temporary offline-proof repository",
    "base_revision": "<40- or 64-character Git SHA>"
  },
  "allowed_paths": {
    "read": ["calculator.py", "test_calculator.py"],
    "write": ["calculator.py"]
  },
  "acceptance_criteria": [
    "answer() returns 2",
    "the supplied unittest suite passes"
  ],
  "source_context": {
    "calculator.py": "def answer():\n    return 1 + 1\n",
    "test_calculator.py": "<exact UTF-8 test source>"
  }
}
```

Inspect the exact Manifest, Artifact, and workspace revision before creating a
policy. A policy file has this shape (replace paths, commands, and limits with
the exact reviewed values):

```json
{
  "base_revision": "<40- or 64-character Git SHA>",
  "read_paths": ["calculator.py", "test_calculator.py"],
  "write_paths": ["calculator.py"],
  "commands": [
    ["/usr/bin/python3", "-B", "-m", "unittest", "discover", "-s", "/workspace", "-p", "test_*.py"],
    ["/usr/bin/python3", "-B", "-c", "import ast,pathlib; ast.parse(pathlib.Path('calculator.py').read_text())"]
  ],
  "limits": {
    "wall_timeout_seconds": 30,
    "cpu_seconds": 20,
    "memory_bytes": 268435456,
    "workspace_bytes": 16777216,
    "max_file_bytes": 1048576,
    "max_output_bytes": 16384,
    "max_processes": 256,
    "max_open_files": 64
  }
}
```

Then authorize and execute the same Run-bound Artifact:

```sh
ai-office workflows local-effect-authorize RUN_ID ARTIFACT_ID \
  --policy ./local-effect-policy.json \
  --workspace /absolute/path/to/clean/repository \
  --approved-by OPERATOR_ID --approval-id CHANGE_APPROVAL_ID \
  --expires-in-seconds 300 --approve-local-effect \
  --run-store /private/path/ai-office-runs

ai-office workflows local-effect-run RUN_ID ARTIFACT_ID \
  --authorization-id AUTHORIZATION_SHA256 \
  --workspace /absolute/path/to/clean/repository \
  --run-store /private/path/ai-office-runs
```

Only `status: verified` is eligible for review. Supply the exact output of
`local-effect-review-input` as the Run Input of each independently configured
reviewer Run; the sample config declares two distinct reviewer Employees.
Reviewers use the normal Workflow/Run and provider transport, and their
count/order remain configuration data. This proof's tests use only an
in-memory fake provider transport; the runbook does not authorize a real model
call.

```sh
ai-office workflows local-effect-review-input RUN_ID ARTIFACT_ID \
  --authorization-id AUTHORIZATION_SHA256 \
  --run-store /private/path/ai-office-runs
```

Keep the Run store for author/reviewer Runs and local-effect Evidence outside
the target repository; local-effect authorization fails closed if the store
is inside the workspace.

If bwrap or any required namespace is unavailable, stop. Do not weaken the
policy, run commands on the host, or treat an incomplete/unknown result as a
verified candidate. Local verification must not commit or push the candidate.
