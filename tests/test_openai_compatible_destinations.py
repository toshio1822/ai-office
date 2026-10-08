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


def _start_args(
    run_store: Path, registry: Path, run_id: str, destination: str, model: str
) -> list[str]:
    return [
        "workflows",
        "start",
        "write-internal-ai-guide",
        "--run-id",
        run_id,
        "--run-input",
        RUN_INPUT,
        "--run-store",
        str(run_store),
        "--directory",
        str(ROOT / "workflows"),
        "--employees-directory",
        str(ROOT / "employees"),
        "--execution-target",
        destination,
        "--execution-destinations",
        str(registry),
        "--execution-model",
        model,
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
    assert manifest["schema_version"] == "workflow-run-manifest.v4"
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
    assert "differs from the Run Manifest" in switched.stderr


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
