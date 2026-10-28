# Publication intent handoff (offline-only capability)

`build_publication_intent_handoff()` reads the existing Run Manifest store,
reconstructs the terminal bounded-job result through
`inspect_bounded_document_job()`, then binds the selected Artifact, final
review Artifact, and local-verification Evidence into one canonical payload.
The immutable Run Input supplies the repository identity, pinned base, target
ref, and requested effect list. The candidate bytes travel in the handoff;
prompts, employee instructions, local paths, credentials, and Evidence bodies
do not. No second Evidence store is created.

The handoff is Ed25519-signed by a caller-supplied issuer key and checked by a
separate consumer against a pinned public key, current workflow run ID/attempt,
repository/base/ref context, expiry, and the candidate-byte digest. Production
key storage and delivery are deliberately not configured here. The private
key belongs only in a separately trusted preparation boundary, never in the AI
employee process or untrusted candidate execution.

## Authority boundary

The signature proves only that the holder of the trusted intent key attested
to these exact Run/Artifact/Evidence references and bytes. The returned value
has `authority="intent_only"`: it is **not** a human approval, publication
grant, or permission to create Git objects, refs, or a PR. The local-execution
`approved_by` field is audit metadata and is not copied into this intent.
There is no publisher, App credential loader, GitHub API client, approval
issuer, grant creation, or effect execution in this implementation.

A future trusted publisher must, after the Environment gate, independently
read GitHub's workflow-run approval history, bind the actual approving user
and the exact intent to the current workflow run/attempt, reject self-review
or bypass, and recheck expiry/revocation before credentials become available.
That separate authorization must be durable and one-use before any external
write. Intent verification alone must never unlock an App token.

## Preconditions not established by offline validation

Before any real publisher can be configured, a human GitHub administrator
must separately establish and inspect effective protection for the trusted
main/workflow source, required review of privileged workflow changes,
Environment reviewer and bypass rules, and an App installation limited to the
selected repository and required permissions. A GitHub-hosted runner is only
a candidate trust boundary; the run-store-to-runner artifact transfer,
signature-key custody, approval-history verification, and secret isolation
must be reviewed in that deployment. These GitHub settings and credentials
were not created or changed by this work.

Offline tests prove canonical signature/content/context checks against the
existing Run records and synthetic keys only. They do not prove human identity,
GitHub protection, Environment configuration, App credential safety, live
GitHub delivery, or provider behavior.
