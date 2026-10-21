"""Offline proof for Issue #706 configurable compatible destinations."""

import json
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.engine.workflow_recovery import assess_workflow_recovery
from ai_office.execution_destination import (
    ExecutionDestinationError,
    load_execution_destination_registry,
)
from ai_office.execution_evidence import (
    load_normalized_result_evidence,
    load_raw_response_evidence,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse
from ai_office.providers.openai.chat_completions import (
    normalize_openai_chat_completions_raw_response,
    serialize_openai_chat_completions_request,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_INPUT = "社内向けの短い生成AI利用ガイドをMarkdownで作成してください。"
runner = CliRunner()


def _write_registry(
    path: Path,
    *,
    responses_endpoint: str = "https://opencode.ai/zen/v1/responses",
) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "destinations": {
                    "zen-responses": {
                        "endpoint": responses_endpoint,
                        "protocol": "openai-responses",
                        "credential": "OPENCODE_API_KEY",
                        "models": ["test-responses-model"],
                    },
                    "example-chat": {
                        "endpoint": "https://api.example.com/v1/chat/completions",
                        "protocol": "openai-chat-completions",
                        "credential": "EXAMPLE_CHAT_API_KEY",
                        "models": ["test-chat-model"],
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_routed_registry(
    path: Path,
    *,
    chat_endpoint: str = "https://api.example.com/v1/chat/completions",
) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "destinations": {
                    "zen-responses": {
                        "endpoint": "https://opencode.ai/zen/v1/responses",
                        "protocol": "openai-responses",
                        "credential": "OPENCODE_API_KEY",
                        "models": ["test-responses-model"],
                        "headers": {"x-opencode-session": "{run_session}"},
                    },
                    "example-chat": {
                        "endpoint": chat_endpoint,
                        "protocol": "openai-chat-completions",
                        "credential": "EXAMPLE_CHAT_API_KEY",
                        "models": ["test-chat-model"],
                        "headers": {"x-opencode-session": "{run_session}"},
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_two_step_workflow(directory: Path) -> None:
    directory.mkdir()
    (directory / "routed.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "routed-workflow",
                "name": "Routed Workflow",
                "description": "A two-step offline routing proof.",
                "execution_destination": "example-chat",
                "steps": [
                    {
                        "id": "draft",
                        "name": "Draft",
                        "employee": "article-writer",
                        "model": "test-responses-model",
                        "execution_destination": "zen-responses",
                        "instructions": "Write a short draft from the supplied input.",
                        "business_approval_required": True,
                        "artifact_content_type": "text/markdown",
                    },
                    {
                        "id": "review",
                        "name": "Review",
                        "employee": "article-writer",
                        "model": "test-chat-model",
                        "instructions": "Review the exact previous output.",
                        "business_approval_required": True,
                        "artifact_content_type": "text/markdown",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _start_args(
    run_store: Path, registry: Path, run_id: str, destination: str, model: str
) -> list[str]:
    return [
        "workflows",
        "start",
        "offline-document-draft",
        "--run-id",
        run_id,
        "--run-input",
        RUN_INPUT,
        "--run-store",
        str(run_store),
        "--directory",
        str(ROOT / "tests" / "fixtures" / "workflows"),
        "--employees-directory",
        str(ROOT / "employees"),
        "--execution-target",
        destination,
        "--execution-destinations",
        str(registry),
        "--step-model",
        "draft=" + model,
    ]


def _approval_args(preview: dict[str, object], suffix: str) -> list[str]:
    return [
        "--approve-business",
        "--business-approved-by",
        "offline-reviewer",
        "--business-approval-id",
        f"business-{suffix}",
        "--approve-execution",
        "--execution-approved-by",
        "offline-operator",
        "--execution-approval-id",
        f"execution-{suffix}",
        "--expected-step-id",
        str(preview["step_id"]),
        "--expected-step-index",
        str(preview["step_index"]),
        "--expected-employee-id",
        str(preview["employee_id"]),
        "--expected-request-fingerprint",
        str(preview["request_fingerprint"]),
    ]


def test_registry_loads_two_protocols_and_rejects_unsafe_endpoints(
    tmp_path: Path,
) -> None:
    path = tmp_path / "destinations.yaml"
    _write_registry(path)
    registry = load_execution_destination_registry(path)
    responses = registry.resolve("zen-responses")
    chat = registry.resolve("example-chat")
    assert responses.target.protocol == "openai-responses"
    assert chat.target.protocol == "openai-chat-completions"
    assert responses.select_model("test-responses-model") == "test-responses-model"
    assert responses.target.configuration_fingerprint is not None

    for endpoint in (
        "http://example.com/v1/responses",
        "https://127.0.0.1/v1/responses",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://user:secret@example.com/v1/responses",
        "https://example.com/v1/responses?token=unsafe",
    ):
        _write_registry(path, responses_endpoint=endpoint)
        with pytest.raises(ExecutionDestinationError):
            load_execution_destination_registry(path)


@pytest.mark.parametrize(
    ("destination", "model", "protocol"),
    (
        ("zen-responses", "test-responses-model", "openai-responses"),
        ("example-chat", "test-chat-model", "openai-chat-completions"),
    ),
)
def test_same_workflow_executes_both_protocols_offline(
    tmp_path: Path,
    monkeypatch,
    destination: str,
    model: str,
    protocol: str,
) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_registry(registry)
    run_store = tmp_path / "runs"
    requests: list[object] = []
    key_targets: list[object] = []

    def fake_key(target: object) -> OpenAIApiKey:
        key_targets.append(target)
        return OpenAIApiKey(value=SecretStr("offline-key"))

    def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        requests.append(request)
        request_value = json.loads(request.body)
        if protocol == "openai-responses":
            assert request_value["model"] == model
            response = {
                "id": "response-offline",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "# Offline proof"}],
                    }
                ],
            }
        else:
            assert request_value["model"] == model
            assert request_value["stream"] is False
            assert [item["role"] for item in request_value["messages"]] == [
                "system",
                "user",
            ]
            assert all(item["content"] for item in request_value["messages"])
            response = {
                "id": "chatcmpl-offline",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "# Offline proof"},
                        "finish_reason": "stop",
                    }
                ],
            }
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "offline-request"),),
            json.dumps(response).encode("utf-8"),
        )

    monkeypatch.setattr(cli_module, "load_api_key_for_execution_target", fake_key)
    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", fake_transport
    )
    args = _start_args(run_store, registry, f"run-{destination}", destination, model)
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    assert preview["model"] == model
    assert preview["execution_target"]["protocol"] == protocol
    assert preview["execution_target"]["configuration_fingerprint"]
    assert not requests and not key_targets

    result = runner.invoke(app, args + _approval_args(preview, destination))
    assert result.exit_code == 0, (result.stderr, result.stdout, result.exception)
    assert json.loads(result.stdout)["status"] == "workflow_complete"
    assert len(requests) == 1
    assert len(key_targets) == 1

    evidence = runner.invoke(
        app,
        [
            "workflows",
            "execution-evidence",
            f"run-{destination}",
            "--run-store",
            str(run_store),
        ],
    )
    assert evidence.exit_code == 0, evidence.stderr
    attempt = json.loads(evidence.stdout)["attempts"][0]
    assert attempt["provider"] == destination
    assert attempt["normalized_result_category"] == "success"
    artifacts = runner.invoke(
        app,
        [
            "workflows",
            "artifacts",
            f"run-{destination}",
            "--run-store",
            str(run_store),
        ],
    )
    assert artifacts.exit_code == 0, artifacts.stderr
    artifact_id = json.loads(artifacts.stdout)["artifacts"][0]["artifact_id"]
    export_path = tmp_path / f"{destination}.md"
    exported = runner.invoke(
        app,
        [
            "workflows",
            "artifact-export",
            f"run-{destination}",
            artifact_id,
            "--run-store",
            str(run_store),
            "--output",
            str(export_path),
        ],
    )
    assert exported.exit_code == 0, exported.stderr
    assert export_path.read_text(encoding="utf-8") == "# Offline proof"
    assert len(requests) == 1
    assert all(
        "offline-key" not in path.read_text(encoding="utf-8")
        for path in run_store.iterdir()
        if path.is_file()
    )
    manifest = json.loads(
        (run_store / f"run-{destination}.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == "workflow-run-manifest.v5"
    assert manifest["workflow_snapshot"]["steps"][0]["model"] == model
    assert "model" not in manifest["employee_snapshots"][0]
    assert manifest["execution_destination"]["provider"] == destination
    switched = runner.invoke(
        app,
        [
            "workflows",
            "continue",
            f"run-{destination}",
            "--run-store",
            str(run_store),
            "--execution-target",
            "openai",
            "--preview-only",
        ],
    )
    assert switched.exit_code == 2
    assert len(requests) == 1


def test_workflow_steps_use_pinned_destinations_and_explicit_recovery_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_routed_registry(registry)
    workflows = tmp_path / "workflows"
    _write_two_step_workflow(workflows)
    run_store = tmp_path / "runs"
    run_id = "routed-run"
    key_providers: list[str] = []
    requests: list[object] = []
    chat_attempts = 0

    def fake_key(target: object) -> OpenAIApiKey:
        provider = str(target.provider)
        key_providers.append(provider)
        return OpenAIApiKey(value=SecretStr("offline-only-key"))

    def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        nonlocal chat_attempts
        requests.append(request)
        payload = json.loads(request.body)
        if request.url == "https://opencode.ai/zen/v1/responses":
            assert payload["model"] == "test-responses-model"
            response = {
                "id": "draft-response",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "# Offline draft"}
                        ],
                    }
                ],
            }
            return OpenAIResponsesRawHttpResponse(
                200,
                "synthetic",
                (("x-request-id", "draft-response"),),
                json.dumps(response).encode("utf-8"),
            )

        assert request.url == "https://api.example.com/v1/chat/completions"
        assert payload["model"] == "test-chat-model"
        chat_attempts += 1
        if chat_attempts == 1:
            return OpenAIResponsesRawHttpResponse(
                429,
                "synthetic rate limit",
                (("x-request-id", "chat-rate-limit"),),
                b'{"error":{"message":"rate limit","type":"rate_limit_error"}}',
            )
        response = {
            "id": "review-response",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "# Offline review"},
                    "finish_reason": "stop",
                }
            ],
        }
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "review-response"),),
            json.dumps(response).encode("utf-8"),
        )

    monkeypatch.setattr(cli_module, "load_api_key_for_execution_target", fake_key)
    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", fake_transport
    )
    base_args = [
        "workflows",
        "start",
        "routed-workflow",
        "--run-id",
        run_id,
        "--run-input",
        "Write and review this synthetic brief.",
        "--run-store",
        str(run_store),
        "--directory",
        str(workflows),
        "--employees-directory",
        str(ROOT / "employees"),
        "--execution-target",
        "openai",
        "--execution-destinations",
        str(registry),
    ]

    start_preview_result = runner.invoke(app, base_args + ["--preview-only"])
    assert start_preview_result.exit_code == 0, start_preview_result.stderr
    start_preview = json.loads(start_preview_result.stdout)
    assert start_preview["execution_target"]["provider"] == "zen-responses"
    assert not requests and not key_providers

    started = runner.invoke(
        app, base_args + _approval_args(start_preview, "routed-start")
    )
    assert started.exit_code == 0, (started.stderr, started.stdout, started.exception)
    assert json.loads(started.stdout)["status"] == "prepare_next_step"
    manifest_path = run_store / f"{run_id}.manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["schema_version"] == "workflow-run-manifest.v6"
    assert manifest["execution_destinations"][0]["provider"] == "zen-responses"
    assert manifest["execution_destinations"][1]["provider"] == "example-chat"
    assert manifest["workflow_snapshot"]["execution_destination"] == "example-chat"
    assert manifest["workflow_snapshot"]["steps"][0]["execution_destination"] == (
        "zen-responses"
    )
    assert manifest["workflow_snapshot"]["steps"][1]["execution_destination"] is None

    continue_args = [
        "workflows",
        "continue",
        run_id,
        "--run-store",
        str(run_store),
        "--execution-destinations",
        str(registry),
    ]
    continue_preview_result = runner.invoke(app, continue_args + ["--preview-only"])
    assert continue_preview_result.exit_code == 0, continue_preview_result.stderr
    continue_preview = json.loads(continue_preview_result.stdout)
    assert continue_preview["step_id"] == "review"
    assert continue_preview["execution_target"]["provider"] == "example-chat"
    assert len(requests) == 1 and len(key_providers) == 1

    state_path = run_store / f"{run_id}.state.json"
    events_path = run_store / f"{run_id}.events.jsonl"
    state_before_stale_preview = state_path.read_bytes()
    events_before_stale_preview = events_path.read_bytes()
    _write_routed_registry(
        registry, chat_endpoint="https://api.example.com/changed/v1/chat/completions"
    )
    stale_preview = runner.invoke(app, continue_args + ["--preview-only"])
    assert stale_preview.exit_code == 2
    assert len(requests) == 1 and len(key_providers) == 1
    assert manifest_path.read_bytes() == manifest_bytes
    assert state_path.read_bytes() == state_before_stale_preview
    assert events_path.read_bytes() == events_before_stale_preview

    _write_routed_registry(registry)
    failed = runner.invoke(
        app,
        continue_args + _approval_args(continue_preview, "routed-review"),
    )
    assert failed.exit_code == 1
    assert json.loads(failed.stdout)["status"] == "persisted_failure"
    assert len(requests) == 2
    assert key_providers == ["zen-responses", "example-chat"]

    recovery_inspection = runner.invoke(
        app,
        [
            "workflows",
            "recovery",
            run_id,
            "--run-store",
            str(run_store),
        ],
    )
    assert recovery_inspection.exit_code == 0, recovery_inspection.stderr
    decision = json.loads(recovery_inspection.stdout)
    assert decision["eligible"] is True
    assert decision["action"] == "retry_failed"
    recovery_args = [
        "workflows",
        "recover",
        run_id,
        "--run-store",
        str(run_store),
        "--recovery-decision-sha256",
        decision["recovery_decision_sha256"],
        "--approve-recovery",
        "--recovery-approved-by",
        "offline-recovery-reviewer",
        "--recovery-approval-id",
        "routed-recovery-approval",
        "--execution-destinations",
        str(registry),
        "--approve-execution",
        "--execution-approved-by",
        "offline-execution-operator",
        "--execution-approval-id",
        "routed-recovery-execution",
        "--expected-step-id",
        decision["step_id"],
        "--expected-step-index",
        str(decision["step_index"]),
        "--expected-employee-id",
        decision["employee_id"],
        "--expected-request-fingerprint",
        decision["invocation_fingerprint"],
    ]
    _write_routed_registry(
        registry, chat_endpoint="https://api.example.com/changed/v1/chat/completions"
    )
    stale_recovery = runner.invoke(app, recovery_args)
    assert stale_recovery.exit_code == 2
    assert len(requests) == 2 and key_providers == ["zen-responses", "example-chat"]
    _write_routed_registry(registry)
    recovered = runner.invoke(app, recovery_args)
    assert recovered.exit_code == 0, (recovered.stderr, recovered.stdout)
    assert len(requests) == 3
    assert key_providers == ["zen-responses", "example-chat", "example-chat"]

    session_values = [
        next(
            value
            for name, value in request.headers
            if name.lower() == "x-opencode-session"
        )
        for request in requests
    ]
    assert session_values == [
        f"ai-office-run-{run_id}-step-1",
        f"ai-office-run-{run_id}-step-2",
        f"ai-office-run-{run_id}-step-2",
    ]
    evidence_result = runner.invoke(
        app,
        ["workflows", "execution-evidence", run_id, "--run-store", str(run_store)],
    )
    assert evidence_result.exit_code == 0, evidence_result.stderr
    attempts = json.loads(evidence_result.stdout)["attempts"]
    assert [item["provider"] for item in attempts] == [
        "zen-responses",
        "example-chat",
        "example-chat",
    ]
    assert [(item["step_id"], item["step_index"]) for item in attempts] == [
        ("draft", 1),
        ("review", 2),
        ("review", 2),
    ]


