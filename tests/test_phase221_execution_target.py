"""Focused Phase 221 tests for target-bound OpenAI-compatible execution."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
import ai_office.providers.openai.responses_transport as responses_transport
from ai_office.cli import app
from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    LOCAL_OMNIROUTE_EXECUTION_TARGET,
    ModelExecutionTarget,
    ModelExecutionTargetError,
    execution_target_fingerprint,
    execution_target_for_name,
    validate_execution_target_for_provider,
)
from ai_office.invocation import (
    ModelInvocationExecutionApprovalError,
    ModelInvocationRequest,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
    validate_model_invocation_execution_approval,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
    execute_openai_model_invocation,
    load_api_key_for_execution_target,
    send_openai_responses_http_request,
)

runner = CliRunner()


def request(model: str = "openClaw") -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model=model,
        system_instructions="system",
        task_instructions="task",
        allowed_tools=(),
    )


def api_key(value: str = "synthetic-omniroute-secret") -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr(value))


def raw_response(status_code: int = 200) -> OpenAIResponsesRawHttpResponse:
    if status_code == 200:
        body = {
            "id": "response-omni",
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "synthetic output"}],
                }
            ],
        }
    else:
        body = {
            "error": {
                "message": "synthetic failure",
                "type": "synthetic_error",
                "param": None,
                "code": None,
            }
        }
    return OpenAIResponsesRawHttpResponse(
        status_code=status_code,
        reason="synthetic",
        headers=(("x-request-id", "request-omni"),),
        body=json.dumps(body).encode("utf-8"),
    )


def omni_approval(
    invocation: ModelInvocationRequest,
):
    return approve_model_invocation_execution(
        invocation,
        (),
        provider="omniroute",
        approved_by="operator",
        approval_id="approval-omni",
        execution_target=LOCAL_OMNIROUTE_EXECUTION_TARGET,
    )


def test_builtin_execution_targets_are_exact_and_secret_free() -> None:
    assert DIRECT_OPENAI_EXECUTION_TARGET == ModelExecutionTarget(
        provider="openai",
        protocol="openai-responses",
        base_url="https://api.openai.com/v1/responses",
        credential_environment_variable="OPENAI_API_KEY",
        allow_loopback_http=False,
    )
    assert LOCAL_OMNIROUTE_EXECUTION_TARGET == ModelExecutionTarget(
        provider="omniroute",
        protocol="openai-responses",
        base_url="http://127.0.0.1:20128/v1/responses",
        credential_environment_variable="OMNIROUTE_API_KEY",
        allow_loopback_http=True,
    )
    assert execution_target_for_name("openai") is DIRECT_OPENAI_EXECUTION_TARGET
    assert execution_target_for_name("omniroute") is LOCAL_OMNIROUTE_EXECUTION_TARGET
    assert "secret" not in repr(LOCAL_OMNIROUTE_EXECUTION_TARGET)
    assert LOCAL_OMNIROUTE_EXECUTION_TARGET.descriptor() == {
        "allow_loopback_http": True,
        "credential_environment_variable": "OMNIROUTE_API_KEY",
        "endpoint": "http://127.0.0.1:20128/v1/responses",
        "protocol": "openai-responses",
        "provider": "omniroute",
    }


@pytest.mark.parametrize("name", ["", "openai-compatible", "OpenAI", 1])
def test_unsupported_target_selection_is_rejected(name: object) -> None:
    with pytest.raises(ModelExecutionTargetError):
        execution_target_for_name(name)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutated",
    [
        replace(LOCAL_OMNIROUTE_EXECUTION_TARGET, provider="openai"),
        replace(
            LOCAL_OMNIROUTE_EXECUTION_TARGET,
            base_url="https://api.openai.com/v1/responses",
        ),
        replace(
            LOCAL_OMNIROUTE_EXECUTION_TARGET,
            credential_environment_variable="OPENAI_API_KEY",
        ),
        replace(LOCAL_OMNIROUTE_EXECUTION_TARGET, allow_loopback_http=False),
    ],
)
def test_noncanonical_target_mutations_are_rejected(
    mutated: ModelExecutionTarget,
) -> None:
    with pytest.raises(ModelExecutionTargetError):
        validate_execution_target_for_provider(mutated)


def test_mutated_protocol_is_rejected_even_when_object_is_bypassed() -> None:
    mutated = object.__new__(ModelExecutionTarget)
    object.__setattr__(mutated, "provider", "omniroute")
    object.__setattr__(mutated, "protocol", "other-protocol")
    object.__setattr__(mutated, "base_url", LOCAL_OMNIROUTE_EXECUTION_TARGET.base_url)
    object.__setattr__(mutated, "credential_environment_variable", "OMNIROUTE_API_KEY")
    object.__setattr__(mutated, "allow_loopback_http", True)

    with pytest.raises(ModelExecutionTargetError):
        validate_model_execution_target_for_test(mutated)


def validate_model_execution_target_for_test(target: object) -> object:
    """Keep the malformed-object assertion independent of provider selection."""
    from ai_office.execution_target import validate_model_execution_target

    return validate_model_execution_target(target)


def test_target_fingerprint_covers_every_execution_identity_field() -> None:
    baseline = execution_target_fingerprint(LOCAL_OMNIROUTE_EXECUTION_TARGET)
    assert baseline != execution_target_fingerprint(
        replace(LOCAL_OMNIROUTE_EXECUTION_TARGET, allow_loopback_http=False)
    )
    assert baseline != execution_target_fingerprint(
        replace(
            LOCAL_OMNIROUTE_EXECUTION_TARGET,
            credential_environment_variable="OTHER_KEY",
        )
    )
    assert baseline != execution_target_fingerprint(
        replace(LOCAL_OMNIROUTE_EXECUTION_TARGET, base_url="https://example.test")
    )


def test_omniroute_approval_binds_target_and_request() -> None:
    invocation = request()
    approval = omni_approval(invocation)

    assert approval.provider == "omniroute"
    assert approval.execution_target is LOCAL_OMNIROUTE_EXECUTION_TARGET
    assert approval.request_fingerprint == build_model_invocation_execution_fingerprint(
        invocation, (), LOCAL_OMNIROUTE_EXECUTION_TARGET
    )
    validate_model_invocation_execution_approval(
        invocation,
        (),
        approval,
        provider="omniroute",
        execution_target=LOCAL_OMNIROUTE_EXECUTION_TARGET,
    )


@pytest.mark.parametrize(
    "changed_target",
    [
        DIRECT_OPENAI_EXECUTION_TARGET,
        replace(LOCAL_OMNIROUTE_EXECUTION_TARGET, allow_loopback_http=False),
        replace(
            LOCAL_OMNIROUTE_EXECUTION_TARGET,
            credential_environment_variable="OPENAI_API_KEY",
        ),
        replace(
            LOCAL_OMNIROUTE_EXECUTION_TARGET,
            base_url="https://api.openai.com/v1/responses",
        ),
    ],
)
def test_existing_omniroute_approval_rejects_target_replay_mutation(
    changed_target: ModelExecutionTarget,
) -> None:
    invocation = request()
    approval = omni_approval(invocation)

    with pytest.raises(ModelInvocationExecutionApprovalError):
        validate_model_invocation_execution_approval(
            invocation,
            (),
            approval,
            provider=changed_target.provider,
            execution_target=changed_target,
        )


def test_legacy_direct_openai_approval_remains_valid() -> None:
    invocation = request()
    approval = approve_model_invocation_execution(
        invocation,
        (),
        provider="openai",
        approved_by="operator",
        approval_id="approval-openai",
    )

    validate_model_invocation_execution_approval(
        invocation,
        (),
        approval,
        provider="openai",
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )


def test_target_aware_environment_loader_uses_name_only_and_masks_secret() -> None:
    loaded = load_api_key_for_execution_target(
        LOCAL_OMNIROUTE_EXECUTION_TARGET,
        {"OMNIROUTE_API_KEY": "masked-secret-value"},
    )

    assert loaded.value.get_secret_value() == "masked-secret-value"
    assert "masked-secret-value" not in repr(loaded)
    assert "masked-secret-value" not in str(loaded)


@pytest.mark.parametrize("environment", [{}, {"OMNIROUTE_API_KEY": ""}])
def test_missing_or_blank_omniroute_credential_fails_safely(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError) as error:
        load_api_key_for_execution_target(LOCAL_OMNIROUTE_EXECUTION_TARGET, environment)

    assert "masked-secret" not in str(error.value)


class _FakeHttpConnection:
    def __init__(self) -> None:
        self.requests: list[tuple[object, ...]] = []
        self.headers: list[tuple[str, str]] = []
        self.bodies: list[bytes] = []
        self.closed = False

    def putrequest(self, method: str, target: str, **kwargs: object) -> None:
        self.requests.append((method, target, kwargs))

    def putheader(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def endheaders(self, body: bytes) -> None:
        self.bodies.append(body)

    def getresponse(self) -> object:
        class _Response:
            status = 200
            reason = "OK"

            @staticmethod
            def getheaders() -> list[tuple[str, str]]:
                return [("x-request-id", "request-loopback")]

            @staticmethod
            def read() -> bytes:
                return b"synthetic body"

        return _Response()

    def close(self) -> None:
        self.closed = True


def test_canonical_loopback_http_transport_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeHttpConnection()
    created: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        responses_transport,
        "_create_http_connection",
        lambda host, port: (created.append((host, port)) or connection),
    )
    request_value = OpenAIResponsesAuthenticatedHttpRequest(
        "POST",
        LOCAL_OMNIROUTE_EXECUTION_TARGET.endpoint,
        (("Authorization", "Bearer masked"),),
        "{}",
    )

    result = send_openai_responses_http_request(request_value)

    assert result.status_code == 200
    assert created == [("127.0.0.1", 20128)]
    assert connection.requests[0][0:2] == ("POST", "/v1/responses")
    assert connection.closed is True


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1/responses",
        "http://192.168.1.1/v1/responses",
        "http://0.0.0.0/v1/responses",
        "http://127.0.0.2/v1/responses",
        "http://user@127.0.0.1/v1/responses",
        "http://[::1]/v1/responses",
    ],
)
def test_remote_or_confusing_plaintext_http_is_rejected_before_connection(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    created = False

    def fail_if_called(*args: object) -> None:
        nonlocal created
        created = True

    monkeypatch.setattr(responses_transport, "_create_http_connection", fail_if_called)
    request_value = OpenAIResponsesAuthenticatedHttpRequest("POST", url, (), "{}")

    with pytest.raises(ValueError):
        send_openai_responses_http_request(request_value)

    assert created is False


def test_omniroute_execution_reuses_responses_stack_and_preserves_alias() -> None:
    invocation = request("openClaw")
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []

    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return raw_response()

    result = execute_openai_model_invocation(
        invocation,
        (),
        api_key(),
        omni_approval(invocation),
        transport=transport,
    )

    assert result.provider == "omniroute"  # type: ignore[union-attr]
    assert result.text == "synthetic output"  # type: ignore[union-attr]
    assert len(calls) == 1
    assert calls[0].url == LOCAL_OMNIROUTE_EXECUTION_TARGET.endpoint
    assert json.loads(calls[0].body)["model"] == "openClaw"
    assert "synthetic-omniroute-secret" not in repr(result)


def test_omniroute_api_failure_keeps_truthful_provider_identity() -> None:
    invocation = request()
    result = execute_openai_model_invocation(
        invocation,
        (),
        api_key(),
        omni_approval(invocation),
        transport=lambda _: raw_response(500),
    )

    assert result.provider == "omniroute"  # type: ignore[union-attr]
    assert result.category == "api_error"  # type: ignore[union-attr]


def _write_cli_project(tmp_path: Path, *, step_count: int = 2) -> dict[str, Path]:
    workflows = tmp_path / "workflows"
    employees = tmp_path / "employees"
    workflows.mkdir()
    employees.mkdir()
    (employees / "employee.yaml").write_text(
        """id: general-researcher
