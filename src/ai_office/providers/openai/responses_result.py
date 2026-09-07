"""Normalize safe OpenAI outcome values into provider-independent results."""

from ai_office.invocation import (
    ModelInvocationExecutionApprovalError,
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationFailureDiagnostics,
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
    *,
    provider: str = "openai",
) -> ModelInvocationSuccess:
    """Copy an OpenAI output-text result into the common success contract."""
    return ModelInvocationSuccess(
        provider=provider,
        response_id=result.response_id,
        request_id=result.request_id,
        status=result.status,
        text_parts=result.text_parts,
        text=result.text,
    )


def build_model_invocation_failure_from_openai_api_error(
    result: OpenAIResponsesApiErrorResponse,
    *,
    provider: str = "openai",
) -> ModelInvocationFailure:
    """Copy safe OpenAI API-error fields into the common failure contract."""
    return ModelInvocationFailure(
        provider=provider,
        category="api_error",
        message=result.message,
        request_id=result.request_id,
        status_code=result.status_code,
        provider_error_type=result.error_type,
        provider_error_code=result.code,
    )


class OpenAIResponsesExecutionInputError(ValueError):
    """Raised when explicit execution inputs are inconsistent."""


def build_model_invocation_failure_from_openai_execution_input_error(
    error: OpenAIResponsesExecutionInputError,
    *,
    provider: str = "openai",
) -> ModelInvocationFailure:
    """Normalize the safe public message of an invalid execution input."""
    return _build_safe_openai_exception_failure(error, "invalid_request", provider)


def build_model_invocation_failure_from_execution_approval_error(
    error: ModelInvocationExecutionApprovalError,
    *,
    provider: str = "openai",
) -> ModelInvocationFailure:
    """Normalize a rejected explicit approval into a safe failure result."""
    return _build_safe_openai_exception_failure(error, "approval_required", provider)


def build_model_invocation_failure_from_openai_transport_error(
    error: OpenAIResponsesTransportError,
    *,
    provider: str = "openai",
) -> ModelInvocationFailure:
    """Normalize the safe public message of an OpenAI transport error."""
    return _build_safe_openai_exception_failure(error, "transport_error", provider)


def build_model_invocation_failure_from_openai_invalid_response_error(
    error: OpenAIResponsesInvalidResponseError,
    *,
    provider: str = "openai",
) -> ModelInvocationFailure:
    """Normalize the safe public message of an invalid OpenAI response error."""
    return _build_safe_openai_exception_failure(error, "invalid_response", provider)


def build_model_invocation_failure_from_openai_invalid_output_error(
    error: OpenAIResponsesInvalidOutputError,
    *,
    provider: str = "openai",
) -> ModelInvocationFailure:
    """Normalize the safe public message of an invalid OpenAI output error."""
    return _build_safe_openai_exception_failure(error, "invalid_output", provider)


def _build_safe_openai_exception_failure(
    error: (
        OpenAIResponsesTransportError
        | OpenAIResponsesInvalidResponseError
        | OpenAIResponsesInvalidOutputError
        | OpenAIResponsesExecutionInputError
        | ModelInvocationExecutionApprovalError
    ),
    category: ModelInvocationFailureCategory,
    provider: str = "openai",
) -> ModelInvocationFailure:
    response_diagnostics = getattr(error, "response_diagnostics", None)
    if not isinstance(response_diagnostics, ModelInvocationFailureDiagnostics):
        response_diagnostics = None
    return ModelInvocationFailure(
        provider=provider,
        category=category,
        message=str(error),
        request_id=None,
        status_code=None,
        provider_error_type=None,
        provider_error_code=None,
        response_diagnostics=response_diagnostics,
    )
