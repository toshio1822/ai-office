# Issue #805 Offline Delivery Proof

This is a test-only composition proof, not a product Git publisher. It starts
from the existing configured bounded Workflow and reuses its Run scope,
reviewed Artifact, local-execution authorization, verified Evidence, and
Evidence link. The test then exercises the proposed finite delivery sequence
against a temporary repository, a local bare Git remote, and an in-memory fake
Draft PR API. It adds no employee type, Workflow engine, provider transport, or
live GitHub client.

Run the end-to-end proof on a host where the required local sandbox is
available:

```sh
PYTHONPATH=src pytest -q tests/test_local_execution.py::test_bounded_workflow_validates_revises_and_re_reviews_exact_artifacts
```

The same test first runs the configured author/reviewer loop through the
existing fake provider transport. It rejects the initial candidate, validates
the revision in the configured sandbox, and requires every configured
reviewer to pass before the final Artifact and Evidence are accepted. The
offline delivery portion then checks that:

- the grant binds the Run, final Artifact and digest, verification Evidence,
  base revision, repository, exact changed paths, fake remote, and new ref;
- protected refs, expired/revoked grants, and a moved base are exercised as
  fail-closed cases. The preflight also refuses dirty workspaces or missing or
  mismatched persisted Evidence before an effect attempt;
- the local commit contains exactly the Artifact's permitted content and has
  the pinned base as its parent, without changing the source checkout;
- push targets only the local bare fake remote, with hooks and ambient Git
  configuration disabled; and
- a lost response is reconciled by reads only, with no repeated push or fake
  PR creation. The resulting PR record is Draft and points to the exact
  verified commit.

The test's grant issuer is deliberately synthetic. Its `approved_by` value is
not authenticated human identity or authorization to publish. The fake API
has no credentials or network transport, and the helper rejects any other API
object. Passing this proof does not establish a production trust/credential
boundary and does not authorize an AI employee to publish to GitHub. Real
delivery, credential handling, and any live canary remain out of scope and
require separate design approval and explicit authorization.