@pytest.mark.parametrize(
    ("response_status", "failure_category"),
    (
        ("incomplete", "invalid_output"),
        ("failed", "invalid_output"),
        ("in_progress", "invalid_output"),
        (None, "invalid_response"),
        ("unknown", "invalid_output"),
    ),
)
def test_non_completed_responses_stop_without_artifact_and_require_recovery(
    tmp_path: Path,
    monkeypatch,
    response_status: str | None,
    failure_category: str,
) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_registry(registry)
    run_store = tmp_path / "runs"
    status_name = "missing" if response_status is None else response_status
    run_id = f"responses-status-{status_name}"
    transport_calls: list[object] = []

    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: OpenAIApiKey(value=SecretStr("offline-key")),
    )

    def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        transport_calls.append(request)
        response: dict[str, object] = {
            "id": f"response-{status_name}",
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "# Unfinished output"}
                    ],
                }
            ],
        }
        if response_status is not None:
            response["status"] = response_status
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", f"request-{status_name}"),),
            json.dumps(response).encode("utf-8"),
        )

    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", fake_transport
    )
    args = _start_args(
        run_store,
        registry,
        run_id,
        "zen-responses",
        "test-responses-model",
    )
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)

    result = runner.invoke(
        app, args + _approval_args(preview, f"responses-{status_name}")
    )
    assert result.exit_code == 1, (result.stderr, result.stdout, result.exception)
    result_value = json.loads(result.stdout)
    assert result_value["status"] == "persisted_failure"
    assert result_value["failure_category"] == failure_category
    assert "output" not in result_value

    evidence_result = runner.invoke(
        app,
        [
            "workflows",
            "execution-evidence",
            run_id,
            "--run-store",
            str(run_store),
        ],
    )
    assert evidence_result.exit_code == 0, evidence_result.stderr
    attempt = json.loads(evidence_result.stdout)["attempts"][0]
    assert attempt["normalized_result_category"] == failure_category
    assert attempt["final_step_outcome_linked"] is True
    normalized = load_normalized_result_evidence(
        run_store, run_id, attempt["attempt_id"]
    )
    assert normalized.failure_category == failure_category
    assert normalized.status_code is None
    assert normalized.provider_error_type is None
    assert normalized.provider_error_code is None
    raw = load_raw_response_evidence(run_store, run_id, attempt["attempt_id"])
    assert raw.status_code == 200
    assert json.loads(raw.body).get("status") == response_status

    artifacts = runner.invoke(
        app,
        ["workflows", "artifacts", run_id, "--run-store", str(run_store)],
    )
    assert artifacts.exit_code == 0, artifacts.stderr
    assert json.loads(artifacts.stdout)["artifacts"] == []
    recovery = assess_workflow_recovery(run_store, run_id)
    assert recovery.eligible is True
    assert recovery.action == "retry_failed"
    assert recovery.reason == "terminal_failure_requires_explicit_retry"
    assert len(transport_calls) == 1