name: General Researcher
role: Organizes information.
instructions: Work on the assigned step.
model: openClaw
allowed_tools: []
""",
        encoding="utf-8",
    )
    steps = [
        "  - id: step-one\n"
        "    name: Step One\n"
        "    employee: general-researcher\n"
        "    instructions: Do step one."
    ]
    if step_count > 1:
        steps.append(
            "  - id: step-two\n"
            "    name: Step Two\n"
            "    employee: general-researcher\n"
            "    instructions: Do step two."
        )
    (workflows / "workflow.yaml").write_text(
        "id: target-workflow\n"
        "name: Target Workflow\n"
        "description: Target test workflow.\n"
        "steps:\n"
        + "\n".join(steps)
        + "\n",
        encoding="utf-8",
    )
    return {
        "workflows": workflows,
        "employees": employees,
        "state": tmp_path / "state.json",
        "events": tmp_path / "events.jsonl",
    }


def _cli_args(operation: str, paths: dict[str, Path]) -> list[str]:
    return [
        "workflows",
        operation,
        "target-workflow",
        "--state-path",
        str(paths["state"]),
        "--events-path",
        str(paths["events"]),
        "--directory",
        str(paths["workflows"]),
        "--employees-directory",
        str(paths["employees"]),
    ]


def _approval_args(preview: dict[str, object]) -> list[str]:
    return [
        "--approve-preparation",
        "--approve-execution",
        "--approved-by",
        "operator",
        "--approval-id",
        "approval-cli",
        "--expected-step-id",
        str(preview["step_id"]),
        "--expected-step-index",
        str(preview["step_index"]),
        "--expected-employee-id",
        str(preview["employee_id"]),
        "--expected-request-fingerprint",
        str(preview["request_fingerprint"]),
    ]


def test_cli_omniroute_preview_is_secret_free_and_network_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_cli_project(tmp_path)
    key_calls: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: key_calls.append(target),
    )

    result = runner.invoke(
        app,
        _cli_args("start", paths)
        + ["--preview-only", "--execution-target", "omniroute"],
    )

    assert result.exit_code == 0
    preview = json.loads(result.stdout)
    assert preview["execution_target"] == {
        "allow_loopback_http": True,
        "credential_environment_variable": "OMNIROUTE_API_KEY",
        "endpoint": "http://127.0.0.1:20128/v1/responses",
        "protocol": "openai-responses",
        "provider": "omniroute",
    }
    assert key_calls == []
    assert not paths["state"].exists()
    assert not paths["events"].exists()


def test_cli_unsupported_target_is_rejected_before_credential_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_cli_project(tmp_path)
    key_calls: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "load_api_key_for_execution_target",
        lambda target: key_calls.append(target),
    )

    result = runner.invoke(
        app,
        _cli_args("start", paths)
        + ["--preview-only", "--execution-target", "unsupported"],
    )

    assert result.exit_code == 2
    assert "execution target is invalid" in result.stderr
    assert key_calls == []


def test_cli_fresh_and_continuation_omniroute_execution_then_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_cli_project(tmp_path, step_count=2)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "synthetic-omniroute-secret")
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []

    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return raw_response()

    monkeypatch.setattr(cli_module, "send_openai_responses_http_request", transport)
    preview_result = runner.invoke(
        app,
        _cli_args("start", paths)
        + ["--preview-only", "--execution-target", "omniroute"],
    )
    preview = json.loads(preview_result.stdout)
    start_result = runner.invoke(
        app,
        _cli_args("start", paths)
        + ["--execution-target", "omniroute"]
        + _approval_args(preview),
    )
    assert start_result.exit_code == 0, start_result.stderr
    assert json.loads(start_result.stdout)["status"] == "prepare_next_step"
    assert calls[0].url == LOCAL_OMNIROUTE_EXECUTION_TARGET.endpoint

    continue_preview_result = runner.invoke(
        app,
        _cli_args("continue", paths)
        + ["--preview-only", "--execution-target", "omniroute"],
    )
    continue_preview = json.loads(continue_preview_result.stdout)
    assert continue_preview["execution_target"]["provider"] == "omniroute"
    continue_result = runner.invoke(
        app,
        _cli_args("continue", paths)
        + ["--execution-target", "omniroute"]
        + _approval_args(continue_preview),
    )
    assert continue_result.exit_code == 0, continue_result.stderr
    assert json.loads(continue_result.stdout)["status"] == "workflow_complete"

    result_command = runner.invoke(app, _cli_args("result", paths))
    assert result_command.exit_code == 0, result_command.stderr
    assert json.loads(result_command.stdout)["status"] == "workflow_complete"
    assert len(calls) == 2
    assert all(call.url == LOCAL_OMNIROUTE_EXECUTION_TARGET.endpoint for call in calls)
    events = paths["events"].read_text(encoding="utf-8")
    assert events.count('"provider":"omniroute"') == 2
    assert "synthetic-omniroute-secret" not in result_command.stdout
