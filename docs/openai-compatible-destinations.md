# OpenAI-compatible execution destinations

AI Office can reuse its existing approved model-execution path with named,
administrator-configured OpenAI-compatible APIs. This is a deliberately small
compatibility subset, not a claim of universal OpenAI API compatibility.

## Administrator registration

The current Git-managed runtime settings are
[`config/destinations.yaml`](../config/destinations.yaml). The file under
`examples/` remains illustrative and is not the production registry.
Each destination has four required administrator-owned fields:

- `endpoint`: the exact HTTPS endpoint;
- `protocol`: `openai-responses` or `openai-chat-completions`;
- `credential`: the environment variable name, never the secret value; and
- `models`: the exact operator-selectable model allowlist.

An optional `headers` mapping can declare a small set of safe, non-secret
request headers. See [Safe request headers](#safe-request-headers).

Deploy the registry as a regular, non-symlink file and use operating-system
ownership and permissions to restrict who can replace it or set the referenced
credential environment variables.

### Git source and operational copy

`config/destinations.yaml` is the reviewed source of truth for non-secret
destination settings. The host's
`~/.config/ai-office/destinations.yaml` remains the operational copy consumed
by the existing loader. GitHub changes do not synchronize or apply themselves:
there is no pull-triggered deployment, watcher, or new sync service.

After the relevant change has been reviewed and merged, an operator applying
it must select the approved commit, inspect the exact registry diff, and
manually copy that file to the host path. Preserve the existing trusted owner
and directory controls, a regular non-symlink file, and owner-only mode (0600),
then validate it with the existing destination loader/preview path. For example,
when run by the intended file owner from the selected checkout:

```console
install -m 0600 config/destinations.yaml \
  "$HOME/.config/ai-office/destinations.yaml"
```

Validate the operational copy using the existing preview-only CLI path (from
the repository root); this reads settings but does not read the credential or
contact the provider:

```console
ai-office workflows start offline-document-draft \
  --run-id destination-registry-preview \
  --run-input "Offline destination configuration validation only" \
  --run-store /tmp/ai-office-destination-registry-preview \
  --directory tests/fixtures/workflows \
  --employees-directory employees \
  --execution-target opencode-go-deepseek-v4-pro-chat \
  --execution-destinations "$HOME/.config/ai-office/destinations.yaml" \
  --step-model draft=deepseek-v4-pro \
  --preview-only
```

This is an operator procedure, not an action performed by this Issue. Confirm
the destination fingerprint in a fresh offline preview before considering any
separately authorized execution. Changing an endpoint changes that fingerprint;
old previews and approvals cannot be reused, and existing Runs, Manifests,
Evidence, and Artifacts are not migrated or resent.

Destination names become the evidence `provider` identity. `openai` and
`omniroute` remain reserved built-ins. The registry rejects extra fields,
userinfo, query strings, plaintext remote URLs, literal non-public IPs,
localhost, and known metadata hostnames. The HTTP transport does not follow
redirects; a redirect is normalized as a received invalid response, so the
Bearer credential is not forwarded to a second host.

Example preview (no credential read and no network request):

```console
ai-office workflows start article-production \
  --run-id m1-compatible-preview \
  --run-input "Medium: technical blog; explain the supplied design to engineers" \
  --execution-target opencode-zen \
  --execution-destinations docs/examples/openai-compatible-destinations.yaml \
  --step-model write-draft=replace-with-approved-responses-model \
  --preview-only
```

`article-production` is a bounded Workflow, so this command demonstrates its
read-only preview only; execute it through `workflows bounded-job-run` as
documented in the runbook. For a non-bounded Workflow execution, repeat the
same destination registry and Step model and supply
the existing Business Approval, Execution Approval, and all preview-bound
`--expected-*` values. `continue` and `recover` also require the same named
destination registry. A changed endpoint, protocol, credential reference, or
model policy changes the configuration fingerprint and invalidates a stale
preview/approval before credentials or transport are consulted. The selected
model is frozen in the existing Run Manifest Workflow-Step snapshot.

## Safe request headers

A destination may declare an optional `headers` mapping for compatible
services that require an identifying `User-Agent` and/or a stable session
identifier, without adding any provider-specific execution branch. The policy
is a strict allowlist: exactly two header names are supported, and everything
else is rejected.

- `User-Agent` accepts only a static, safe identifying string. It never
  accepts `{run_session}` or any substitution.
- `x-opencode-session` accepts only the single `{run_session}` marker, which
  resolves to a stable value derived solely from the Run identity
  (`ai-office-run-<run-id>`). It stays constant across steps, continuation and
  recovery, and it does not masquerade as OpenCode or a coding agent.

Because no arbitrary header name is allowed, there is no way to set a secret
value (for example an `X-API-Key`) and have it persisted into the Run
Manifest. CR/LF, control characters, duplicate names, surrounding whitespace,
unsafe lengths, and any unsupported `{...}` substitution are still rejected
before transport. `Authorization: Bearer` and `Content-Type` remain under
existing transport control and cannot be overridden. Endpoint allowlisting,
DNS pinning, redirect blocking, HTTPS and SSRF safeguards are unchanged.

The effective header policy is part of the destination configuration
fingerprint and the Run Manifest v5 snapshot. Changing the headers after a
preview/approval invalidates the stale approval before credentials or
network are consulted, and continuation/recovery cannot silently change
headers or session identity. The application currently supports Manifest v5;
this settings change adds no legacy-format migration or new Manifest schema.

Example Go Chat Completions destination (see
`examples/openai-compatible-destinations.yaml`):

```yaml
destinations:
  opencode-go-glm-chat:
    protocol: openai-chat-completions
    endpoint: https://opencode.ai/inference/go/openai/v1/chat/completions
    credential: OPENCODE_API_KEY
    models:
      - glm-5.3-flash
    headers:
      User-Agent: ai-office/1.0
      x-opencode-session: "{run_session}"
```

The example registry also includes `opencode-go-deepseek-v4-pro-chat` with the
same endpoint and safe headers but only `deepseek-v4-pro` in its model list.
The canonical runtime registry above retains only the currently approved
DeepSeek destination; the illustrative GLM entry does not add GLM to the
runtime allowlist. An endpoint change produces a new fingerprint; historical
Runs keep their original evidence and are not rewritten. Any provider
execution remains a separate, explicitly authorized action.

## OpenCode Go usage caveats

The Go destination is a secret-free, offline-testable configuration. The
[official OpenCode Go endpoint table](https://dev.opencode.ai/v2/docs/console/go/)
lists `deepseek-v4-pro` on
`/inference/go/openai/v1/chat/completions`; its Responses-compatible models use
the separate `/inference/go/openai/v1/responses` path. Do not substitute one
protocol's URL for the other. OpenCode calls for typical coding-agent traffic,
a truthful identifying `User-Agent`, and a stable `x-opencode-session` per
conversation. Go is separate from the Console pay-as-you-go Inference API.
Before a live run, an operator
must confirm current model entitlement and allowance, use the exact admin
destination fingerprint across every fixed Job action, and ensure no fallback
to a separate balance is possible. A provider attempt count is not a monetary
spend cap. Technical wire compatibility and account/contract eligibility are
separate checks. The bounded one-shot Gate B procedure and required host/UID
isolation checks are documented in
[`runbooks/article-production.md`](runbooks/article-production.md).

## Supported compatibility subset

Both API families support one non-streaming, text-in/text-out workflow step.
The Responses family retains the existing AI Office tool schema. The new Chat
Completions compatibility path intentionally supports `allowed_tools: []`
only and sends:

- `model`;
- one `system` and one `user` message; and
- `stream: false`.

It accepts one `chat.completion` choice with an assistant string `content`.
Only `finish_reason: stop` is a successful completion. `length` is recorded as
an incomplete output, `content_filter` as a filtered output, and every other
finish reason as unsupported. All three are durable `invalid_output` failures:
the Run stops, no success Artifact is created, and an explicit failed-attempt
Recovery is required. Both families accept the standard JSON `error` envelope.
Streaming, multimodal content, tool calls, multiple choices, vendor extensions,
and vendor-specific authentication are out of scope and fail closed.

## OpenCode Zen sample

The sample uses the documented Zen Responses endpoint. Before any live run,
an administrator must re-check the current Zen model list and select a model
explicitly documented for the Responses API. Do not assume the sample model
placeholder or a Workflow Step's default model is available. Review the
chosen model's pricing, retention, and training terms for the intended data.
See the [OpenCode Zen documentation](https://docs.opencode.ai/docs/zen/).

## Validation matrix

| Check | Offline status | Live approval required |
| --- | --- | --- |
| Registry parsing and unsafe literal endpoint rejection | verified | no |
| Responses request/success/error normalization via fake transport | verified | no |
| Chat Completions request/success/error normalization via fake transport | verified | no |
| Preview/approval/config/model binding | verified | no |
| Evidence, Markdown Artifact, read/export, and secret absence | verified | no |
| Provider wire compatibility and generated content quality | not executed | yes |

No provider request or real credential is needed for the offline checks. A
future live canary is a separate paid/external action and requires explicit
authorization.
