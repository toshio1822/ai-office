"""Pure workflow state transitions from completed runtime step results."""

from dataclasses import dataclass
from typing import Literal

from ai_office.invocation import (
    ModelInvocationFailureCategory,
    ModelInvocationSuccess,
)
from ai_office.planning import StepExecutionRequest
from ai_office.runtime.step_runtime_execution import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionResult,
    StepRuntimeExecutionSuccess,
)

WorkflowExecutionStatus = Literal["ready", "running", "succeeded", "failed"]
RuntimeStepEventType = Literal["step_succeeded", "step_failed"]

_INPUT_ERROR_MESSAGE = "workflow execution transition inputs are inconsistent"


@dataclass(frozen=True)
class WorkflowExecutionState:
    """Immutable state for one explicitly selected workflow step."""

    workflow_id: str
    status: WorkflowExecutionStatus
    current_step_id: str
    current_step_index: int
    current_employee_id: str
    completed_step_ids: tuple[str, ...]
    last_failure_category: ModelInvocationFailureCategory | None


@dataclass(frozen=True)
class RuntimeStepEvent:
    """Immutable safe event data for one completed runtime step."""

    event_type: RuntimeStepEventType
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    previous_status: WorkflowExecutionStatus
    next_status: WorkflowExecutionStatus
    provider: str
    failure_category: ModelInvocationFailureCategory | None
    response_id: str | None
    request_id: str | None
    output_text: str | None
    message: str | None


@dataclass(frozen=True)
class WorkflowExecutionTransition:
    """One pure state change and its corresponding runtime event."""

    previous_state: WorkflowExecutionState
    next_state: WorkflowExecutionState
    event: RuntimeStepEvent


class WorkflowExecutionTransitionInputError(ValueError):
    """Raised when a state and a completed step result are inconsistent."""


def build_running_workflow_execution_state(
    step_request: StepExecutionRequest,
    *,
    completed_step_ids: tuple[str, ...] = (),
) -> WorkflowExecutionState:
    """Build the explicit running state immediately before one step executes."""
    return WorkflowExecutionState(
        workflow_id=step_request.workflow_id,
        status="running",
        current_step_id=step_request.step_id,
        current_step_index=step_request.step_index,
        current_employee_id=step_request.employee_id,
        completed_step_ids=completed_step_ids,
        last_failure_category=None,
    )


def transition_workflow_execution_from_step_result(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionResult,
) -> WorkflowExecutionTransition:
    """Return the deterministic state and event for one completed runtime step."""
    _validate_transition_input(current_state, result)
    if isinstance(result, StepRuntimeExecutionSuccess):
        return _build_success_transition(current_state, result)
    return _build_failure_transition(current_state, result)


def _validate_transition_input(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionResult,
) -> None:
    if (
        current_state.workflow_id != result.workflow_id
        or current_state.current_step_id != result.step_id
        or current_state.current_step_index != result.step_index
        or current_state.current_employee_id != result.employee_id
        or current_state.status != "running"
    ):
        raise WorkflowExecutionTransitionInputError(_INPUT_ERROR_MESSAGE) from None


def _build_success_transition(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionSuccess,
) -> WorkflowExecutionTransition:
    invocation_result = result.invocation_result
    next_state = WorkflowExecutionState(
        workflow_id=current_state.workflow_id,
        status="succeeded",
        current_step_id=current_state.current_step_id,
        current_step_index=current_state.current_step_index,
        current_employee_id=current_state.current_employee_id,
        completed_step_ids=current_state.completed_step_ids
        + (current_state.current_step_id,),
        last_failure_category=None,
    )
    return WorkflowExecutionTransition(
        previous_state=current_state,
        next_state=next_state,
        event=_build_success_event(current_state, result, invocation_result),
    )


def _build_success_event(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionSuccess,
    invocation_result: ModelInvocationSuccess,
) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        event_type="step_succeeded",
        workflow_id=result.workflow_id,
        step_id=result.step_id,
        step_index=result.step_index,
        employee_id=result.employee_id,
        previous_status=current_state.status,
        next_status="succeeded",
        provider=invocation_result.provider,
        failure_category=None,
        response_id=invocation_result.response_id,
        request_id=invocation_result.request_id,
        output_text=invocation_result.text,
        message=None,
    )


def _build_failure_transition(
    current_state: WorkflowExecutionState,
    result: StepRuntimeExecutionFailure,
) -> WorkflowExecutionTransition:
    invocation_result = result.invocation_result
    next_state = WorkflowExecutionState(
        workflow_id=current_state.workflow_id,
        status="failed",
        current_step_id=current_state.current_step_id,
        current_step_index=current_state.current_step_index,
        current_employee_id=current_state.current_employee_id,
        completed_step_ids=current_state.completed_step_ids,
        last_failure_category=invocation_result.category,
    )
    return WorkflowExecutionTransition(
        previous_state=current_state,
        next_state=next_state,
        event=RuntimeStepEvent(
            event_type="step_failed",
            workflow_id=result.workflow_id,
            step_id=result.step_id,
            step_index=result.step_index,
            employee_id=result.employee_id,
            previous_status=current_state.status,
            next_status="failed",
            provider=invocation_result.provider,
            failure_category=invocation_result.category,
            response_id=None,
            request_id=invocation_result.request_id,
            output_text=None,
            message=invocation_result.message,
        ),
    )
