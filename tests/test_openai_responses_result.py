"""Tests for OpenAI outcome normalization into invocation results."""

from types import MappingProxyType

from ai_office.providers.openai import (
    OpenAIResponsesApiErrorResponse,
    OpenAIResponsesInvalidOutputError,
    OpenAIResponsesInvalidResponseError,
    OpenAIResponsesOutputText,
    OpenAIResponsesTransportError,
    build_model_invocation_failure_from_openai_api_error,
    build_model_invocation_failure_from_openai_invalid_output_error,
    build_model_invocation_failure_from_openai_invalid_response_error,
    build_model_invocation_failure_from_openai_transport_error,
    build_model_invocation_success_from_openai,
)


def test_success_preserves_openai_output_text_exactly() -> None:
    output = OpenAIResponsesOutputText(
        response_id="resp_日本語",
        request_id="request_123",
        status="completed",
        text_parts=("  first\n", "", "最後 😀"),
        text="  first\n最後 😀",
    )

    result = build_model_invocation_success_from_openai(output)

    assert result.provider == "openai"
    assert result.response_id == output.response_id
    assert result.request_id == output.request_id
    assert result.status == output.status
    assert result.text_parts == output.text_parts
    assert result.text_parts is output.text_parts
    assert result.text == output.text
    assert build_model_invocation_success_from_openai(output) == result


def test_api_error_preserves_only_safe_public_fields() -> None:
    error = OpenAIResponsesApiErrorResponse(
        status_code=429,
        request_id="request_error",
        message="synthetic API error",
        error_type="synthetic_type",
        param="do-not-copy",
        code="synthetic_code",
        payload=MappingProxyType({"secret": "do-not-copy"}),
    )

    result = build_model_invocation_failure_from_openai_api_error(error)

    assert result.provider == "openai"
    assert result.category == "api_error"
    assert result.message == error.message
    assert result.request_id == error.request_id
    assert result.status_code == error.status_code
    assert result.provider_error_type == error.error_type
    assert result.provider_error_code == error.code
    assert not hasattr(result, "payload")
    assert not hasattr(result, "param")
    assert "do-not-copy" not in repr(result)
    assert build_model_invocation_failure_from_openai_api_error(error) == result


def test_safe_exceptions_map_without_exposing_exception_internals() -> None:
    errors = (
        (
            OpenAIResponsesTransportError("safe transport message"),
            "transport_error",
            build_model_invocation_failure_from_openai_transport_error,
        ),
        (
            OpenAIResponsesInvalidResponseError("safe response message"),
            "invalid_response",
            build_model_invocation_failure_from_openai_invalid_response_error,
        ),
        (
            OpenAIResponsesInvalidOutputError("safe output message"),
            "invalid_output",
            build_model_invocation_failure_from_openai_invalid_output_error,
        ),
    )

    for error, category, adapter in errors:
        error.__cause__ = RuntimeError("underlying secret")
        error.__context__ = RuntimeError("context secret")
        original_args = error.args
        original_cause = error.__cause__
        original_context = error.__context__

        result = adapter(error)

        assert result.provider == "openai"
        assert result.category == category
        assert result.message == str(error)
        assert result.request_id is None
        assert result.status_code is None
        assert result.provider_error_type is None
        assert result.provider_error_code is None
        assert "secret" not in repr(result)
        assert error.args == original_args
        assert error.__cause__ is original_cause
        assert error.__context__ is original_context
        assert adapter(error) == result
