"""Single-step runtime result wrapping for explicit OpenAI execution."""

from dataclasses import dataclass

from ai_office.execution_target import (
    ModelExecutionTargetError,
    is_supported_execution_provider,
    validate_execution_target_for_provider,
)
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationFailureDiagnostics,
    ModelInvocationRequest,
    ModelInvocationResult,
    ModelInvocationSuccess,
)
from ai_office.planning import StepExecutionRequest
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesTransport,
    execute_openai_model_invocation,
    send_openai_responses_http_request,
)
from ai_office.tools import ToolDefinition

_INPUT_ERROR_MESSAGE = "runtime step execution inputs are inconsistent"


@dataclass(frozen=True)
class StepRuntimeExecutionInput:
    """Already-prepared, credential-free inputs for one runtime step."""

    step_request: StepExecutionRequest
    invocation_request: ModelInvocationRequest
    resolved_tools: tuple[ToolDefinition, ...]
    approval: ModelInvocationExecutionApproval


@dataclass(frozen=True)
class StepRuntimeExecutionSuccess:
    """A successful provider invocation with its original step identity."""

    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    invocation_result: ModelInvocationSuccess


@dataclass(frozen=True)
class StepRuntimeExecutionFailure:
    """A failed provider invocation with its original step identity."""

    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    invocation_result: ModelInvocationFailure


StepRuntimeExecutionResult = StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure


class StepRuntimeExecutionInputError(ValueError):
    """Raised when prepared runtime execution inputs are inconsistent."""


def execute_openai_runtime_step(
    execution_input: StepRuntimeExecutionInput,
    api_key: OpenAIApiKey,
    *,
    transport: OpenAIResponsesTransport = send_openai_responses_http_request,
) -> StepRuntimeExecutionResult:
    """Execute one approved OpenAI step without changing runtime state."""
    try:
        _validate_execution_input(execution_input)
    except StepRuntimeExecutionInputError as error:
        return _build_input_failure(
            execution_input.step_request,
            error,
            provider=_approval_provider(execution_input.approval),
        )

    try:
        execution_target = validate_execution_target_for_provider(
            execution_input.approval.execution_target,
            provider=execution_input.approval.provider,
        )
    except (AttributeError, ModelExecutionTargetError, TypeError, ValueError):
        return _build_input_failure(
            execution_input.step_request,
            StepRuntimeExecutionInputError(_INPUT_ERROR_MESSAGE),
            provider=_approval_provider(execution_input.approval),
        )

    invocation_result = execute_openai_model_invocation(
        execution_input.invocation_request,
        execution_input.resolved_tools,
        api_key,
        execution_input.approval,
        transport=transport,
        execution_target=execution_target,
    )
    result = _build_runtime_result(execution_input.step_request, invocation_result)
    if not is_valid_step_runtime_execution_result(
        result,
        workflow_id=execution_input.step_request.workflow_id,
        step_id=execution_input.step_request.step_id,
        step_index=execution_input.step_request.step_index,
        employee_id=execution_input.step_request.employee_id,
    ):
        raise RuntimeError("runtime step execution result is invalid")
    return result


def _validate_execution_input(execution_input: StepRuntimeExecutionInput) -> None:
    step_request = execution_input.step_request
    invocation_request = execution_input.invocation_request
    if (
        step_request.model != invocation_request.model
        or step_request.employee_instructions != invocation_request.system_instructions
        or step_request.step_instructions != invocation_request.task_instructions
        or step_request.allowed_tools != invocation_request.allowed_tools
    ):
        raise StepRuntimeExecutionInputError(_INPUT_ERROR_MESSAGE)


def _build_input_failure(
    step_request: StepExecutionRequest,
    error: StepRuntimeExecutionInputError,
    *,
    provider: str = "openai",
) -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        workflow_id=step_request.workflow_id,
        step_id=step_request.step_id,
        step_index=step_request.step_index,
        employee_id=step_request.employee_id,
        invocation_result=ModelInvocationFailure(
            provider=provider,
            category="invalid_request",
            message=str(error),
            request_id=None,
            status_code=None,
            provider_error_type=None,
            provider_error_code=None,
        ),
    )


