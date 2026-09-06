"""Guarded composition of existing OpenAI provider execution boundaries."""

from collections.abc import Callable

from ai_office.execution_target import (
    ModelExecutionTarget,
    ModelExecutionTargetError,
    validate_execution_target_for_provider,
)
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
    execution_target: ModelExecutionTarget | None = None,
    target: ModelExecutionTarget | None = None,
) -> ModelInvocationResult:
    """Execute one guarded, non-streaming Responses invocation.

    The OpenAI Responses wire stack is shared by both supported execution
    targets.  The immutable target carried by the approval is authoritative
    when the caller does not pass an explicit target; an explicit mismatch is
    rejected before request construction or transport.
    """
    try:
        if execution_target is not None and target is not None:
            if execution_target != target:
                raise ModelExecutionTargetError
        selected_target = (
            execution_target
            if execution_target is not None
            else target
            if target is not None
            else approval.execution_target
        )
        selected_target = validate_execution_target_for_provider(selected_target)
        provider = selected_target.provider
    except (ModelExecutionTargetError, AttributeError, TypeError, ValueError):
        return build_model_invocation_failure_from_execution_approval_error(
            ModelInvocationExecutionApprovalError(
                "model invocation execution is not approved"
            )
        )
    try:
        _validate_resolved_tools(request, resolved_tools)
    except OpenAIResponsesExecutionInputError as error:
        return build_model_invocation_failure_from_openai_execution_input_error(
            error, provider=provider
        )

    try:
        validate_model_invocation_execution_approval(
            request,
            resolved_tools,
            approval,
            provider=provider,
            execution_target=selected_target,
        )
    except ModelInvocationExecutionApprovalError as error:
        return build_model_invocation_failure_from_execution_approval_error(
            error, provider=provider
        )

    try:
        openai_request = build_openai_responses_request(request)
        tools = build_openai_responses_tools(resolved_tools)
        payload = build_openai_responses_payload(openai_request, tools)
        payload_dict = build_openai_responses_payload_dict(payload)
        body = serialize_openai_responses_payload_dict(payload_dict)
        http_request = build_openai_responses_http_request(
            body,
            execution_target=selected_target,
        )
        authenticated_request = authenticate_openai_responses_http_request(
            http_request,
            api_key,
        )
        raw_response = transport(authenticated_request)
        response = parse_openai_responses_http_response(raw_response)
        if isinstance(response, OpenAIResponsesSuccessResponse):
            output = extract_openai_responses_output_text(response)
            return build_model_invocation_success_from_openai(
                output, provider=provider
            )
        return build_model_invocation_failure_from_openai_api_error(
            response, provider=provider
        )
    except OpenAIResponsesTransportError as error:
        return build_model_invocation_failure_from_openai_transport_error(
            error, provider=provider
        )
    except OpenAIResponsesInvalidResponseError as error:
        return build_model_invocation_failure_from_openai_invalid_response_error(
            error, provider=provider
        )
    except OpenAIResponsesInvalidOutputError as error:
        return build_model_invocation_failure_from_openai_invalid_output_error(
            error, provider=provider
        )


def _validate_resolved_tools(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
) -> None:
    if tuple(tool.name for tool in resolved_tools) != request.allowed_tools:
        raise OpenAIResponsesExecutionInputError(
            "resolved tools do not match invocation request"
        )
