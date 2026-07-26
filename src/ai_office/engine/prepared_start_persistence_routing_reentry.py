"""Route one exact Phase 40 result to Phase 35 persistence."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.prepared_running_state_reentry import (
    PreparedRunningStateReentryError,
    persist_prepared_running_state_reentry,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
)

RoutingClassification = Literal[
    "result_type",
    "start_contract",
    "completion_contract",
    "workflow_definition",
    "employee_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "persistence_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "prepared-start persistence routing inputs are incompatible"
PersistenceReentryFunction = Callable[
    [object, object, object, object, object], RunningStatePersistenceResult
]


@dataclass(frozen=True)
class PreparedStartPersistenceRoutingFailureDetail:
    classification: RoutingClassification


class PreparedStartPersistenceRoutingError(ValueError):
    pass


class PreparedStartPersistenceRoutingCompatibilityError(
    PreparedStartPersistenceRoutingError
):
    def __init__(self, classification: RoutingClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PreparedStartPersistenceRoutingFailureDetail(classification)


def route_prepared_start_persistence_reentry(
    result: object,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    *,
    persistence_reentry_function: PersistenceReentryFunction = (
        persist_prepared_running_state_reentry
    ),
) -> RunningStatePersistenceResult | WorkflowProgressionDecision:
    """Persist only the exact proposed running state, or stop completion unchanged."""
    _validate_inputs(
        result,
        workflow,
        employee,
        state_path,
        events_path,
        persistence_reentry_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        return result
    assert (
        type(result) is PreparedStepExecutionStart
        and type(employee) is EmployeeDefinition
    )
    try:
        persisted = persistence_reentry_function(
            workflow, employee, state_path, events_path, result
        )
    except PreparedRunningStateReentryError:
        _restore(state_path, events_path, original)
        raise
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    _validate_persisted(persisted, result, state_path, events_path, original)
    return persisted


def _validate_inputs(
    result: object,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    function: object,
) -> None:
    if type(result) not in (PreparedStepExecutionStart, WorkflowProgressionDecision):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(result) is PreparedStepExecutionStart:
        _validate_start(result, workflow, employee)
    else:
        _validate_completion(result, workflow, employee)
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(function):
        _raise("persistence_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("dependency_error")


def _validate_start(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    request, running = start.request, start.running_state
    if (
        type(request) is not ModelInvocationRequest
        or type(running) is not WorkflowExecutionState
    ):
        _raise("start_contract")
    if (
        isinstance(running.current_step_index, bool)
        or not isinstance(running.current_step_index, int)
        or not 1 <= running.current_step_index <= len(workflow.steps)
    ):
        _raise("start_contract")
    step = workflow.steps[running.current_step_index - 1]
    if not (
        running.status == "running"
        and running.workflow_id == workflow.id
        and running.current_step_id == step.id
        and running.current_employee_id == step.employee == employee.id
        and running.last_failure_category is None
        and isinstance(running.completed_step_ids, tuple)
        and all(isinstance(x, str) and bool(x) for x in running.completed_step_ids)
        and (
            not running.completed_step_ids
            or running.completed_step_ids[-1] != running.current_step_id
        )
        and request.model == employee.model
        and request.system_instructions == employee.instructions
        and request.task_instructions == step.instructions
        and request.allowed_tools == tuple(employee.allowed_tools)
    ):
        _raise("start_contract")


def _validate_completion(
    decision: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    final = workflow.steps[-1]
    if not (
        employee is None
        and decision.decision == "workflow_complete"
        and decision.workflow_id == workflow.id
        and decision.current_step_id == final.id
        and decision.current_step_index == len(workflow.steps)
        and decision.current_employee_id == final.employee
        and decision.next_step_id is None
        and decision.next_step_index is None
        and decision.next_employee_id is None
        and decision.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("dependency_error")


def _validate_persisted(
    value: object,
    start: PreparedStepExecutionStart,
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
) -> None:
    try:
        event_unchanged = events.is_file() and events.read_bytes() == original[1]
        loaded = load_workflow_execution_state(state)
        state_bytes = state.read_bytes()
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _restore(state, events, original)
        _raise("persistence_contract")
    valid = (
        type(value) is RunningStatePersistenceResult
        and not isinstance(value.state_bytes_written, bool)
        and isinstance(value.state_bytes_written, int)
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(state_bytes)
        and event_unchanged
        and loaded == start.running_state
    )
    if not valid:
        _restore(state, events, original)
        _raise("persistence_contract")


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            changed = not path.is_file() or path.read_bytes() != before
            if changed:
                path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _raise(classification: RoutingClassification) -> None:
    raise PreparedStartPersistenceRoutingCompatibilityError(classification) from None
