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

## Optional trusted Python bundle

Use this only when the fixed verification command needs Python packages or
oracle/source files that are absent from read-only `/usr`. Keep the bundle
outside both the repository workspace and Run store. It is supplied by the
operator, never by Run Input or model output, and is not a virtual environment
or dependency installer.

The directory contains a canonical `manifest.json` and exactly the listed
regular files under three roots:

```text
manifest.json
oracle/<fixed test files>
site-packages/<pre-provisioned dependency files>
source/src/<fixed project-source closure>
```

The manifest pins `schema_version` (`local-execution-trusted-bundle.v1`),
`python_cache_tag`, `python_platform`, and a path-sorted `files` list. Each list
item contains the relative `path`, byte `size`, and `sha256`. The operator puts
the canonical manifest's SHA-256 and absolute bundle path in the local-effect
policy. The existing authorization digest binds that policy; local Evidence
records the same manifest digest. The host and in-sandbox bootstrap both check
ABI, exact file/directory set, no symlinks or hard links, each file digest, and
the current 1,024-file / 4,096-directory / 32-MiB total / 16-MiB per-file
bounds before running commands. A missing, changed, extra, unsupported, or
malformed bundle fails closed.

Only explicitly approved `/usr/bin/python3 -S -B ...` commands may use a bundle.
The existing bootstrap passes a fixed `PYTHONPATH` containing only
`/input/trusted/source/src` and `/input/trusted/site-packages`; Python site
initialization and ambient site packages are disabled. The complete input
root remains one read-only bwrap bind at `/input`, including the fixed oracle
and runtime. Candidate files remain limited by `read_paths`/`write_paths` in
the existing disposable `/workspace`; do not add oracle or dependency files
to model `source_context` just to make them importable. Prepare the bundle
offline from an already trusted, ABI-matched dependency closure; do not include
the host `.venv`, secrets, mutable package caches, or downloaded/unverified
files.

## Operator flow

First create the candidate Artifact through the normal configured Workflow
Run. Fix the Run Input before provider execution and include the request,
repository identity/base SHA, exact readable/writable path sets, acceptance
criteria, exact UTF-8 source context needed for the bounded change, and a
`source_sha256` mapping for every writable path. Compute those digests on the
operator side from the exact bytes at the fixed base revision, then verify each
digest against its matching `source_context` value before Run creation. These
values are prompt inputs, not a replacement for the existing local preimage
validation; author and revision steps copy them verbatim and never calculate
them. The existing revision handoff retains the original Run Input under
`assignment`, so the digest source remains fixed across a revision. For example:

```json
{
  "request": "Change answer() to return 2 without changing the test.",
  "repository": {
    "identity": "temporary offline-proof repository",
    "base_revision": "<40- or 64-character Git SHA>",
    "path": "/absolute/path/to/clean/repository"
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
  },
  "source_sha256": {
    "calculator.py": "<operator-computed SHA-256 of exact calculator.py base bytes>"
  }
}
```

The local policy must be no broader than `allowed_paths`, use the exact fixed
base revision, and match every selected source file in `source_context`. Inspect
the exact Run Input, workspace revision, commands, and limits before creating a
policy file with this shape:

```json
{
  "base_revision": "<40- or 64-character Git SHA>",
  "read_paths": ["calculator.py", "test_calculator.py"],
  "write_paths": ["calculator.py"],
  "commands": [
    ["/usr/bin/python3", "-S", "-B", "-m", "unittest", "discover", "-s", "/workspace", "-p", "test_*.py"],
    ["/usr/bin/python3", "-S", "-B", "-c", "import ast,pathlib; ast.parse(pathlib.Path('calculator.py').read_text())"]
  ],
  "trusted_bundle_path": "/absolute/operator/managed/python-proof-bundle",
  "trusted_bundle_manifest_sha256": "<sha256 of canonical manifest.json>",
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

Omit both trusted-bundle fields for standard-library-only commands. If either
is present, the other is required and Python commands must begin exactly with
`["/usr/bin/python3", "-S", "-B"]`. The approval output and final local
Evidence include the manifest digest.

Run the configured finite Workflow with a separate local-effect approval. The
bounded Job generates a candidate, validates it in the isolated runner, then
passes that exact Artifact and immutable Evidence to every configured reviewer.
If reviewers request a revision, the new Artifact is validated and independently
reviewed through the same configured loop. The CLI asks for separate Business,
Execution, and local-effect confirmations; the latter covers at most one initial
candidate plus the configured revision budget.

```sh
ai-office workflows bounded-job-run JOB_ID \
  --run-id INITIAL_RUN_ID \
  --assignment-file ./run-input.json \
  --workflow bounded-change-review \
  --max-revisions 1 \
  --local-effect-policy ./local-effect-policy.json \
  --local-effect-workspace /absolute/path/to/clean/repository \
  --approve-local-effect \
  --execution-destinations ./config/execution-destinations.yaml \
  --run-store /private/path/ai-office-runs
```

Only a `verified` local result is passed to reviewers; other outcomes stop the
Job before review or revision. Reviewer count/order remain configuration data,
and the common bounded Workflow parser enforces the shared structured verdict
contract. Automated proof tests use an in-memory fake provider transport and
never authorize a real model call.

Keep the Run store for author/reviewer Runs and local-effect Evidence outside
the target repository; local-effect authorization fails closed if the store
is inside the workspace.

If bwrap or any required namespace is unavailable, stop. Do not weaken the
policy, run commands on the host, or treat an incomplete/unknown result as a
verified candidate. Local verification must not commit or push the candidate.
