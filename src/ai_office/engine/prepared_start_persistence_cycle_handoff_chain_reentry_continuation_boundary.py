"""Phase 125 prepared-start persistence cycle handoff chain boundary."""

# ruff: noqa: E501,E701

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffReentryContinuationError as Phase118Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    serialize_workflow_execution_state_json,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
)

Classification = Literal[
    "result_type", "workflow_definition", "start_contract", "employee_contract",
    "completion_contract", "failure_contract", "state_target", "event_target",
    "target_conflict", "terminal_contract", "persistence_contract", "dependency_error",
    "dependency_rollback",
]
Phase118Function = Callable[
    [object, object, object, object, object],
    RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PreparedStartPersistenceCycleHandoffChainReentryContinuationFailureDetail:
    classification: Classification


class PreparedStartPersistenceCycleHandoffChainReentryContinuationError(ValueError):
    """Raised when the Phase 125 boundary cannot safely hand off one result."""


class PreparedStartPersistenceCycleHandoffChainReentryContinuationCompatibilityError(
    PreparedStartPersistenceCycleHandoffChainReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "prepared-start persistence cycle handoff chain inputs are incompatible"
        )
        self.detail = PreparedStartPersistenceCycleHandoffChainReentryContinuationFailureDetail(
            classification
        )


def route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase118_function: Phase118Function = (
        route_prepared_start_persistence_cycle_handoff_reentry_continuation_boundary
    ),
) -> RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact Phase 124 result through the public Phase 118 boundary once."""
    _validate_inputs(result, workflow, state_path, events_path, phase118_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is PreparedStepExecutionStart:
        _validate_employee(employee)
        assert type(employee) is EmployeeDefinition
        _validate_start(result, workflow, employee)
    elif type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow, employee)
    else:
        _validate_failure(result, workflow, employee)

    _validate_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is PreparedStepExecutionStart
    assert type(employee) is EmployeeDefinition
    _validate_predecessor(result, workflow, state_path, events_path)
    try:
        value = phase118_function(result, workflow, employee, state_path, events_path)
    except Phase118Error as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _compatibility_error("dependency_error")

    try:
        _validate_persistence(value, result, state_path, events_path, original)
    except PreparedStartPersistenceCycleHandoffChainReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _validate_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    dependency: object,
) -> None:
    if type(result) not in (
        PreparedStepExecutionStart,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _compatibility_error("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _compatibility_error("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _compatibility_error("state_target")
    if type(events) is not _PATH_TYPE:
        _compatibility_error("event_target")
    if state == events:
        _compatibility_error("target_conflict")
    if not callable(dependency):
        _compatibility_error("persistence_contract")


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    return (
        type(workflow.id) is str
        and type(workflow.name) is str
        and type(workflow.description) is str
        and type(workflow.steps) is list
        and bool(workflow.steps)
        and all(
            type(step) is WorkflowStepDefinition
            and type(step.id) is str
            and type(step.name) is str
            and type(step.employee) is str
            and type(step.instructions) is str
            for step in workflow.steps
        )
    )


def _valid_employee(value: EmployeeDefinition) -> bool:
    return (
        type(value.id) is str
        and type(value.name) is str
        and type(value.role) is str
        and type(value.instructions) is str
        and type(value.model) is str
        and type(value.allowed_tools) is list
        and all(type(item) is str for item in value.allowed_tools)
    )


def _validate_employee(value: object) -> None:
    if type(value) is not EmployeeDefinition:
        _compatibility_error("employee_contract")
    assert type(value) is EmployeeDefinition
    if not _valid_employee(value):
        _compatibility_error("employee_contract")


def _validate_start(
    value: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    request, state = value.request, value.running_state
    if type(request) is not ModelInvocationRequest or type(state) is not WorkflowExecutionState:
        _compatibility_error("start_contract")
    if type(state.current_step_index) is not int or not 3 <= state.current_step_index <= len(workflow.steps):
        _compatibility_error("start_contract")
    step = workflow.steps[state.current_step_index - 1]
    expected_completed = tuple(item.id for item in workflow.steps[: state.current_step_index - 1])
    if not (
        type(state.workflow_id) is str
        and state.workflow_id == workflow.id
        and type(state.status) is str
        and state.status == "running"
        and type(state.current_step_id) is str
        and state.current_step_id == step.id
        and type(state.current_employee_id) is str
        and state.current_employee_id == step.employee
        and state.current_employee_id == employee.id
        and type(state.completed_step_ids) is tuple
        and all(type(item) is str for item in state.completed_step_ids)
        and state.completed_step_ids == expected_completed
        and state.last_failure_category is None
        and type(request.model) is str
        and request.model == employee.model
        and type(request.system_instructions) is str
        and request.system_instructions == employee.instructions
        and type(request.task_instructions) is str
        and request.task_instructions == step.instructions
        and type(request.allowed_tools) is tuple
        and all(type(item) is str for item in request.allowed_tools)
        and request.allowed_tools == tuple(employee.allowed_tools)
    ):
        _compatibility_error("start_contract")


def _validate_completion(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    final = workflow.steps[-1]
    if not (
        employee is None
        and type(value.decision) is str
        and value.decision == "workflow_complete"
        and type(value.workflow_id) is str
        and value.workflow_id == workflow.id
        and type(value.current_step_id) is str
        and value.current_step_id == final.id
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and type(value.current_employee_id) is str
        and value.current_employee_id == final.employee
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and type(value.reason) is str
        and value.reason == "last_step_succeeded"
    ):
        _compatibility_error("completion_contract")


def _validate_failure(
    value: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    index = value.current_step_index
    if not (
        employee is None
        and type(value.outcome) is str
        and value.outcome == "persisted_failure"
        and type(value.workflow_id) is str
        and value.workflow_id == workflow.id
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and type(value.current_step_id) is str
        and value.current_step_id == workflow.steps[index - 1].id
        and type(value.current_employee_id) is str
        and value.current_employee_id == workflow.steps[index - 1].employee
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _compatibility_error("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    for path, classification in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file():
                _compatibility_error(classification)
        except OSError:
            _compatibility_error(classification)


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _compatibility_error("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _compatibility_error("event_target")
    return state_bytes, event_bytes


def _validate_terminal(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    status: Literal["succeeded", "failed"],
) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _compatibility_error("terminal_contract")
    if not (
        type(persisted) is WorkflowExecutionState
        and type(persisted.status) is str
        and persisted.status == status
        and type(persisted.workflow_id) is str
        and type(persisted.current_step_id) is str
        and type(persisted.current_step_index) is int
        and type(persisted.current_employee_id) is str
        and (
            persisted.workflow_id,
            persisted.current_step_id,
            persisted.current_step_index,
            persisted.current_employee_id,
        )
        == (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.current_employee_id,
        )
    ):
        _compatibility_error("terminal_contract")
    if (
        type(value) is PersistedExecutionOutcome
        and persisted.last_failure_category != value.failure_category
    ):
        _compatibility_error("terminal_contract")


def _validate_predecessor(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> WorkflowExecutionState:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _compatibility_error("terminal_contract")
    current = start.running_state
    previous_index = current.current_step_index - 1
    previous = workflow.steps[previous_index - 1]
    expected_completed = tuple(item.id for item in workflow.steps[:previous_index])
    if not (
        type(persisted) is WorkflowExecutionState
        and type(persisted.workflow_id) is str
        and persisted.workflow_id == workflow.id
        and type(persisted.status) is str
        and persisted.status == "succeeded"
        and type(persisted.current_step_id) is str
        and persisted.current_step_id == previous.id
        and type(persisted.current_step_index) is int
        and persisted.current_step_index == previous_index
        and type(persisted.current_employee_id) is str
        and persisted.current_employee_id == previous.employee
        and type(persisted.completed_step_ids) is tuple
        and all(type(item) is str for item in persisted.completed_step_ids)
        and persisted.completed_step_ids == expected_completed
        and persisted.last_failure_category is None
    ):
        _compatibility_error("terminal_contract")
    return persisted


def _validate_persistence(
    value: object,
    start: PreparedStepExecutionStart,
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
) -> None:
    try:
        state_bytes = state.read_bytes()
        event_bytes = events.read_bytes()
        loaded = load_workflow_execution_state(state)
    except (OSError, UnicodeError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _compatibility_error("persistence_contract")
    expected = serialize_workflow_execution_state_json(start.running_state).encode("utf-8")
    if not (
        type(value) is RunningStatePersistenceResult
        and type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(state_bytes)
        and state_bytes == expected
        and event_bytes == original[1]
        and type(loaded) is WorkflowExecutionState
        and loaded == start.running_state
    ):
        _compatibility_error("persistence_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
) -> None:
    changed = (_changed(state, original[0]), _changed(events, original[1]))
    if not any(changed):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]):
        _compatibility_error("dependency_rollback")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _compatibility_error(classification)


def _compatibility_error(classification: Classification) -> None:
    raise PreparedStartPersistenceCycleHandoffChainReentryContinuationCompatibilityError(
        classification
    ) from None
