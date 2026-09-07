"""Focused no-network tests for Phase 226 response observability."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    LOCAL_OMNIROUTE_EXECUTION_TARGET,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesApiErrorResponse,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesInvalidResponseError,
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesSuccessResponse,
    build_openai_responses_response_diagnostics,
    classify_openai_responses_body,
    execute_openai_model_invocation,
    extract_openai_responses_content_type,
    parse_openai_responses_http_response,
)
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    WorkflowExecutionState,
    transition_workflow_execution_from_step_result,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    build_runtime_step_event_dict,
    load_workflow_execution_history,
    persist_workflow_execution_transition,
)

_SECRET = "synthetic-provider-secret"


@pytest.mark.parametrize(
    ("body", "content_type", "expected"),
    [
        (b"", None, "empty"),
        (b'{"id":"resp"}', "application/json", "json"),
        (b"data: {\"type\":\"delta\"}\n\n", "text/event-stream", "sse"),
        (b"<!doctype html><html></html>", "text/html", "html"),
        (b"upstream unavailable", "text/plain", "plaintext"),
        (b"{\"id\":", "application/json", "malformed_json"),
        (b"\xff", "application/octet-stream", "non_utf8"),
    ],
)
def test_classifier_distinguishes_required_body_kinds(
    body: bytes,
    content_type: str | None,
    expected: str,
) -> None:
    assert classify_openai_responses_body(body, content_type=content_type) == expected


@pytest.mark.parametrize(
    "body",
    [b"{}", b"[]", b'"scalar"', b"42", b"null"],
)
def test_valid_json_values_are_json_for_diagnostic_purposes(body: bytes) -> None:
    assert classify_openai_responses_body(body) == "json"


def test_metadata_extracts_only_case_insensitive_content_type_and_byte_length() -> None:
    body = "日本語".encode()
    headers = (
        ("X-Provider-Secret", _SECRET),
        ("cOnTeNt-TyPe", " application/json; charset=utf-8 "),
        ("Set-Cookie", "cookie-secret"),
    )

    diagnostics = build_openai_responses_response_diagnostics(502, headers, body)

    assert diagnostics.status_code == 502
    assert diagnostics.content_type == "application/json; charset=utf-8"
    assert diagnostics.body_length == len(body)
    assert diagnostics.body_kind == "plaintext"
    assert extract_openai_responses_content_type(headers) == (
        "application/json; charset=utf-8"
    )
    assert set(diagnostics.__dataclass_fields__) == {
        "status_code",
        "content_type",
        "body_length",
        "body_kind",
    }
    assert _SECRET not in repr(diagnostics)
    assert "cookie-secret" not in repr(diagnostics)


def test_missing_content_type_is_null() -> None:
    diagnostics = build_openai_responses_response_diagnostics(502, (), b"plain")

    assert diagnostics.content_type is None
    assert diagnostics.body_length == 5
    assert diagnostics.body_kind == "plaintext"


def test_valid_responses_success_and_api_error_remain_strict_data() -> None:
    success = parse_openai_responses_http_response(
        OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("Content-Type", "application/json"),),
            b'{"id":"resp","object":"response","status":"completed","output":[]}',
        )
    )
    api_error = parse_openai_responses_http_response(
        OpenAIResponsesRawHttpResponse(
            429,
            "synthetic",
            (("Content-Type", "application/json"),),
            b'{"error":{"message":"rate limited","type":"rate_limit"}}',
        )
    )

    assert isinstance(success, OpenAIResponsesSuccessResponse)
    assert success.response_id == "resp"
    assert isinstance(api_error, OpenAIResponsesApiErrorResponse)
    assert api_error.status_code == 429
    assert api_error.message == "rate limited"


def test_valid_json_with_invalid_responses_shape_is_json_but_not_success() -> None:
    response = OpenAIResponsesRawHttpResponse(
        200,
        "synthetic",
        (("Content-Type", "application/json"),),
        b"[]",
    )

    with pytest.raises(OpenAIResponsesInvalidResponseError) as caught:
        parse_openai_responses_http_response(response)

    assert caught.value.response_diagnostics is not None
    assert caught.value.response_diagnostics.body_kind == "json"
    assert str(caught.value) == "response JSON must be an object"


def test_invalid_utf8_parser_failure_carries_non_utf8_metadata() -> None:
    response = OpenAIResponsesRawHttpResponse(
        503,
        "synthetic",
        (("Content-Type", "application/octet-stream"),),
        b"\xff\xfe",
    )

    with pytest.raises(OpenAIResponsesInvalidResponseError) as caught:
        parse_openai_responses_http_response(response)

    assert str(caught.value) == "invalid UTF-8 response body"
    assert caught.value.response_diagnostics is not None
    assert caught.value.response_diagnostics.status_code == 503
    assert caught.value.response_diagnostics.body_length == 2
    assert caught.value.response_diagnostics.body_kind == "non_utf8"


@pytest.mark.parametrize(
    ("body", "content_type", "expected_kind"),
    [
        (b"", None, "empty"),
        (b"data: event\n\n", "text/event-stream", "sse"),
        (b"<html>gateway failure</html>", "text/html", "html"),
        (b"gateway failure", "text/plain", "plaintext"),
    ],
)
def test_non_json_wire_shapes_remain_invalid_responses(
    body: bytes,
    content_type: str | None,
    expected_kind: str,
) -> None:
    response = OpenAIResponsesRawHttpResponse(
        status_code=502,
        reason="synthetic",
        headers=() if content_type is None else (("Content-Type", content_type),),
        body=body,
    )

    with pytest.raises(OpenAIResponsesInvalidResponseError) as caught:
        parse_openai_responses_http_response(response)

    assert str(caught.value) == "invalid JSON response body"
    assert caught.value.response_diagnostics is not None
    assert caught.value.response_diagnostics.body_kind == expected_kind
    assert caught.value.response_diagnostics.status_code == 502
    assert caught.value.response_diagnostics.body_length == len(body)


def test_invalid_json_carries_exact_safe_metadata_without_body_or_headers() -> None:
    body = b'{"prompt":"' + _SECRET.encode() + b'",'
    response = OpenAIResponsesRawHttpResponse(
        status_code=502,
        reason="reason-secret",
        headers=(
            ("Authorization", "Bearer " + _SECRET),
            ("Content-Type", "application/json"),
            ("Set-Cookie", "cookie-secret"),
        ),
        body=body,
    )

    with pytest.raises(OpenAIResponsesInvalidResponseError) as caught:
        parse_openai_responses_http_response(response)

    diagnostics = caught.value.response_diagnostics
    assert diagnostics is not None
    assert diagnostics.status_code == 502
    assert diagnostics.content_type == "application/json"
    assert diagnostics.body_length == len(body)
    assert diagnostics.body_kind == "malformed_json"
    assert str(caught.value) == "invalid JSON response body"
    assert _SECRET not in repr(response)
    assert _SECRET not in str(response)
    assert "reason-secret" not in repr(response)
    assert "Set-Cookie" not in repr(response)


def _request() -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model="synthetic-model",
        system_instructions="synthetic system",
        task_instructions="synthetic task",
        allowed_tools=(),
    )


def _approval(
    request: ModelInvocationRequest,
    provider: str,
    target: object,
):
    return approve_model_invocation_execution(
        request,
        (),
        provider=provider,
        approved_by="synthetic-reviewer",
        approval_id="synthetic-approval",
        target=target,  # type: ignore[arg-type]
    )


def _invalid_transport(
    body: bytes,
    headers: tuple[tuple[str, str], ...],
):
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        return OpenAIResponsesRawHttpResponse(502, "synthetic", headers, body)

    return transport, lambda: calls


@pytest.mark.parametrize(
    ("provider", "target"),
    [
        ("openai", DIRECT_OPENAI_EXECUTION_TARGET),
        ("omniroute", LOCAL_OMNIROUTE_EXECUTION_TARGET),
    ],
)
def test_invalid_response_propagates_diagnostics_with_provider_identity_and_no_retry(
    provider: str,
    target: object,
) -> None:
    body = b"not-json " + _SECRET.encode()
    transport, call_count = _invalid_transport(
        body, (("content-type", "text/plain"),)
    )
    request = _request()

    result = execute_openai_model_invocation(
        request,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-api-key")),
        _approval(request, provider, target),
        transport=transport,
    )

    assert isinstance(result, ModelInvocationFailure)
    assert result.provider == provider
    assert result.category == "invalid_response"
    assert result.message == "invalid JSON response body"
    assert result.response_diagnostics is not None
    assert result.response_diagnostics.status_code == 502
    assert result.response_diagnostics.content_type == "text/plain"
    assert result.response_diagnostics.body_length == len(body)
    assert result.response_diagnostics.body_kind == "plaintext"
    assert call_count() == 1
    assert _SECRET not in repr(result)
    assert "synthetic-api-key" not in repr(result)
    assert "Authorization" not in repr(result)


def test_sse_remains_invalid_response_category_and_is_not_accepted() -> None:
    request = _request()
    result = execute_openai_model_invocation(
        request,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-api-key")),
        _approval(request, "openai", DIRECT_OPENAI_EXECUTION_TARGET),
        transport=lambda _: OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("Content-Type", "text/event-stream"),),
            b"data: {\"type\":\"response.output_text.delta\"}\n\n",
        ),
    )

    assert isinstance(result, ModelInvocationFailure)
    assert result.provider == "openai"
    assert result.category == "invalid_response"
    assert result.response_diagnostics is not None
    assert result.response_diagnostics.body_kind == "sse"


def test_failure_event_persists_safe_diagnostics_for_later_process_inspection(
    tmp_path: Path,
) -> None:
    diagnostics = build_openai_responses_response_diagnostics(
        502,
        (("Content-Type", "application/json"),),
        b"{not-json " + _SECRET.encode(),
    )
    failure = ModelInvocationFailure(
        provider="omniroute",
        category="invalid_response",
        message="invalid JSON response body",
        request_id=None,
        status_code=None,
        provider_error_type=None,
        provider_error_code=None,
        response_diagnostics=diagnostics,
    )
    state = WorkflowExecutionState(
        "workflow", "running", "step", 1, "employee", (), None
    )
    transition = transition_workflow_execution_from_step_result(
        state,
        StepRuntimeExecutionFailure("workflow", "step", 1, "employee", failure),
    )
    targets = WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )

    persist_workflow_execution_transition(transition, targets)
    serialized = targets.events_path.read_text()
    loaded = load_workflow_execution_history(targets)
    event = loaded.events[-1]

    assert event.response_diagnostics == diagnostics
    assert event.provider == "omniroute"
    assert event.failure_category == "invalid_response"
    assert json.loads(serialized)["response_diagnostics"] == {
        "status_code": 502,
        "content_type": "application/json",
        "body_length": len(b"{not-json " + _SECRET.encode()),
        "body_kind": "malformed_json",
    }
    assert _SECRET not in serialized
    assert "not-json " not in serialized
    assert "Authorization" not in serialized
    assert "Set-Cookie" not in serialized
    assert _SECRET not in repr(event)
    assert build_runtime_step_event_dict(event)["response_diagnostics"] == {
        "status_code": 502,
        "content_type": "application/json",
        "body_length": len(b"{not-json " + _SECRET.encode()),
        "body_kind": "malformed_json",
    }

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "from ai_office.storage import ("
                "WorkflowExecutionPersistenceTargets, "
                "load_workflow_execution_history); "
                "history = load_workflow_execution_history("
                "WorkflowExecutionPersistenceTargets("
                "__import__('pathlib').Path(sys.argv[1]), "
                "__import__('pathlib').Path(sys.argv[2]))); "
                "diagnostics = history.events[-1].response_diagnostics; "
                "print(json.dumps({'status_code': diagnostics.status_code, "
                "'content_type': diagnostics.content_type, "
                "'body_length': diagnostics.body_length, "
                "'body_kind': diagnostics.body_kind}))"
            ),
            str(targets.state_path),
            str(targets.events_path),
        ],
        capture_output=True,
        check=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        },
    )
    assert json.loads(child.stdout) == {
        "status_code": 502,
        "content_type": "application/json",
        "body_length": len(b"{not-json " + _SECRET.encode()),
        "body_kind": "malformed_json",
    }
    assert _SECRET not in child.stdout
    assert _SECRET not in child.stderr


def test_failure_and_event_representations_are_secret_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = b"plaintext-prefix " + _SECRET.encode()
    response = OpenAIResponsesRawHttpResponse(502, "reason", (), body)
    diagnostics = build_openai_responses_response_diagnostics(502, (), body)
    failure = ModelInvocationFailure(
        "openai",
        "invalid_response",
        "invalid JSON response body",
        None,
        None,
        None,
        None,
        diagnostics,
    )
    state = WorkflowExecutionState(
        "workflow", "running", "step", 1, "employee", (), None
    )
    event = transition_workflow_execution_from_step_result(
        state,
        StepRuntimeExecutionFailure("workflow", "step", 1, "employee", failure),
    ).event

    print(response)
    print(failure)
    print(event)
    print(json.dumps(build_runtime_step_event_dict(event)))
    captured = capsys.readouterr()

    for output in (
        captured.out,
        captured.err,
        repr(response),
        repr(failure),
        repr(event),
    ):
        assert _SECRET not in output
        assert body.decode() not in output
        assert "Authorization" not in output
