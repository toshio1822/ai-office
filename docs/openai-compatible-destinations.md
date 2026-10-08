# OpenAI-compatible execution destinations

AI Office can reuse its existing approved model-execution path with named,
administrator-configured OpenAI-compatible APIs. This is a deliberately small
compatibility subset, not a claim of universal OpenAI API compatibility.

## Administrator registration

Start from [`examples/openai-compatible-destinations.yaml`](examples/openai-compatible-destinations.yaml).
Each destination has exactly four administrator-owned fields:

- `endpoint`: the exact HTTPS endpoint;
- `protocol`: `openai-responses` or `openai-chat-completions`;
- `credential`: the environment variable name, never the secret value; and
- `models`: the exact operator-selectable model allowlist.

Deploy the registry as a regular, non-symlink file and use operating-system
ownership and permissions to restrict who can replace it or set the referenced
credential environment variables.

Destination names become the evidence `provider` identity. `openai` and
`omniroute` remain reserved built-ins. The registry rejects extra fields,
userinfo, query strings, plaintext remote URLs, literal non-public IPs,
localhost, and known metadata hostnames. The HTTP transport does not follow
redirects; a redirect is normalized as a received invalid response, so the
Bearer credential is not forwarded to a second host.

Example preview (no credential read and no network request):

```console
ai-office workflows start write-internal-ai-guide \
  --run-id m1-compatible-preview \
  --run-input "社内向け利用ガイドを作成" \
  --execution-target opencode-zen \
  --execution-destinations docs/examples/openai-compatible-destinations.yaml \
  --execution-model replace-with-approved-responses-model \
  --preview-only
```

For an execution, repeat the same destination registry and model and supply
the existing Business Approval, Execution Approval, and all preview-bound
`--expected-*` values. `continue` and `recover` also require the same named
destination registry. A changed endpoint, protocol, credential reference, or
model policy changes the configuration fingerprint and invalidates a stale
preview/approval before credentials or transport are consulted. The selected
model is frozen in the existing Run Manifest employee snapshot.

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
placeholder or an employee's default OpenAI model is available. Review the
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
