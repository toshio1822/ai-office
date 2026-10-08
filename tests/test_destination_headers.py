"""Offline proof for Issue #708 safe destination HTTP headers."""

import json
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)
from ai_office.execution_destination import (
    load_execution_destination_registry,
)
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse
from ai_office.request_headers import (
    SESSION_MARKER,
    ConfiguredRequestHeaderError,
    parse_configured_request_headers,
    session_value_for_run,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_INPUT = "社内向けの短い生成AI利用ガイドをMarkdownで作成してください。"
runner = CliRunner()


def _write_registry(path: Path, headers: object | None = None) -> None:
    entry: dict[str, object] = {
        "endpoint": "https://opencode.ai/zen/go/v1/responses",
        "protocol": "openai-responses",
        "credential": "OPENCODE_API_KEY",
        "models": ["gpt-6-luna"],
    }
    if headers is not None:
        entry["headers"] = headers
    path.write_text(
        yaml.safe_dump(
            {"destinations": {"opencode-go": entry}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _chat_registry(path: Path, headers: object | None = None) -> None:
    entry: dict[str, object] = {
        "endpoint": "https://opencode.ai/zen/go/v1/chat/completions",
        "protocol": "openai-chat-completions",
        "credential": "OPENCODE_API_KEY",
        "models": ["glm-5.3-flash"],
    }
    if headers is not None:
        entry["headers"] = headers
    path.write_text(
        yaml.safe_dump(
            {"destinations": {"opencode-go-chat": entry}},
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


def _responses_body() -> bytes:
    return json.dumps(
        {
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
    ).encode("utf-8")


def _chat_body() -> bytes:
    return json.dumps(
        {
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
    ).encode("utf-8")


def _headers_of(request: object) -> dict[str, str]:
    return {name.lower(): value for name, value in request.headers}


# ---------------------------------------------------------------------------
# Header policy parsing and validation


def test_two_destinations_can_declare_different_header_policies(tmp_path: Path) -> None:
    responses = tmp_path / "responses.yaml"
    chat = tmp_path / "chat.yaml"
    _write_registry(
        responses, {"User-Agent": "ai-office/1.0", "x-opencode-session": SESSION_MARKER}
    )
    _chat_registry(
        chat, {"User-Agent": "ai-office-docs/2.0"}
    )
    responses_target = load_execution_destination_registry(responses).resolve(
        "opencode-go"
    ).target
    chat_target = load_execution_destination_registry(chat).resolve(
        "opencode-go-chat"
    ).target
    assert [h.name for h in responses_target.request_headers] == [
        "User-Agent",
        "x-opencode-session",
    ]
    assert [h.name for h in chat_target.request_headers] == ["User-Agent"]
    assert responses_target.configuration_fingerprint != (
        chat_target.configuration_fingerprint
    )


@pytest.mark.parametrize(
    "name",
    [
        "Authorization",
        "Proxy-Authorization",
        "Host",
        "Content-Length",
        "Content-Type",
        "Transfer-Encoding",
        "Connection",
        "Keep-Alive",
        "Cookie",
        "Set-Cookie",
        "X-Forwarded-For",
        "X-Forwarded-Host",
        "proxy-connection",
        "x-forwarded-proto",
        "Via",
        "Expect",
        "Te",
        "Trailer",
        "Upgrade",
    ],
)
def test_reserved_header_names_are_rejected(name: str) -> None:
    with pytest.raises(ConfiguredRequestHeaderError):
        parse_configured_request_headers({name: "value"})


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a\rb",
        "a\nb",
        "a\r\nb",
        "value with\tcontrol",
        "{unknown}",
        "{run_session}extra",
        "x{run_session}",
        "  leading-space",
        "trailing-space  ",
    ],
)
def test_unsafe_header_values_are_rejected(value: str) -> None:
    with pytest.raises(ConfiguredRequestHeaderError):
        parse_configured_request_headers({"User-Agent": value})


def test_duplicate_and_case_conflicting_header_names_are_rejected() -> None:
    with pytest.raises(ConfiguredRequestHeaderError):
        parse_configured_request_headers({"User-Agent": "a", "user-agent": "b"})


def test_oversized_header_values_are_rejected() -> None:
    with pytest.raises(ConfiguredRequestHeaderError):
        parse_configured_request_headers({"User-Agent": "x" * 513})


def test_session_value_is_stable_run_scoped_and_non_masquerading() -> None:
    assert session_value_for_run("m1-run-001") == "ai-office-run-m1-run-001"
    assert session_value_for_run("m1-run-001") == session_value_for_run("m1-run-001")
    assert session_value_for_run("m1-run-001") != session_value_for_run("m1-run-002")
    assert "opencode" not in session_value_for_run("m1-run-001").lower()
    assert "codex" not in session_value_for_run("m1-run-001").lower()


# ---------------------------------------------------------------------------
# Fake-transport execution with configured headers


@pytest.mark.parametrize(
    ("registry_writer", "destination", "model", "protocol"),
    (
        (_write_registry, "opencode-go", "gpt-6-luna", "openai-responses"),
        (
            _chat_registry,
            "opencode-go-chat",
            "glm-5.3-flash",
            "openai-chat-completions",
        ),
    ),
)
def test_m1_run_sends_configured_headers_for_both_protocols(
    tmp_path: Path,
    monkeypatch,
    registry_writer,
    destination: str,
    model: str,
    protocol: str,
) -> None:
    registry = tmp_path / "destinations.yaml"
    registry_writer(
        registry,
        {"User-Agent": "ai-office/1.0", "x-opencode-session": SESSION_MARKER},
    )
    run_store = tmp_path / "runs"
    run_id = f"header-{destination}"
    sent_headers: list[dict[str, str]] = []

    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: OpenAIApiKey(value=SecretStr("offline-key")),
    )

    def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        sent_headers.append(_headers_of(request))
        body = _responses_body() if protocol == "openai-responses" else _chat_body()
        return OpenAIResponsesRawHttpResponse(
            200, "synthetic", (("x-request-id", "offline-request"),), body
        )

    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", fake_transport
    )
    args = _start_args(run_store, registry, run_id, destination, model)
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    assert preview["execution_target"]["request_headers"] == [
        {"name": "User-Agent", "value": "ai-office/1.0"},
        {"name": "x-opencode-session", "value": SESSION_MARKER},
    ]
    assert not sent_headers

    result = runner.invoke(app, args + _approval_args(preview, destination))
    assert result.exit_code == 0, (result.stderr, result.stdout, result.exception)
    assert json.loads(result.stdout)["status"] == "workflow_complete"
    assert len(sent_headers) == 1
    headers = sent_headers[0]
    assert headers["user-agent"] == "ai-office/1.0"
    assert headers["x-opencode-session"] == f"ai-office-run-{run_id}"
    assert headers["authorization"] == "Bearer offline-key"


def test_header_change_invalidates_preview_before_credential(
    tmp_path: Path, monkeypatch
) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_registry(
        registry,
        {"User-Agent": "ai-office/1.0", "x-opencode-session": SESSION_MARKER},
    )
    args = _start_args(
        tmp_path / "runs", registry, "header-stale", "opencode-go", "gpt-6-luna"
    )
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    # Change only the static User-Agent after preview.
    _write_registry(
        registry,
        {"User-Agent": "ai-office/2.0", "x-opencode-session": SESSION_MARKER},
    )
    key_calls: list[int] = []
    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: key_calls.append(1),
    )
    result = runner.invoke(app, args + _approval_args(preview, "header-stale"))
    assert result.exit_code == 2
    assert "expected preview does not match current step" in result.stderr
    assert key_calls == []


def test_continuation_cannot_switch_header_policy(tmp_path: Path, monkeypatch) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_registry(
        registry,
        {"User-Agent": "ai-office/1.0", "x-opencode-session": SESSION_MARKER},
    )
    run_store = tmp_path / "runs"
    run_id = "header-continue"
    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: OpenAIApiKey(value=SecretStr("offline-key")),
    )
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        lambda request: OpenAIResponsesRawHttpResponse(
            200, "synthetic", (("x-request-id", "offline-request"),), _responses_body()
        ),
    )
    args = _start_args(run_store, registry, run_id, "opencode-go", "gpt-6-luna")
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    result = runner.invoke(app, args + _approval_args(preview, "continue"))
    assert result.exit_code == 0, result.stderr

    # A changed header policy must be rejected on continuation before transport.
    _write_registry(
        registry,
        {"User-Agent": "ai-office/9.9", "x-opencode-session": SESSION_MARKER},
    )
    switched = runner.invoke(
        app,
        [
            "workflows",
            "continue",
            run_id,
            "--run-store",
            str(run_store),
            "--execution-target",
            "opencode-go",
            "--execution-destinations",
            str(registry),
            "--preview-only",
        ],
    )
    assert switched.exit_code == 2
    assert "differs from the Run Manifest" in switched.stderr


# ---------------------------------------------------------------------------
# Manifest durability and historical compatibility


def test_manifest_round_trip_preserves_request_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_registry(
        registry,
        {"User-Agent": "ai-office/1.0", "x-opencode-session": SESSION_MARKER},
    )
    run_store = tmp_path / "runs"
    run_id = "header-manifest"
    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: OpenAIApiKey(value=SecretStr("offline-key")),
    )
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        lambda request: OpenAIResponsesRawHttpResponse(
            200, "synthetic", (("x-request-id", "offline-request"),), _responses_body()
        ),
    )
    args = _start_args(run_store, registry, run_id, "opencode-go", "gpt-6-luna")
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    result = runner.invoke(app, args + _approval_args(preview, "manifest"))
    assert result.exit_code == 0, result.stderr

    store = WorkflowRunManifestStore(run_store)
    manifest = load_workflow_run_manifest(store, run_id)
    assert manifest.execution_destination is not None
    headers = manifest.execution_destination.request_headers
    assert [(h.name, h.value) for h in headers] == [
        ("User-Agent", "ai-office/1.0"),
        ("x-opencode-session", SESSION_MARKER),
    ]
    # The persisted record binds the header policy exactly.
    raw = json.loads(
        (run_store / f"{run_id}.manifest.json").read_text(encoding="utf-8")
    )
    assert raw["execution_destination"]["request_headers"] == [
        ["User-Agent", "ai-office/1.0"],
        ["x-opencode-session", SESSION_MARKER],
    ]


def test_headerless_v4_manifest_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "destinations.yaml"
    _write_registry(registry)  # no headers key
    run_store = tmp_path / "runs"
    run_id = "headerless"
    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: OpenAIApiKey(value=SecretStr("offline-key")),
    )
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        lambda request: OpenAIResponsesRawHttpResponse(
            200, "synthetic", (("x-request-id", "offline-request"),), _responses_body()
        ),
    )
    args = _start_args(run_store, registry, run_id, "opencode-go", "gpt-6-luna")
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    result = runner.invoke(app, args + _approval_args(preview, "headerless"))
    assert result.exit_code == 0, result.stderr

    manifest = load_workflow_run_manifest(WorkflowRunManifestStore(run_store), run_id)
    assert manifest.execution_destination is not None
    assert manifest.execution_destination.request_headers == ()
    raw = json.loads(
        (run_store / f"{run_id}.manifest.json").read_text(encoding="utf-8")
    )
    assert "request_headers" not in raw["execution_destination"]