def _approval_provider(approval: object) -> str:
    """Preserve a supported target provider on pre-transport failures."""
    provider = getattr(approval, "provider", "openai")
    return provider if is_supported_execution_provider(provider) else "openai"


def _build_runtime_result(
    step_request: StepExecutionRequest,
    invocation_result: ModelInvocationResult,
) -> StepRuntimeExecutionResult:
    identity = {
        "workflow_id": step_request.workflow_id,
        "step_id": step_request.step_id,
        "step_index": step_request.step_index,
        "employee_id": step_request.employee_id,
    }
    if isinstance(invocation_result, ModelInvocationSuccess):
        return StepRuntimeExecutionSuccess(
            **identity,
            invocation_result=invocation_result,
        )
    return StepRuntimeExecutionFailure(
        **identity,
        invocation_result=invocation_result,
    )


def is_valid_step_runtime_execution_result(
    result: object,
    *,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
) -> bool:
    """Check the exact Phase 21 result contract without rebuilding it."""
    if type(result) not in {
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
    } or not _valid_runtime_identity(
        result, workflow_id, step_id, step_index, employee_id
    ):
        return False
    if type(result) is StepRuntimeExecutionSuccess:
        return _valid_invocation_success(result.invocation_result)
    return _valid_invocation_failure(result.invocation_result)


def _valid_runtime_identity(
    result: StepRuntimeExecutionResult,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
) -> bool:
    return (
        all(type(value) is str and value != "" for value in (
            result.workflow_id,
            result.step_id,
            result.employee_id,
        ))
        and type(result.step_index) is int
        and result.workflow_id == workflow_id
        and result.step_id == step_id
        and result.step_index == step_index
        and result.employee_id == employee_id
    )


def _valid_invocation_success(value: object) -> bool:
    return (
        type(value) is ModelInvocationSuccess
        and is_supported_execution_provider(value.provider)
        and all(type(item) is str and item != "" for item in (
            value.provider,
            value.response_id,
            value.status,
        ))
        and _valid_optional_string(value.request_id)
        and type(value.text_parts) is tuple
        and all(type(item) is str for item in value.text_parts)
        and type(value.text) is str
        and value.text == "".join(value.text_parts)
    )


def _valid_invocation_failure(value: object) -> bool:
    diagnostics = (
        value.response_diagnostics if type(value) is ModelInvocationFailure else None
    )
    return (
        type(value) is ModelInvocationFailure
        and is_supported_execution_provider(value.provider)
        and type(value.provider) is str
        and value.category in {
            "api_error",
            "transport_error",
            "invalid_response",
            "invalid_output",
            "invalid_request",
            "approval_required",
        }
        and type(value.message) is str
        and value.message != ""
        and _valid_optional_string(value.request_id)
        and _valid_optional_int(value.status_code)
        and _valid_optional_string(value.provider_error_type)
        and _valid_optional_string(value.provider_error_code)
        and _valid_failure_diagnostics(diagnostics, value.category)
        and (
            value.category == "api_error"
            or (
                value.status_code is None
                and value.provider_error_type is None
                and value.provider_error_code is None
            )
        )
    )


def _valid_failure_diagnostics(
    value: object,
    category: ModelInvocationFailureCategory,
) -> bool:
    if value is None:
        return True
    return (
        category == "invalid_response"
        and type(value) is ModelInvocationFailureDiagnostics
        and type(value.status_code) is int
        and (
            value.content_type is None
            or (type(value.content_type) is str and value.content_type != "")
        )
        and type(value.body_length) is int
        and value.body_length >= 0
        and type(value.body_kind) is str
        and value.body_kind in {
            "empty",
            "json",
            "sse",
            "html",
            "plaintext",
            "malformed_json",
            "non_utf8",
        }
    )


def _valid_optional_string(value: object) -> bool:
    return value is None or (type(value) is str and value != "")


def _valid_optional_int(value: object) -> bool:
    return value is None or type(value) is int
