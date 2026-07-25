"""Single-step runtime result wrapping for explicit OpenAI execution."""

from dataclasses import dataclass

from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationFailure,
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
        return _build_input_failure(execution_input.step_request, error)

    invocation_result = execute_openai_model_invocation(
        execution_input.invocation_request,
        execution_input.resolved_tools,
        api_key,
        execution_input.approval,
        transport=transport,
    )
    return _build_runtime_result(execution_input.step_request, invocation_result)


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
) -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        workflow_id=step_request.workflow_id,
        step_id=step_request.step_id,
        step_index=step_request.step_index,
        employee_id=step_request.employee_id,
        invocation_result=ModelInvocationFailure(
            provider="openai",
            category="invalid_request",
            message=str(error),
            request_id=None,
            status_code=None,
            provider_error_type=None,
            provider_error_code=None,
        ),
    )


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
