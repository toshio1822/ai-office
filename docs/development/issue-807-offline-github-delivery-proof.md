# Issue #807 Offline Delivery Proof

This is a test-only composition proof, not a product Git publisher. It starts
from the existing configured bounded Workflow and reuses its Run scope,
reviewed Artifact, local-execution authorization, verified Evidence, and
Evidence link. The test exercises the proposed finite delivery sequence
against a temporary repository, a local bare Git remote, and a persistent
fake GitHub API. It adds no employee type, Workflow engine, provider transport,
or live GitHub client.

Run the end-to-end proof on a host where the required local sandbox is
available:

```sh
PYTHONPATH=src pytest -q tests/test_local_execution.py::test_bounded_workflow_validates_revises_and_re_reviews_exact_artifacts
```

The same parametrized test runs the configured author/reviewer loop through
the existing fake provider transport in both normal terminal forms: all
reviewers PASS with no revision (`candidate`) and all reviewers PASS after a
revision (`reviewed`). The engine's read-only inspection reconstructs the
terminal Artifact, final reviewer Artifact, PASS history, and revision count
from the persisted Job; delivery accepts only those exact IDs and binds the
terminal status/revision count alongside the Artifact and Evidence. A locally
verified but review-rejected earlier Artifact is explicitly rejected before a
grant or delivery Attempt is written. The offline delivery portion then
checks that:

- the grant binds the Run, final Artifact and digest, verification Evidence,
  base revision, repository, exact changed paths, fake remote, and new ref;
- protected refs, expired/revoked grants, moved bases, a forged `approved_by`
  value, and a wrong-repository credential fail closed before an effect;
- the local commit contains exactly the Artifact's permitted content and has
  the pinned base as its parent, without changing the source checkout;
- fake GitHub blob, tree, commit, ref, and Draft PR writes each have a one-shot
  Attempt/result in the existing Run store, and the Draft's repository,
  base/head/ref and diff digest match the verified candidate;
- the local bare fake remote and separate persistent fake API state contain
  Git objects, refs, Draft records and request audit metadata, but no synthetic
  credential; and
- a lost response is reconciled by reads only, with no repeated write. The
  Draft points to the exact verified commit.

Recovery is exercised by launching fresh OS processes. Each reopens the Run
store, local bare remote, and fake API state; it does not reuse an API object or
process memory. After an ambiguous Draft creation response, the next process
locates the exact persisted Draft by repository and head and verifies its base,
head and diff digest without another POST. A completed replay also adds no
write.

The grant issuer is deliberately synthetic. Its `approved_by` value is not
authenticated human identity or authorization to publish; a fixed test-only
issuer fixture rejects caller-supplied identity text. The fake credential is
also a synthetic test token supplied by a test-only environment variable; it
is not saved in Run records, fake API state, or subprocess output. There is no
HTTP/network transport. Passing this proof does not establish a production
trust/credential boundary, real GitHub behavior, repository protection, or
authorization for an AI employee to publish to GitHub. Real delivery,
credential handling, and any live canary remain out of scope and require
separate design approval and explicit authorization. This test helper is not a
product delivery capability: a future implementation should consume the
existing Workflow terminal inspection, Run-bound authorization, and Evidence
rather than duplicate those owners or treat this proof as a completed
publisher.
