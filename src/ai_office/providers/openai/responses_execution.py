"""Guarded composition of existing OpenAI provider execution boundaries."""

import email.utils
import time
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
            session_id=execution_evidence.request_session_id,
        )
        authenticated_request = authenticate_openai_responses_http_request(
            http_request,
            api_key,
        )
        from ai_office.engine.workflow_bounded_job import (
            BoundedJobActionAuthorization,
            admit_bounded_job_dispatch,
            reserve_bounded_job_attempt,
        )
        from ai_office.engine.workflow_job_authorization import (
            WorkflowJobAuthorization,
            WorkflowJobAuthorizationError,
            reserve_job_attempt,
        )
        from ai_office.execution_evidence import admit_job_execution_dispatch

        authorization = execution_evidence.job_authorization
        bounded = type(authorization) is BoundedJobActionAuthorization
        retry_limit = authorization.scope.max_retries if bounded else 0
        interval_seconds = (
            authorization.scope.retry_interval_seconds if bounded else 0
        )
        body_bytes = http_request.body.encode("utf-8")
        body_sha256 = sha256(body_bytes).hexdigest()
        last_result: ModelInvocationResult | None = None
        for retry_index in range(retry_limit + 1):
            if authorization is not None:
                try:
                    if bounded:
                        reservation_sha256 = reserve_bounded_job_attempt(
                            authorization,
                            request_body_sha256=body_sha256,
                            request_body_length=len(body_bytes),
                            retry_index=retry_index,
                        )
                    elif type(authorization) is WorkflowJobAuthorization:
                        reservation_sha256 = reserve_job_attempt(
                            authorization,
                            binding=execution_evidence.binding,
                            workflow_id=execution_evidence.workflow_id,
                            step_id=execution_evidence.step_id,
                            step_index=execution_evidence.step_index,
                            employee_id=execution_evidence.employee_id,
                            request=request,
                            resolved_tools=resolved_tools,
                            target=selected_target,
                            request_body_sha256=body_sha256,
                            request_body_length=len(body_bytes),
                        )
                    else:
                        raise WorkflowJobAuthorizationError("authorization")
                except WorkflowJobAuthorizationError:
                    if retry_index and last_result is not None:
                        return last_result
                    raise ExecutionEvidenceError("job_reservation") from None
                execution_evidence = replace(
                    execution_evidence, job_reservation_sha256=reservation_sha256
                )
            attempt: ExecutionAttemptEvidence = claim_execution_attempt(
                execution_evidence, http_request
            )
            if attempt_claimed is not None:
                attempt_claimed(attempt)
            if before_transport is not None:
                before_transport()
            if authorization is not None:
                if bounded:
                    try:
                        admit_bounded_job_dispatch(
                            authorization,
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
            raw_response = None
            try:
                raw_response = transport(authenticated_request)
                raw_evidence = persist_raw_response_evidence(
                    execution_evidence, attempt, raw_response
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
                result = (
                    build_model_invocation_failure_from_openai_invalid_response_error(
                    error, provider=provider
                    )
                )
            except OpenAIResponsesInvalidOutputError as error:
                result = (
                    build_model_invocation_failure_from_openai_invalid_output_error(
                    error, provider=provider
                    )
                )
            except ExecutionEvidenceError:
                raise
            except Exception:
                result = build_model_invocation_failure_from_openai_transport_error(
                    OpenAIResponsesTransportError("OpenAI Responses transport failed"),
                    provider=provider,
                )
            normalized = persist_normalized_result_evidence(
                execution_evidence, attempt, result, raw_response=raw_evidence
            )
            last_result = normalized.result
            if (
                not bounded
                or retry_index >= retry_limit
                or raw_response is None
                or not _retryable_bounded_response(
                    raw_response.status_code,
                    raw_response.body,
                    authorization.scope.retryable_http_statuses,
                )
            ):
                return normalized.result
            try:
                from ai_office.engine.workflow_bounded_job import (
                    bounded_job_retry_budget_remaining,
                )

                if not bounded_job_retry_budget_remaining(authorization):
                    return normalized.result
            except Exception:
                return normalized.result
            delay = max(
                interval_seconds,
                _retry_after_seconds(raw_response.headers) or 0,
            )
            if (
                time.time_ns() + delay * 1_000_000_000
                >= authorization.scope.expires_at_ns
            ):
                return normalized.result
            time.sleep(delay)
        assert last_result is not None
        return last_result
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


def _retryable_bounded_response(
    status_code: int,
    body: bytes,
    retryable_http_statuses: tuple[int, ...],
) -> bool:
    from ai_office.engine.workflow_bounded_job import _retryable_http_response

    return _retryable_http_response(status_code, body, retryable_http_statuses)


def _retry_after_seconds(headers: tuple[tuple[str, str], ...]) -> int | None:
    values = [value.strip() for name, value in headers if name.lower() == "retry-after"]
    if len(values) != 1:
        return None
    value = values[0]
    if value.isascii() and value.isdecimal():
        return int(value)
    try:
        retry_at = email.utils.parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            return None
        seconds = retry_at.timestamp() - time.time()
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, int(seconds + 0.999999))


def _validate_resolved_tools(
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
) -> None:
    if tuple(tool.name for tool in resolved_tools) != request.allowed_tools:
        raise OpenAIResponsesExecutionInputError(
            "resolved tools do not match invocation request"
        )
