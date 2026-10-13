"""Guarded composition of existing OpenAI provider execution boundaries."""

from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256

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
    approval: ModelInvocationExecutionApproval | None,
    *,
    transport: OpenAIResponsesTransport = send_openai_responses_http_request,
    execution_evidence: ExecutionEvidenceContext,
    before_transport: Callable[[], None] | None = None,
    attempt_claimed: ExecutionAttemptClaimed | None = None,
) -> ModelInvocationResult:
    """Execute one guarded, non-streaming Responses invocation.

    The OpenAI-compatible wire stack is shared by both supported protocols.
    The Run-bound evidence context derives its target from the persisted
    Manifest; optional caller target arguments cannot select a connection.
    Run-bound execution evidence is mandatory and its durable attempt claim
    precedes transport.
    An optional pre-transport control runs only after that claim is durable.
    """
    if type(execution_evidence) is not ExecutionEvidenceContext:
        raise ExecutionEvidenceError("context")

    try:
        selected_target = execution_evidence.target
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
        if approval is not None:
            validate_model_invocation_execution_approval(
                request,
                resolved_tools,
                approval,
                provider=provider,
                execution_target=selected_target,
            )
        elif execution_evidence.job_authorization is None:
            raise ModelInvocationExecutionApprovalError(
                "model invocation execution is not approved"
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
        or (approval is not None and execution_evidence.job_authorization is not None)
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
            run_id=request.run_id,
        )
        authenticated_request = authenticate_openai_responses_http_request(
            http_request,
            api_key,
        )
        if execution_evidence.job_authorization is not None:
            from ai_office.engine.workflow_job_authorization import (
                WorkflowJobAuthorizationError,
            )

            body_bytes = http_request.body.encode("utf-8")
            try:
                from ai_office.engine.workflow_bounded_job import (
                    BoundedJobActionAuthorization,
                    reserve_bounded_job_attempt,
                )
                from ai_office.engine.workflow_job_authorization import (
                    WorkflowJobAuthorization,
                    reserve_job_attempt,
                )

                if (
                    type(execution_evidence.job_authorization)
                    is BoundedJobActionAuthorization
                ):
                    reservation_sha256 = reserve_bounded_job_attempt(
                        execution_evidence.job_authorization,
                        request_body_sha256=sha256(body_bytes).hexdigest(),
                        request_body_length=len(body_bytes),
                    )
                elif (
                    type(execution_evidence.job_authorization)
                    is WorkflowJobAuthorization
                ):
                    reservation_sha256 = reserve_job_attempt(
                        execution_evidence.job_authorization,
                        binding=execution_evidence.binding,
                        workflow_id=execution_evidence.workflow_id,
                        step_id=execution_evidence.step_id,
                        step_index=execution_evidence.step_index,
                        employee_id=execution_evidence.employee_id,
                        request=request,
                        resolved_tools=resolved_tools,
                        target=selected_target,
                        request_body_sha256=sha256(body_bytes).hexdigest(),
                        request_body_length=len(body_bytes),
                    )
                else:
                    raise WorkflowJobAuthorizationError("authorization")
            except WorkflowJobAuthorizationError:
                raise ExecutionEvidenceError("job_reservation") from None
            execution_evidence = replace(
                execution_evidence, job_reservation_sha256=reservation_sha256
            )
        # The claim consumes only the unauthenticated request template.  The
        # Authorization-bearing value never crosses into durable evidence.
        # A Job reservation is the durable quota consume before this Run claim.
        attempt: ExecutionAttemptEvidence = claim_execution_attempt(
            execution_evidence, http_request
        )
        if attempt_claimed is not None:
            attempt_claimed(attempt)
        if before_transport is not None:
            before_transport()
        if execution_evidence.job_authorization is not None:
            from ai_office.engine.workflow_bounded_job import (
                BoundedJobActionAuthorization,
                admit_bounded_job_dispatch,
            )
            from ai_office.execution_evidence import admit_job_execution_dispatch

            if (
                type(execution_evidence.job_authorization)
                is BoundedJobActionAuthorization
            ):
                try:
                    admit_bounded_job_dispatch(
                        execution_evidence.job_authorization,
                        attempt=attempt,
                        reservation_sha256=attempt.job_reservation_sha256,
                    )
                except WorkflowJobAuthorizationError as error:
                    raise ExecutionEvidenceError(
                        "job_" + error.classification
                    ) from None
            else:
                admit_job_execution_dispatch(execution_evidence, attempt)
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
                OpenAIResponsesTransportError("OpenAI Responses transport failed"),
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
            return build_model_invocation_success_from_openai(output, provider=provider)
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
