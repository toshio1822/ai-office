"""Tests for pure workflow state and runtime event transitions."""

from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.planning import StepExecutionRequest
from ai_office.runtime import (
    RuntimeStepEvent,
    RuntimeStepEventType,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    WorkflowExecutionStatus,
    WorkflowExecutionTransition,
    WorkflowExecutionTransitionInputError,
    build_running_workflow_execution_state,
    transition_workflow_execution_from_step_result,
)


def step_request() -> StepExecutionRequest:
    return StepExecutionRequest(
        workflow_id="workflow",
        workflow_name="Workflow",
        step_index=1,
        step_id="step",
        step_name="Step",
        employee_id="employee",
        employee_name="Employee",
        employee_role="Role",
        model="model",
        allowed_tools=(),
        employee_instructions="employee instructions",
        step_instructions="step instructions",
    )


def running_state(
    *,
    completed_step_ids: tuple[str, ...] = (),
) -> WorkflowExecutionState:
    return build_running_workflow_execution_state(
        step_request(),
        completed_step_ids=completed_step_ids,
    )


def success_result(text: str = "output") -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        workflow_id="workflow",
        step_id="step",
        step_index=1,
        employee_id="employee",
        invocation_result=ModelInvocationSuccess(
            provider="provider",
            response_id="response",
            request_id="request",
            status="completed",
            text_parts=(text,),
            text=text,
        ),
    )


def failure_result(category: str = "api_error") -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        workflow_id="workflow",
        step_id="step",
        step_index=1,
        employee_id="employee",
        invocation_result=ModelInvocationFailure(
            provider="provider",
            category=category,  # type: ignore[arg-type]
            message="safe failure",
            request_id="request",
            status_code=None,
            provider_error_type=None,
            provider_error_code=None,
        ),
    )


def test_models_are_immutable_and_status_and_event_types_are_exported() -> None:
    state = running_state()
    event = RuntimeStepEvent(
        "step_succeeded",
        "workflow",
        "step",
        1,
        "employee",
        "running",
        "succeeded",
        "provider",
        None,
        "response",
        "request",
        "text",
        None,
    )
    transition = WorkflowExecutionTransition(state, state, event)

    assert set(get_args(WorkflowExecutionStatus)) == {
        "ready",
        "running",
        "succeeded",
        "failed",
    }
    assert set(get_args(RuntimeStepEventType)) == {"step_succeeded", "step_failed"}
    assert set(state.__dataclass_fields__) == {
        "workflow_id",
        "status",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "completed_step_ids",
        "last_failure_category",
    }
    assert set(event.__dataclass_fields__) == {
        "event_type",
        "workflow_id",
        "step_id",
        "step_index",
        "employee_id",
        "previous_status",
        "next_status",
        "provider",
        "failure_category",
        "response_id",
        "request_id",
        "output_text",
        "message",
    }
    with pytest.raises(FrozenInstanceError):
        state.status = "failed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        event.message = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        transition.event = event  # type: ignore[misc]


def test_running_state_preserves_one_based_step_identity_and_completed_order() -> None:
    state = running_state(completed_step_ids=("first", "first"))

    assert state.workflow_id == "workflow"
    assert state.status == "running"
    assert state.current_step_id == "step"
    assert state.current_step_index == 1
    assert state.current_employee_id == "employee"
    assert state.completed_step_ids == ("first", "first")
    assert state.last_failure_category is None


def test_success_transition_preserves_values_and_appends_current_step_once() -> None:
    state = running_state(completed_step_ids=("first", "step"))
    result = success_result("")

    transition = transition_workflow_execution_from_step_result(state, result)

    assert transition.previous_state is state
    assert transition.next_state.status == "succeeded"
    assert transition.next_state.completed_step_ids == ("first", "step", "step")
    assert transition.next_state.last_failure_category is None
    assert transition.event.event_type == "step_succeeded"
    assert transition.event.previous_status == "running"
    assert transition.event.next_status == "succeeded"
    assert transition.event.provider == "provider"
    assert transition.event.response_id == "response"
    assert transition.event.request_id == "request"
    assert transition.event.output_text == ""
    assert transition.event.failure_category is None
    assert transition.event.message is None
    assert transition_workflow_execution_from_step_result(state, result) == transition
    assert state == running_state(completed_step_ids=("first", "step"))


@pytest.mark.parametrize(
    "category",
    [
        "api_error",
        "transport_error",
        "invalid_response",
        "invalid_output",
        "invalid_request",
        "approval_required",
    ],
)
def test_failure_transition_preserves_each_existing_failure_category(
    category: str,
) -> None:
    state = running_state(completed_step_ids=("first",))
    transition = transition_workflow_execution_from_step_result(
        state,
        failure_result(category),
    )

    assert transition.previous_state is state
    assert transition.next_state.status == "failed"
    assert transition.next_state.completed_step_ids == ("first",)
    assert transition.next_state.last_failure_category == category
    assert transition.event.event_type == "step_failed"
    assert transition.event.previous_status == "running"
    assert transition.event.next_status == "failed"
    assert transition.event.provider == "provider"
    assert transition.event.failure_category == category
    assert transition.event.response_id is None
    assert transition.event.request_id == "request"
    assert transition.event.output_text is None
    assert transition.event.message == "safe failure"


@pytest.mark.parametrize(
    ("state", "result"),
    [
        (
            WorkflowExecutionState("other", "running", "step", 1, "employee", (), None),
            success_result(),
        ),
        (
            running_state(),
            StepRuntimeExecutionSuccess(
                "workflow", "other", 1, "employee", success_result().invocation_result
            ),
        ),
        (
            running_state(),
            StepRuntimeExecutionSuccess(
                "workflow", "step", 2, "employee", success_result().invocation_result
            ),
        ),
        (
            running_state(),
            StepRuntimeExecutionSuccess(
                "workflow", "step", 1, "other", success_result().invocation_result
            ),
        ),
        (
            WorkflowExecutionState(
                "workflow", "ready", "step", 1, "employee", (), None
            ),
            success_result(),
        ),
    ],
)
def test_inconsistent_inputs_raise_one_safe_unchained_error(
    state: WorkflowExecutionState,
    result: StepRuntimeExecutionSuccess,
) -> None:
    with pytest.raises(
        WorkflowExecutionTransitionInputError,
        match="^workflow execution transition inputs are inconsistent$",
    ) as error:
        transition_workflow_execution_from_step_result(state, result)

    assert error.value.__cause__ is None
