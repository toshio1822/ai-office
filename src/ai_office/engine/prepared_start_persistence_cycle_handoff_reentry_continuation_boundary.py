"""Phase 118 prepared-start persistence cycle handoff reentry continuation boundary."""

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
from ai_office.engine.prepared_start_persistence_cycle_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleReentryContinuationError,
    route_prepared_start_persistence_cycle_reentry_continuation_boundary,
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
Phase111Function = Callable[[object, object, object, object, object], RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PreparedStartPersistenceCycleHandoffReentryContinuationFailureDetail:
    classification: Classification


class PreparedStartPersistenceCycleHandoffReentryContinuationError(ValueError):
    """Raised when the Phase 118 handoff cannot safely continue its result."""


class PreparedStartPersistenceCycleHandoffReentryContinuationCompatibilityError(
    PreparedStartPersistenceCycleHandoffReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("prepared-start persistence cycle reentry continuation inputs are incompatible")
        self.detail = PreparedStartPersistenceCycleHandoffReentryContinuationFailureDetail(classification)


def route_prepared_start_persistence_cycle_handoff_reentry_continuation_boundary(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase111_function: Phase111Function = route_prepared_start_persistence_cycle_reentry_continuation_boundary,
) -> RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome:
    _inputs(result, workflow, state_path, events_path, phase111_function)
    assert type(workflow) is WorkflowDefinition and type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is PreparedStepExecutionStart:
        if type(employee) is not EmployeeDefinition:
            _raise("employee_contract")
        _employee(employee, result)
        _start(result, workflow, employee)
    elif type(result) is WorkflowProgressionDecision:
        _completion(result, workflow, employee)
    else:
        _failure(result, workflow, employee)
    _targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _terminal(result, workflow, state_path, events_path, "succeeded")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _terminal(result, workflow, state_path, events_path, "failed")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    assert type(result) is PreparedStepExecutionStart and type(employee) is EmployeeDefinition
    _predecessor(result, workflow, state_path, events_path)
    try:
        value = phase111_function(result, workflow, employee, state_path, events_path)
    except PreparedStartPersistenceCycleReentryContinuationError as error:
        _restore(state_path, events_path, original)
        raise error
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _persisted(value, result, state_path, events_path, original)
    except PreparedStartPersistenceCycleHandoffReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore(state_path, events_path, original)
        raise
    return value


def _inputs(result: object, workflow: object, state: object, events: object, function: object) -> None:
    if type(result) not in (PreparedStepExecutionStart, WorkflowProgressionDecision, PersistedExecutionOutcome): _raise("result_type")
    if type(workflow) is not WorkflowDefinition: _raise("workflow_definition")
    if not _workflow_fields(workflow): _raise("workflow_definition")
    if type(state) is not _PATH_TYPE: _raise("state_target")
    if type(events) is not _PATH_TYPE: _raise("event_target")
    if state == events: _raise("target_conflict")
    if not callable(function): _raise("persistence_contract")


def _employee(value: EmployeeDefinition, start: PreparedStepExecutionStart) -> None:
    if not (type(value.id) is str and type(value.name) is str and type(value.role) is str and type(value.instructions) is str and type(value.model) is str and type(value.allowed_tools) is list and all(type(x) is str for x in value.allowed_tools) and value.id == start.running_state.current_employee_id): _raise("employee_contract")


def _workflow_fields(workflow: WorkflowDefinition) -> bool:
    return (type(workflow.id) is str and type(workflow.name) is str
            and type(workflow.description) is str and type(workflow.steps) is list
            and bool(workflow.steps)
            and all(type(step) is WorkflowStepDefinition and type(step.id) is str
                    and type(step.name) is str and type(step.employee) is str
                    and type(step.instructions) is str for step in workflow.steps))


def _start(value: PreparedStepExecutionStart, workflow: WorkflowDefinition, employee: EmployeeDefinition) -> None:
    request, state = value.request, value.running_state
    if type(request) is not ModelInvocationRequest or type(state) is not WorkflowExecutionState: _raise("start_contract")
    if type(state.current_step_index) is not int or not 2 <= state.current_step_index <= len(workflow.steps): _raise("start_contract")
    step = workflow.steps[state.current_step_index - 1]
    expected = tuple(x.id for x in workflow.steps[: state.current_step_index - 1])
    if not (type(state.workflow_id) is str and state.workflow_id == workflow.id and type(state.status) is str and state.status == "running" and type(state.current_step_id) is str and state.current_step_id == step.id and type(state.current_employee_id) is str and state.current_employee_id == step.employee and state.current_employee_id == employee.id and type(state.completed_step_ids) is tuple and all(type(x) is str for x in state.completed_step_ids) and state.completed_step_ids == expected and state.last_failure_category is None and type(request.model) is str and request.model == employee.model and type(request.system_instructions) is str and request.system_instructions == employee.instructions and type(request.task_instructions) is str and request.task_instructions == step.instructions and type(request.allowed_tools) is tuple and all(type(x) is str for x in request.allowed_tools) and request.allowed_tools == tuple(employee.allowed_tools)): _raise("start_contract")


def _completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition, employee: object | None) -> None:
    final = workflow.steps[-1]
    if not (employee is None and type(value.decision) is str and value.decision == "workflow_complete" and type(value.workflow_id) is str and value.workflow_id == workflow.id and type(value.current_step_index) is int and value.current_step_index == len(workflow.steps) and type(value.current_step_id) is str and value.current_step_id == final.id and type(value.current_employee_id) is str and value.current_employee_id == final.employee and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None and type(value.reason) is str and value.reason == "last_step_succeeded"): _raise("completion_contract")


def _failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition, employee: object | None) -> None:
    if not (employee is None and type(value.outcome) is str and value.outcome == "persisted_failure" and type(value.workflow_id) is str and value.workflow_id == workflow.id and type(value.current_step_index) is int and 1 <= value.current_step_index <= len(workflow.steps) and type(value.current_step_id) is str and value.current_step_id == workflow.steps[value.current_step_index - 1].id and type(value.current_employee_id) is str and value.current_employee_id == workflow.steps[value.current_step_index - 1].employee and type(value.failure_category) is str and value.failure_category in _FAILURE_CATEGORIES): _raise("failure_contract")


