"""Persist one completed runtime step through the existing transition contracts."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.runtime.step_runtime_execution import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionResult,
    StepRuntimeExecutionSuccess,
)
from ai_office.runtime.workflow_execution_transition import (
    WorkflowExecutionState,
    WorkflowExecutionTransition,
    transition_workflow_execution_from_step_result,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
    load_workflow_execution_state,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    persist_workflow_execution_transition,
)

ExecutedStepTransitionPersistenceClassification = Literal[
    "result_type",
    "state_target",
    "state_data",
    "state_status",
    "state_identity",
    "event_target",
    "target_conflict",
    "transition_contract",
]
_ERROR_MESSAGE = "executed-step transition persistence inputs are incompatible"

TransitionFunction = Callable[
    [WorkflowExecutionState, StepRuntimeExecutionResult], WorkflowExecutionTransition
]
PersistenceFunction = Callable[
    [WorkflowExecutionTransition, WorkflowExecutionPersistenceTargets],
    WorkflowExecutionPersistenceResult,
]


@dataclass(frozen=True)
class ExecutedStepTransitionPersistenceFailureDetail:
    """Safe classification for errors before Phase 23 persistence."""

    classification: ExecutedStepTransitionPersistenceClassification


class ExecutedStepTransitionPersistenceError(ValueError):
    """Raised when a completed result cannot safely be persisted."""


class ExecutedStepTransitionPersistenceCompatibilityError(
    ExecutedStepTransitionPersistenceError
):
    """Raised for a safe, classified incompatibility before persistence."""

    def __init__(
        self, classification: ExecutedStepTransitionPersistenceClassification
    ) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ExecutedStepTransitionPersistenceFailureDetail(classification)


def persist_executed_step_transition(
    result: object,
    state_path: object,
    events_path: object,
    *,
    transition_function: TransitionFunction = (
        transition_workflow_execution_from_step_result
    ),
    persistence_function: PersistenceFunction = persist_workflow_execution_transition,
) -> WorkflowExecutionPersistenceResult:
    """Transition and persist one exact completed result without executing a provider.

    The supplied event target is passed only to Phase 23 and is never read here.
    """
    _validate_explicit_inputs(
        result, state_path, events_path, transition_function, persistence_function
    )
    assert isinstance(
        result, (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
    )
    assert isinstance(state_path, Path)
    assert isinstance(events_path, Path)

    current_state = _load_running_state(state_path)
    _validate_result_identity(current_state, result)
    transition = transition_function(current_state, result)
    _validate_transition_contract(transition, current_state, result)
    return persistence_function(
        transition,
        WorkflowExecutionPersistenceTargets(state_path, events_path),
    )


def _validate_explicit_inputs(
    result: object,
    state_path: object,
    events_path: object,
    transition_function: object,
    persistence_function: object,
) -> None:
    if type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        _raise("result_type")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(transition_function) or not callable(persistence_function):
        _raise("transition_contract")


def _load_running_state(state_path: Path) -> WorkflowExecutionState:
    try:
        state = load_workflow_execution_state(state_path)
    except WorkflowExecutionDataError:
        _raise("state_data")
    except WorkflowExecutionLoadError:
        _raise("state_target")
    if state.status != "running":
        _raise("state_status")
    if state.last_failure_category is not None:
        _raise("state_identity")
    return state


def _validate_result_identity(
    state: WorkflowExecutionState, result: StepRuntimeExecutionResult
) -> None:
    if (
        state.workflow_id != result.workflow_id
        or state.current_step_id != result.step_id
        or state.current_step_index != result.step_index
        or state.current_employee_id != result.employee_id
    ):
        _raise("state_identity")


def _validate_transition_contract(
    transition: object,
    state: WorkflowExecutionState,
    result: StepRuntimeExecutionResult,
) -> None:
    if not isinstance(transition, WorkflowExecutionTransition):
        _raise("transition_contract")
    next_state = transition.next_state
    event = transition.event
    expected_status = (
        "succeeded" if isinstance(result, StepRuntimeExecutionSuccess) else "failed"
    )
    expected_event_type = (
        "step_succeeded"
        if isinstance(result, StepRuntimeExecutionSuccess)
        else "step_failed"
    )
    if isinstance(result, StepRuntimeExecutionSuccess):
        invocation_result = result.invocation_result
        completed_ids_are_valid = (
            next_state.completed_step_ids
            == state.completed_step_ids + (state.current_step_id,)
            and next_state.last_failure_category is None
            and event.provider == invocation_result.provider
            and event.response_id == invocation_result.response_id
            and event.request_id == invocation_result.request_id
            and event.output_text == invocation_result.text
            and event.failure_category is None
            and event.message is None
        )
    else:
        invocation_result = result.invocation_result
        completed_ids_are_valid = (
            next_state.completed_step_ids == state.completed_step_ids
            and next_state.last_failure_category == result.invocation_result.category
            and event.provider == invocation_result.provider
            and event.request_id == invocation_result.request_id
            and event.failure_category == result.invocation_result.category
            and event.message == invocation_result.message
            and event.response_id is None
            and event.output_text is None
        )
    valid = (
        transition.previous_state == state
        and next_state.workflow_id == state.workflow_id
        and next_state.current_step_id == state.current_step_id
        and next_state.current_step_index == state.current_step_index
        and next_state.current_employee_id == state.current_employee_id
        and next_state.status == expected_status
        and event.workflow_id == result.workflow_id
        and event.step_id == result.step_id
        and event.step_index == result.step_index
        and event.employee_id == result.employee_id
        and event.previous_status == "running"
        and event.next_status == expected_status
        and event.event_type == expected_event_type
        and completed_ids_are_valid
    )
    if not valid:
        _raise("transition_contract")


def _raise(classification: ExecutedStepTransitionPersistenceClassification) -> None:
    raise ExecutedStepTransitionPersistenceCompatibilityError(classification) from None
