"""Shared strict terminal-history contract for read-only routing bridges."""

from pathlib import Path
from typing import get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
)

_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


class TerminalHistoryContractError(ValueError):
    """Raised when terminal persisted history violates the shared contract."""


def load_strict_terminal_history(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    """Load and fully validate one terminal workflow state and event history."""
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ) as error:
        raise TerminalHistoryContractError from error
    validate_strict_terminal_history(workflow, history.state, history.events)
    return history.state, history.events


def validate_strict_terminal_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> None:
    """Require exact terminal state, prefix events, and terminal event fields."""
    if (
        state.status not in {"succeeded", "failed"}
        or state.workflow_id != workflow.id
        or not events
        or not 1 <= state.current_step_index <= len(workflow.steps)
    ):
        _invalid()
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _invalid()
    expected_ids = (
        tuple(step.id for step in workflow.steps[: state.current_step_index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: state.current_step_index - 1])
    )
    if state.completed_step_ids != expected_ids:
        _invalid()
    prior_ids = expected_ids[:-1] if state.status == "succeeded" else expected_ids
    if len(events) != len(prior_ids) + 1:
        _invalid()
    for event, step_id in zip(events[:-1], prior_ids, strict=True):
        step_index = next(
            (
                index
                for index, step in enumerate(workflow.steps, 1)
                if step.id == step_id
            ),
            0,
        )
        if not (
            event.event_type == "step_succeeded"
            and event.workflow_id == state.workflow_id
            and event.step_id == step_id
            and event.step_index == step_index
            and event.employee_id == workflow.steps[step_index - 1].employee
            and event.previous_status == "running"
            and event.next_status == "succeeded"
        ):
            _invalid()
    _validate_terminal_event(state, events[-1])


def _validate_terminal_event(
    state: WorkflowExecutionState, event: RuntimeStepEvent
) -> None:
    base = (
        event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
    )
    if state.status == "succeeded":
        valid = (
            base
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and state.last_failure_category is None
            and event.failure_category is None
            and isinstance(event.response_id, str)
            and isinstance(event.output_text, str)
            and event.message is None
        )
    else:
        valid = (
            base
            and event.event_type == "step_failed"
            and event.next_status == "failed"
            and state.last_failure_category in _FAILURE_CATEGORIES
            and event.failure_category == state.last_failure_category
            and event.response_id is None
            and event.output_text is None
            and isinstance(event.message, str)
        )
    if not valid:
        _invalid()


def _invalid() -> None:
    raise TerminalHistoryContractError from None