def _targets(state: Path, events: Path) -> None:
    for path, kind in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file(): _raise(kind)
        except OSError: _raise(kind)


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try: first = state.read_bytes()
    except OSError: _raise("state_target")
    try: second = events.read_bytes()
    except OSError: _raise("event_target")
    return first, second


def _terminal(value: WorkflowProgressionDecision | PersistedExecutionOutcome, workflow: WorkflowDefinition, state: Path, events: Path, status: Literal["succeeded", "failed"]) -> None:
    try: persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError): _raise("terminal_contract")
    if persisted.status != status or (persisted.workflow_id, persisted.current_step_id, persisted.current_step_index, persisted.current_employee_id) != (value.workflow_id, value.current_step_id, value.current_step_index, value.current_employee_id): _raise("terminal_contract")
    if type(value) is PersistedExecutionOutcome and persisted.last_failure_category != value.failure_category: _raise("terminal_contract")


def _predecessor(start: PreparedStepExecutionStart, workflow: WorkflowDefinition, state: Path, events: Path) -> None:
    try: persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError): _raise("terminal_contract")
    current = start.running_state
    prior = workflow.steps[current.current_step_index - 2]
    if not (type(persisted.workflow_id) is str and persisted.workflow_id == workflow.id and type(persisted.status) is str and persisted.status == "succeeded" and type(persisted.current_step_id) is str and persisted.current_step_id == prior.id and type(persisted.current_step_index) is int and persisted.current_step_index == current.current_step_index - 1 and type(persisted.current_employee_id) is str and persisted.current_employee_id == prior.employee and type(persisted.completed_step_ids) is tuple and all(type(x) is str for x in persisted.completed_step_ids) and persisted.completed_step_ids == current.completed_step_ids and persisted.last_failure_category is None): _raise("terminal_contract")


def _persisted(value: object, start: PreparedStepExecutionStart, state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    try:
        state_bytes = state.read_bytes()
        event_bytes = events.read_bytes()
        loaded = load_workflow_execution_state(state)
    except (OSError, UnicodeError, WorkflowExecutionDataError, WorkflowExecutionLoadError): _raise("persistence_contract")
    expected = serialize_workflow_execution_state_json(start.running_state).encode("utf-8")
    if not (type(value) is RunningStatePersistenceResult and type(value.state_bytes_written) is int and value.state_bytes_written == len(state_bytes) and value.state_bytes_written > 0 and state_bytes == expected and event_bytes == original[1] and loaded == start.running_state): _raise("persistence_contract")


def _changed(path: Path, before: bytes) -> bool:
    try: return not path.is_file() or path.read_bytes() != before
    except OSError: return True


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])): return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try: path.write_bytes(contents)
        except OSError: failed = True
    if failed: _raise("dependency_rollback")


def _unchanged(state: Path, events: Path, original: tuple[bytes, bytes], classification: Classification) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise PreparedStartPersistenceCycleHandoffReentryContinuationCompatibilityError(classification) from None
