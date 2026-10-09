# Issue #721: bounded read-only evidence collection

This feature collects only sources explicitly listed by an operator and emits a
private, content-hashed JSON evidence package that can be supplied as the input to
an existing Workflow. It does not add a Workflow phase, discover links, inspect a
working tree, execute source code, or invoke an AI provider.

## Operator flow

1. Copy and edit `examples/issue-721/evidence-request.json`. Issue URLs must be
   public GitHub Issue URLs. Each source file must name an exact repository, full
   40-character commit SHA, path, and optionally a closed line range. Public Web
   documents must be direct HTTPS URLs without query strings or fragments.
2. Create a private output directory, then run:

   ```sh
   umask 077
   evidence_dir="$(mktemp -d)"
   .venv/bin/ai-office workflows collect-engineering-evidence \
     --request examples/issue-721/evidence-request.json \
     --output "$evidence_dir/evidence-package.json" \
     --allowed-web-host calamares.io
   ```

   The command performs one GET per listed source at most. GitHub requests use the
   existing fixed-host GET transport; an optional `GITHUB_TOKEN` can increase
   public API rate limits. Web hosts must be explicitly allowlisted. DNS results
   are checked for public addresses and pinned for TLS connections; redirects are
   not followed. The output file is created exclusively with mode `0600`. Keep the
   containing directory private (`0700`). Do not use a shared or public location.
3. Inspect the summary and package. `collection_status: complete` means every
   requested item was retrieved without truncation; `partial` means at least one
   item is unavailable or truncated. Partial output is still written for review,
   and the CLI exits with status 2. Do not describe it as complete.
4. Only after human review, pass the package text as `--run-input` to the existing
   `investigate-calamares-usb-destination` Workflow. Use its normal preview and
   approval gates. Preview does not call a model; a live Workflow run is a separate
   paid/external action requiring explicit Business and Execution Approvals bound
   to the current Step preview.

## Package and trust boundary

Each source record preserves source type, explicit URL, retrieval timestamp,
retrieval context, status, content SHA-256, and raw response SHA-256 when available.
Source records also retain Issue/repository or pinned commit/path/line metadata.
The package has a deterministic JSON serialization; the CLI summary reports its
SHA-256. GitHub and Web text is untrusted evidence, never instructions or
authorization. Known credential patterns and a supplied GitHub token are redacted
before text is included. The collector does not follow links or redirects, retry a
failed read, fall back to another host, or run commands.

## Limits and safety invariants

- At most 24 explicitly listed sources; source bytes, per-source text, HTTP
  responses, timeout, and total package size are bounded.
- GitHub source reads are pinned to a full commit SHA. They prove only what the
  selected path/range says at that revision, not what a released image contains.
- Web collection is limited to exact operator-supplied host allowlists and HTTPS.
  It does not run JavaScript or fetch linked assets. Some sites may block the
  standard-library client or serve content that is not extractable as text.
- Missing, malformed, unsafe, over-limit, and redirected responses are represented
  as unavailable/partial; they are not silently omitted. A selected source that
  cannot be fetched remains visible in the package.
- The feature performs network GETs only. It does not write to GitHub, any
  repository, target disk, installer media, or machine. Local package/Run/Artifact
  persistence is permitted only in private locations as clarified on Issue #721.
- No AI provider is contacted by collection. Workflow preview is offline. Live AI
  execution remains subject to normal per-Step approval and is not part of this
  collection operation.

## Issue #721 acceptance status

| Acceptance area | Status | Evidence / boundary |
| --- | --- | --- |
| Explicit GitHub Issue, fixed-commit source/config, and official Web inputs | verified (offline) | Synthetic transport tests assert each request is explicit, bounded, and GET-only; the checked-in request is a worked scope example. |
| Bounded package, timestamps, source context, hashes, and visible partial results | verified (offline) | Offline tests cover canonical hashes, source metadata, truncation/unavailable records, and exclusive private output. |
| Reuse of existing Workflow, employee, handoff, provenance, and Markdown Artifact | verified (offline) | Existing #719 Workflow is unchanged; its separate offline behavior tests already cover downstream handoff/Artifact. This feature only supplies its Run input. |
| Live collection from the selected public sources | requires authorized live run | Operator may execute the explicit read-only GET collection command; external source availability/rate limits vary. No AI/provider call is involved. |
| Technical root-cause determination, target-media safety, or reproduction | blocked | Evidence collection does not inspect a target device, run Calamares, or establish the exact shipped build/device state. Human review and, if needed, an explicitly disposable VM with throwaway disks remain necessary. |
| Live AI report generation | requires authorized live run | Outside this collector; requires fresh per-Step preview and Business/Execution Approval. |