def test_chat_completions_api_error_is_normalized_without_vendor_fields() -> None:
    raw = OpenAIResponsesRawHttpResponse(
        429,
        "rate limited",
        (("x-request-id", "request-429"),),
        json.dumps(
            {
                "error": {
                    "message": "rate limit",
                    "type": "rate_limit_error",
                    "code": "rate_limit",
                    "vendor_debug": {"secret": "ignored"},
                }
            }
        ).encode("utf-8"),
    )
    result = normalize_openai_chat_completions_raw_response(
        raw, provider="example-chat"
    )
    assert result.provider == "example-chat"
    assert result.category == "api_error"
    assert result.status_code == 429
    assert result.request_id == "request-429"
    assert result.provider_error_type == "rate_limit_error"
    assert result.provider_error_code == "rate_limit"


@pytest.mark.parametrize(
    ("finish_reason", "failure_message"),
    (
        (
            "length",
            "Chat Completions output was truncated before normal completion",
        ),
        (
            "content_filter",
            "Chat Completions output was stopped by content filtering",
        ),
        (
            "tool_calls",
            "Chat Completions returned an unsupported finish reason",
        ),
    ),
)
def test_non_normal_chat_completion_stops_without_artifact_and_requires_recovery(
    tmp_path: Path,
    monkeypatch,
    finish_reason: str,
    failure_message: str,
) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_registry(registry)
    run_store = tmp_path / "runs"
    run_id = f"chat-finish-{finish_reason}"
    transport_calls: list[object] = []

    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: OpenAIApiKey(value=SecretStr("offline-key")),
    )

    def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        transport_calls.append(request)
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", f"request-{finish_reason}"),),
            json.dumps(
                {
                    "id": f"chatcmpl-{finish_reason}",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": (
                                    None
                                    if finish_reason == "content_filter"
                                    else "# Incomplete output"
                                ),
                            },
                            "finish_reason": finish_reason,
                        }
                    ],
                }
            ).encode("utf-8"),
        )

    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", fake_transport
    )
    args = _start_args(
        run_store, registry, run_id, "example-chat", "test-chat-model"
    )
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)

    result = runner.invoke(
        app, args + _approval_args(preview, f"finish-{finish_reason}")
    )
    assert result.exit_code == 1, (result.stderr, result.stdout, result.exception)
    result_value = json.loads(result.stdout)
    assert result_value["status"] == "persisted_failure"
    assert result_value["failure_category"] == "invalid_output"
    assert "output" not in result_value

    evidence_result = runner.invoke(
        app,
        [
            "workflows",
            "execution-evidence",
            run_id,
            "--run-store",
            str(run_store),
        ],
    )
    assert evidence_result.exit_code == 0, evidence_result.stderr
    attempt = json.loads(evidence_result.stdout)["attempts"][0]
    assert attempt["normalized_result_category"] == "invalid_output"
    assert attempt["final_step_outcome_linked"] is True
    normalized = load_normalized_result_evidence(
        run_store, run_id, attempt["attempt_id"]
    )
    assert normalized.failure_category == "invalid_output"
    assert normalized.failure_message == failure_message
    assert normalized.provider_error_type is None
    assert normalized.provider_error_code is None
    assert normalized.status_code is None
    raw = load_raw_response_evidence(run_store, run_id, attempt["attempt_id"])
    assert raw.status_code == 200
    assert json.loads(raw.body)["choices"][0]["finish_reason"] == finish_reason

    artifacts = runner.invoke(
        app,
        ["workflows", "artifacts", run_id, "--run-store", str(run_store)],
    )
    assert artifacts.exit_code == 0, artifacts.stderr
    assert json.loads(artifacts.stdout)["artifacts"] == []
    recovery = assess_workflow_recovery(run_store, run_id)
    assert recovery.eligible is True
    assert recovery.action == "retry_failed"
    assert recovery.reason == "terminal_failure_requires_explicit_retry"
    assert len(transport_calls) == 1


def test_chat_completions_tools_are_rejected_before_serialization() -> None:
    request = ModelInvocationRequest(
        model="chat-model",
        system_instructions="system",
        task_instructions="task",
        allowed_tools=("web_search",),
    )
    with pytest.raises(ValueError, match="does not support tools"):
        serialize_openai_chat_completions_request(request)


def test_registry_change_invalidates_preview_before_credential(
    tmp_path: Path, monkeypatch
) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_registry(registry)
    args = _start_args(
        tmp_path / "runs",
        registry,
        "stale-config",
        "zen-responses",
        "test-responses-model",
    )
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    _write_registry(registry, responses_endpoint="https://opencode.ai/zen/v2/responses")
    key_calls: list[int] = []
    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: key_calls.append(1),
    )
    result = runner.invoke(app, args + _approval_args(preview, "stale"))
    assert result.exit_code == 2
    assert "expected preview does not match current step" in result.stderr
    assert key_calls == []
