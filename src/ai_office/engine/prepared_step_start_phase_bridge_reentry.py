"""Read-only bridge from one exact Phase 53 result to Phase 47."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.prepared_step_start_bridge_reentry import (
    PreparedStepStartBridgeError,
    route_prepared_step_start_bridge_reentry,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState

Classification = Literal[
    "result_type",
    "workflow_definition",
    "employee_contract",
    "prepared_step_contract",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "start_contract",
    "dependency_error",
    "dependency_rollback",
]
_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
Phase47Function = Callable[
    ...,
    PreparedStepExecutionStart
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]


@dataclass(frozen=True)
class PreparedStepStartPhaseBridgeFailureDetail:
    classification: Classification


class PreparedStepStartPhaseBridgeError(ValueError):
    """Raised when the Phase 54 bridge cannot proceed safely."""


class PreparedStepStartPhaseBridgeCompatibilityError(PreparedStepStartPhaseBridgeError):
    """Raised for a safe Phase 54 compatibility or dependency rejection."""

    def __init__(self, classification: Classification) -> None:
        super().__init__("prepared-step start phase bridge inputs are incompatible")
        self.detail = PreparedStepStartPhaseBridgeFailureDetail(classification)


def route_prepared_step_start_phase_bridge_reentry(
    result: object,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    *,
    start_bridge_function: Phase47Function = route_prepared_step_start_bridge_reentry,
) -> (
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome
):
    """Route one exact Phase 53 result without persisting any state."""
    if type(result) not in (
        PreparedWorkflowStep,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(start_bridge_function):
        _raise("start_contract")
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is PreparedWorkflowStep:
        if type(employee) is not EmployeeDefinition:
            _raise("employee_contract")
        _validate_prepared(result, workflow, employee)
    elif type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow, employee)
    else:
        _validate_failure(result, workflow, employee)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    state, _ = _history(workflow, state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        if state.status != "succeeded" or not _state_matches(result, state):
            _raise("terminal_contract")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        if state.status != "failed" or not _state_matches(result, state):
            _raise("terminal_contract")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    assert type(employee) is EmployeeDefinition
    _validate_prepared_predecessor(result, workflow, state)
    try:
        value = start_bridge_function(
            result, workflow, employee, state_path, events_path
        )
    except PreparedStepStartBridgeError:
        _restore(state_path, events_path, original)
        raise
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    _unchanged(state_path, events_path, original, "start_contract")
    _validate_start(value, result, state)
    return value


def _validate_prepared(
    value: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    if type(value.step_index) is not int or not 2 <= value.step_index <= len(
        workflow.steps
    ):
        _raise("prepared_step_contract")
    step = workflow.steps[value.step_index - 1]
    if not (
        _exact_str(value.workflow_id, workflow.id)
        and _exact_str(value.step_id, step.id)
        and _exact_str(value.employee_id, step.employee)
        and _exact_str(value.employee_id, employee.id)
        and _exact_str(value.employee_instructions, employee.instructions)
        and _exact_str(value.step_instructions, step.instructions)
        and _exact_str(value.model, employee.model)
        and _exact_str_tuple(value.allowed_tool_names, tuple(employee.allowed_tools))
    ):
        _raise("prepared_step_contract")


def _validate_completion(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    final = workflow.steps[-1]
    if not (
        employee is None
        and _exact_str(value.decision, "workflow_complete")
        and (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.current_employee_id,
        )
        == (workflow.id, final.id, len(workflow.steps), final.employee)
        and value.next_step_id
        is value.next_step_index
        is value.next_employee_id
        is None
        and _exact_str(value.reason, "last_step_succeeded")
    ):
        _raise("completion_contract")


def _validate_failure(
    value: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    if not (
        employee is None
        and _exact_str(value.outcome, "persisted_failure")
        and _exact_str(value.workflow_id, workflow.id)
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
        and _exact_str(
            value.current_step_id, workflow.steps[value.current_step_index - 1].id
        )
        and _exact_str(
            value.current_employee_id,
            workflow.steps[value.current_step_index - 1].employee,
        )
        and type(value.failure_category) is str
        and value.failure_category in _CATEGORIES
    ):
        _raise("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("terminal_contract")


def _history(workflow: WorkflowDefinition, state: Path, events: Path):
    try:
        return load_strict_terminal_history(workflow, state, events)
    except TerminalHistoryContractError:
        _raise("terminal_contract")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("terminal_contract")


def _state_matches(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    state: WorkflowExecutionState,
) -> bool:
    fields = (
        state.workflow_id,
        state.current_step_id,
        state.current_step_index,
        state.current_employee_id,
    )
    if type(value) is WorkflowProgressionDecision:
        return fields == (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.current_employee_id,
        )
    return fields + (state.last_failure_category,) == (
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.current_employee_id,
        value.failure_category,
    )


def _validate_prepared_predecessor(
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
) -> None:
    """Bind a prepared next step to the complete, exact prior terminal state."""
    previous_index = prepared.step_index - 1
    previous = workflow.steps[previous_index - 1]
    expected_completed = tuple(step.id for step in workflow.steps[:previous_index])
    if not (
        _exact_str(state.workflow_id, workflow.id)
        and _exact_str(state.status, "succeeded")
        and _exact_str(state.current_step_id, previous.id)
        and type(state.current_step_index) is int
        and state.current_step_index == previous_index
        and _exact_str(state.current_employee_id, previous.employee)
        and _exact_str_tuple(state.completed_step_ids, expected_completed)
        and state.last_failure_category is None
    ):
        _raise("terminal_contract")


def _validate_start(
    value: object, prepared: PreparedWorkflowStep, state: WorkflowExecutionState
) -> None:
    if type(value) is not PreparedStepExecutionStart:
        _raise("start_contract")
    request, running = value.request, value.running_state
    if not (
        type(request) is ModelInvocationRequest
        and type(running) is WorkflowExecutionState
        and _exact_str(request.model, prepared.model)
        and _exact_str(request.system_instructions, prepared.employee_instructions)
        and _exact_str(request.task_instructions, prepared.step_instructions)
        and _exact_str_tuple(request.allowed_tools, prepared.allowed_tool_names)
        and (
            running.workflow_id,
            running.status,
            running.current_step_id,
            running.current_step_index,
            running.current_employee_id,
        )
        == (
            prepared.workflow_id,
            "running",
            prepared.step_id,
            prepared.step_index,
            prepared.employee_id,
        )
        and _exact_str_tuple(running.completed_step_ids, state.completed_step_ids)
        and running.last_failure_category is None
    ):
        _raise("start_contract")


def _exact_str(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _exact_str_tuple(value: object, expected: tuple[str, ...]) -> bool:
    return (
        type(value) is tuple
        and all(type(item) is str for item in value)
        and value == expected
    )


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    try:
        changed = any(
            not path.is_file() or path.read_bytes() != before
            for path, before in ((state, original[0]), (events, original[1]))
        )
    except OSError:
        changed = True
    if not changed:
        return
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    try:
        changed = (
            not state.is_file()
            or not events.is_file()
            or state.read_bytes() != original[0]
            or events.read_bytes() != original[1]
        )
    except OSError:
        changed = True
    if changed:
        _restore(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise PreparedStepStartPhaseBridgeCompatibilityError(classification) from None
