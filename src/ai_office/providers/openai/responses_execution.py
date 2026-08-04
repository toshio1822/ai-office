"""Guarded composition of existing OpenAI provider execution boundaries."""

from collections.abc import Callable

from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationExecutionApprovalError,
    ModelInvocationRequest,
    ModelInvocationResult,
    validate_model_invocation_execution_approval,
)
from ai_office.providers.openai.responses_auth import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    authenticate_openai_responses_http_request,
)
from ai_office.providers.openai.responses_dict_payload import (
    build_openai_responses_payload_dict,
)
from ai_office.providers.openai.responses_http import (
    build_openai_responses_http_request,
)
from ai_office.providers.openai.responses_json import (
    serialize_openai_responses_payload_dict,
)
from ai_office.providers.openai.responses_output import (
    OpenAIResponsesInvalidOutputError,
    extract_openai_responses_output_text,
)
from ai_office.providers.openai.responses_payload import (
    build_openai_responses_payload,
)
from ai_office.providers.openai.responses_request import (
    build_openai_responses_request,
)
from ai_office.providers.openai.responses_response import (
    OpenAIResponsesInvalidResponseError,
    OpenAIResponsesSuccessResponse,
    parse_openai_responses_http_response,
)
from ai_office.providers.openai.responses_result import (
    OpenAIResponsesExecutionInputError,
    build_model_invocation_failure_from_execution_approval_error,
    build_model_invocation_failure_from_openai_api_error,
    build_model_invocation_failure_from_openai_execution_input_error,
    build_model_invocation_failure_from_openai_invalid_output_error,
    build_model_invocation_failure_from_openai_invalid_response_error,
    build_model_invocation_failure_from_openai_transport_error,
    build_model_invocation_success_from_openai,
)
from ai_office.providers.openai.responses_tool import build_openai_responses_tools
from ai_office.providers.openai.responses_transport import (
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesTransportError,
    send_openai_responses_http_request,
)
from ai_office.tools import ToolDefinition

OpenAIResponsesTransport = Callable[
    [OpenAIResponsesAuthenticatedHttpRequest], OpenAIResponsesRawHttpResponse
]


def execute_openai_model_invocation(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    api_key: OpenAIApiKey,
    approval: ModelInvocationExecutionApproval,
    *,
    transport: OpenAIResponsesTransport = send_openai_responses_http_request,
) -> ModelInvocationResult:
    """Execute one guarded, non-streaming OpenAI Responses invocation."""
    try:
        _validate_resolved_tools(request, resolved_tools)
    except OpenAIResponsesExecutionInputError as error:
        return build_model_invocation_failure_from_openai_execution_input_error(error)

    try:
        validate_model_invocation_execution_approval(
            request,
            resolved_tools,
            approval,
            provider="openai",
        )
    except ModelInvocationExecutionApprovalError as error:
        return build_model_invocation_failure_from_execution_approval_error(error)

    try:
        openai_request = build_openai_responses_request(request)
        tools = build_openai_responses_tools(resolved_tools)
        payload = build_openai_responses_payload(openai_request, tools)
        payload_dict = build_openai_responses_payload_dict(payload)
        body = serialize_openai_responses_payload_dict(payload_dict)
        http_request = build_openai_responses_http_request(body)
        authenticated_request = authenticate_openai_responses_http_request(
            http_request,
            api_key,
        )
        raw_response = transport(authenticated_request)
        response = parse_openai_responses_http_response(raw_response)
        if isinstance(response, OpenAIResponsesSuccessResponse):
            output = extract_openai_responses_output_text(response)
            return build_model_invocation_success_from_openai(output)
        return build_model_invocation_failure_from_openai_api_error(response)
    except OpenAIResponsesTransportError as error:
        return build_model_invocation_failure_from_openai_transport_error(error)
    except OpenAIResponsesInvalidResponseError as error:
        return build_model_invocation_failure_from_openai_invalid_response_error(error)
    except OpenAIResponsesInvalidOutputError as error:
        return build_model_invocation_failure_from_openai_invalid_output_error(error)


def _validate_resolved_tools(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
) -> None:
    if tuple(tool.name for tool in resolved_tools) != request.allowed_tools:
        raise OpenAIResponsesExecutionInputError(
            "resolved tools do not match invocation request"
        )
