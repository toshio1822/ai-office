"""Normalize safe OpenAI outcome values into provider-independent results."""

from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationSuccess,
)
from ai_office.providers.openai.responses_output import (
    OpenAIResponsesInvalidOutputError,
    OpenAIResponsesOutputText,
)
from ai_office.providers.openai.responses_response import (
    OpenAIResponsesApiErrorResponse,
    OpenAIResponsesInvalidResponseError,
)
from ai_office.providers.openai.responses_transport import OpenAIResponsesTransportError


def build_model_invocation_success_from_openai(
    result: OpenAIResponsesOutputText,
) -> ModelInvocationSuccess:
    """Copy an OpenAI output-text result into the common success contract."""
    return ModelInvocationSuccess(
        provider="openai",
        response_id=result.response_id,
        request_id=result.request_id,
        status=result.status,
        text_parts=result.text_parts,
        text=result.text,
    )


def build_model_invocation_failure_from_openai_api_error(
    result: OpenAIResponsesApiErrorResponse,
) -> ModelInvocationFailure:
    """Copy safe OpenAI API-error fields into the common failure contract."""
    return ModelInvocationFailure(
        provider="openai",
        category="api_error",
        message=result.message,
        request_id=result.request_id,
        status_code=result.status_code,
        provider_error_type=result.error_type,
        provider_error_code=result.code,
    )


def build_model_invocation_failure_from_openai_transport_error(
    error: OpenAIResponsesTransportError,
) -> ModelInvocationFailure:
    """Normalize the safe public message of an OpenAI transport error."""
    return _build_safe_openai_exception_failure(error, "transport_error")


def build_model_invocation_failure_from_openai_invalid_response_error(
    error: OpenAIResponsesInvalidResponseError,
) -> ModelInvocationFailure:
    """Normalize the safe public message of an invalid OpenAI response error."""
    return _build_safe_openai_exception_failure(error, "invalid_response")


def build_model_invocation_failure_from_openai_invalid_output_error(
    error: OpenAIResponsesInvalidOutputError,
) -> ModelInvocationFailure:
    """Normalize the safe public message of an invalid OpenAI output error."""
    return _build_safe_openai_exception_failure(error, "invalid_output")


def _build_safe_openai_exception_failure(
    error: (
        OpenAIResponsesTransportError
        | OpenAIResponsesInvalidResponseError
        | OpenAIResponsesInvalidOutputError
    ),
    category: ModelInvocationFailureCategory,
) -> ModelInvocationFailure:
    return ModelInvocationFailure(
        provider="openai",
        category=category,
        message=str(error),
        request_id=None,
        status_code=None,
        provider_error_type=None,
        provider_error_code=None,
    )
