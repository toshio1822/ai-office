"""Persist one validated Phase 33 running state without touching events."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_history,
    load_workflow_execution_state,
    persist_prepared_running_state,
)
from ai_office.storage.workflow_execution_history import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceTargets,
)

PreparedRunningStateReentryClassification = Literal[
    "workflow_definition",
    "employee_definition",
    "start_type",
    "start_identity",
    "start_content",
    "state_target",
    "event_target",
    "history_data",
    "history_identity",
    "workflow_identity",
    "persistence_contract",
    "event_immutability",
]
_ERROR_MESSAGE = "prepared running-state persistence inputs are incompatible"
PersistenceFunction = Callable[
    [PreparedStepExecutionStart, Path], RunningStatePersistenceResult
]


@dataclass(frozen=True)
class PreparedRunningStateReentryFailureDetail:
    classification: PreparedRunningStateReentryClassification


class PreparedRunningStateReentryError(ValueError):
    """Raised when Phase 34 cannot safely reach Phase 28."""


class PreparedRunningStateReentryCompatibilityError(PreparedRunningStateReentryError):
    def __init__(
        self, classification: PreparedRunningStateReentryClassification
    ) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PreparedRunningStateReentryFailureDetail(classification)


def persist_prepared_running_state_reentry(
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    start: object,
    *,
    persistence_function: PersistenceFunction = persist_prepared_running_state,
) -> RunningStatePersistenceResult:
    """Reload success, persist exactly one proposed running state, then verify it."""
    _validate_inputs(
        workflow, employee, state_path, events_path, start, persistence_function
    )
    assert isinstance(workflow, WorkflowDefinition)
    assert isinstance(employee, EmployeeDefinition)
    assert isinstance(state_path, Path)
    assert isinstance(events_path, Path)
    assert isinstance(start, PreparedStepExecutionStart)
    history = _load_history(state_path, events_path)
    _validate_history(workflow, history)
    _validate_start(workflow, employee, history, start)
    try:
        event_bytes = events_path.read_bytes()
    except OSError:
        _raise("event_target")
    result = persistence_function(start, state_path)
    _validate_persistence(result, state_path, events_path, event_bytes, start)
    return result


def _validate_inputs(
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    start: object,
    persistence_function: object,
) -> None:
    if not isinstance(workflow, WorkflowDefinition):
        _raise("workflow_definition")
    if not isinstance(employee, EmployeeDefinition):
        _raise("employee_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if not isinstance(start, PreparedStepExecutionStart):
        _raise("start_type")
    if not callable(persistence_function):
        _raise("persistence_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("history_data")


def _load_history(
    state_path: Path, events_path: Path
) -> LoadedWorkflowExecutionHistory:
    try:
        return load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("history_data")


def _validate_history(
    workflow: WorkflowDefinition, history: LoadedWorkflowExecutionHistory
) -> None:
    state = history.state
    if (
        state.status != "succeeded"
        or state.last_failure_category is not None
        or not history.events
    ):
        _raise("history_identity")
    event = history.events[-1]
    if not (
        event.event_type == "step_succeeded"
        and event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
        and event.next_status == "succeeded"
        and event.failure_category is None
        and event.message is None
        and state.completed_step_ids
        and state.completed_step_ids[-1] == state.current_step_id
    ):
        _raise("history_identity")
    if workflow.id != state.workflow_id or not 1 <= state.current_step_index < len(
        workflow.steps
    ):
        _raise("workflow_identity")
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _raise("workflow_identity")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        completed = [positions[item] for item in state.completed_step_ids]
    except KeyError:
        _raise("workflow_identity")
    compressed = tuple(
        item
        for index, item in enumerate(completed)
        if index == 0 or completed[index - 1] != item
    )
    if compressed != tuple(range(1, state.current_step_index + 1)):
        _raise("workflow_identity")


def _validate_start(
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
    history: LoadedWorkflowExecutionHistory,
    start: PreparedStepExecutionStart,
) -> None:
    request, running, state = start.request, start.running_state, history.state
    next_step = workflow.steps[state.current_step_index]
    if not isinstance(request, ModelInvocationRequest) or not isinstance(
        running, WorkflowExecutionState
    ):
        _raise("start_type")
    if not (
        running.workflow_id == workflow.id
        and running.status == "running"
        and running.current_step_id == next_step.id
        and running.current_step_index == state.current_step_index + 1
        and running.current_employee_id == next_step.employee
        and running.current_employee_id == employee.id
        and running.completed_step_ids == state.completed_step_ids
        and running.last_failure_category is None
    ):
        _raise("start_identity")
    if not (
        request.model == employee.model
        and request.system_instructions == employee.instructions
        and request.task_instructions == next_step.instructions
        and request.allowed_tools == tuple(employee.allowed_tools)
    ):
        _raise("start_content")


def _validate_persistence(
    result: object,
    state_path: Path,
    events_path: Path,
    event_bytes: bytes,
    start: PreparedStepExecutionStart,
) -> None:
    if (
        not isinstance(result, RunningStatePersistenceResult)
        or isinstance(result.state_bytes_written, bool)
        or not isinstance(result.state_bytes_written, int)
        or result.state_bytes_written < 1
    ):
        _raise("persistence_contract")
    try:
        persisted = load_workflow_execution_state(state_path)
        current_bytes = state_path.read_bytes()
        unchanged_events = events_path.read_bytes() == event_bytes
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _raise("persistence_contract")
    if not unchanged_events:
        _raise("event_immutability")
    if persisted != start.running_state or result.state_bytes_written != len(
        current_bytes
    ):
        _raise("persistence_contract")


def _raise(classification: PreparedRunningStateReentryClassification) -> None:
    raise PreparedRunningStateReentryCompatibilityError(classification) from None
