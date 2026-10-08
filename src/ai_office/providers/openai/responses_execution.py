"""Guarded composition of existing OpenAI provider execution boundaries."""

from collections.abc import Callable

from ai_office.execution_evidence import (
    ExecutionAttemptEvidence,
    ExecutionEvidenceContext,
    ExecutionEvidenceError,
    RawProviderResponseEvidence,
    claim_execution_attempt,
    persist_normalized_result_evidence,
    persist_raw_response_evidence,
)
from ai_office.execution_target import (
    OPENAI_CHAT_COMPLETIONS_PROTOCOL,
    OPENAI_RESPONSES_PROTOCOL,
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
from ai_office.providers.openai.chat_completions import (
    normalize_openai_chat_completions_raw_response,
    serialize_openai_chat_completions_request,
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
ExecutionAttemptClaimed = Callable[[ExecutionAttemptEvidence], None]


def execute_openai_model_invocation(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    api_key: OpenAIApiKey,
    approval: ModelInvocationExecutionApproval,
    *,
    transport: OpenAIResponsesTransport = send_openai_responses_http_request,
    execution_target: ModelExecutionTarget | None = None,
    target: ModelExecutionTarget | None = None,
    execution_evidence: ExecutionEvidenceContext,
    before_transport: Callable[[], None] | None = None,
    attempt_claimed: ExecutionAttemptClaimed | None = None,
) -> ModelInvocationResult:
    """Execute one guarded, non-streaming Responses invocation.

    The OpenAI Responses wire stack is shared by both supported execution
    targets.  The immutable target carried by the approval is authoritative
    when the caller does not pass an explicit target; an explicit mismatch is
    rejected before request construction or transport. Run-bound execution
    evidence is mandatory and its exact durable attempt claim precedes transport.
    An optional pre-transport control runs only after that claim is durable.
    """
    if type(execution_evidence) is not ExecutionEvidenceContext:
        raise ExecutionEvidenceError("context")

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

    if (
        execution_evidence.request != request
        or execution_evidence.resolved_tools != resolved_tools
        or execution_evidence.approval != approval
        or execution_evidence.target != selected_target
    ):
        raise ExecutionEvidenceError("context")

    try:
        if selected_target.protocol == OPENAI_RESPONSES_PROTOCOL:
            openai_request = build_openai_responses_request(request)
            tools = build_openai_responses_tools(resolved_tools)
            payload = build_openai_responses_payload(openai_request, tools)
            payload_dict = build_openai_responses_payload_dict(payload)
            body = serialize_openai_responses_payload_dict(payload_dict)
        else:
            try:
                body = serialize_openai_chat_completions_request(request)
            except ValueError as error:
                return build_model_invocation_failure_from_openai_execution_input_error(
                    OpenAIResponsesExecutionInputError(str(error)), provider=provider
                )
        http_request = build_openai_responses_http_request(
            body,
            execution_target=selected_target,
        )
        authenticated_request = authenticate_openai_responses_http_request(
            http_request,
            api_key,
        )
        # The claim consumes only the unauthenticated request template.  The
        # Authorization-bearing value never crosses into durable evidence.
        attempt: ExecutionAttemptEvidence = claim_execution_attempt(
            execution_evidence, http_request
        )
        if attempt_claimed is not None:
            attempt_claimed(attempt)
        if before_transport is not None:
            before_transport()
        raw_evidence: RawProviderResponseEvidence | None = None
        try:
            raw_response = transport(authenticated_request)
            raw_evidence = persist_raw_response_evidence(
                execution_evidence,
                attempt,
                raw_response,
            )
            result = normalize_openai_compatible_raw_response(
                raw_response,
                protocol=selected_target.protocol,
                provider=provider,
            )
        except OpenAIResponsesTransportError as error:
            result = build_model_invocation_failure_from_openai_transport_error(
                error, provider=provider
            )
        except OpenAIResponsesInvalidResponseError as error:
            result = build_model_invocation_failure_from_openai_invalid_response_error(
                error, provider=provider
            )
        except OpenAIResponsesInvalidOutputError as error:
            result = build_model_invocation_failure_from_openai_invalid_output_error(
                error, provider=provider
            )
        except ExecutionEvidenceError:
            raise
        except Exception:
            # A custom transport may raise an unexpected exception after the
            # provider boundary.  Do not expose its internals; preserve the
            # conservative transport-uncertainty meaning instead.
            result = build_model_invocation_failure_from_openai_transport_error(
                OpenAIResponsesTransportError(
                    "OpenAI Responses transport failed"
                ),
                provider=provider,
            )
        normalized = persist_normalized_result_evidence(
            execution_evidence,
            attempt,
            result,
            raw_response=raw_evidence,
        )
        return normalized.result
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


def normalize_openai_responses_raw_response(
    raw_response: OpenAIResponsesRawHttpResponse, *, provider: str = "openai"
) -> ModelInvocationResult:
    """Deterministically normalize saved raw response bytes without transport."""
    try:
        response = parse_openai_responses_http_response(raw_response)
        if isinstance(response, OpenAIResponsesSuccessResponse):
            output = extract_openai_responses_output_text(response)
            return build_model_invocation_success_from_openai(
                output, provider=provider
            )
        return build_model_invocation_failure_from_openai_api_error(
            response, provider=provider
        )
    except OpenAIResponsesInvalidResponseError as error:
        return build_model_invocation_failure_from_openai_invalid_response_error(
            error, provider=provider
        )
    except OpenAIResponsesInvalidOutputError as error:
        return build_model_invocation_failure_from_openai_invalid_output_error(
            error, provider=provider
        )


def normalize_openai_compatible_raw_response(
    raw_response: OpenAIResponsesRawHttpResponse,
    *,
    protocol: str,
    provider: str,
) -> ModelInvocationResult:
    """Normalize one saved response according to its approved API family."""
    if protocol == OPENAI_RESPONSES_PROTOCOL:
        return normalize_openai_responses_raw_response(raw_response, provider=provider)
    if protocol == OPENAI_CHAT_COMPLETIONS_PROTOCOL:
        try:
            return normalize_openai_chat_completions_raw_response(
                raw_response, provider=provider
            )
        except OpenAIResponsesInvalidResponseError as error:
            return build_model_invocation_failure_from_openai_invalid_response_error(
                error, provider=provider
            )
    return build_model_invocation_failure_from_openai_execution_input_error(
        OpenAIResponsesExecutionInputError("execution target protocol is unsupported"),
        provider=provider,
    )


def _validate_resolved_tools(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
) -> None:
    if tuple(tool.name for tool in resolved_tools) != request.allowed_tools:
        raise OpenAIResponsesExecutionInputError(
            "resolved tools do not match invocation request"
        )
