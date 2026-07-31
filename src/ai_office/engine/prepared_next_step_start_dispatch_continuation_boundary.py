"""Dispatch one exact Phase 95 result through the Phase 89 boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_next_step_start_dispatch_phase_bridge_cycle_reentry_continuation import (
    PreparedNextStepStartDispatchPhaseBridgeCycleReentryContinuationError,
    route_prepared_next_step_start_dispatch_phase_bridge_cycle_reentry_continuation,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState

Classification = Literal[
    "result_type", "workflow_definition", "prepared_step_contract", "employee_contract",
    "completion_contract", "failure_contract", "state_target", "event_target", "target_conflict",
    "terminal_contract", "start_contract", "dependency_error", "dependency_rollback",
]
Phase89Function = Callable[..., PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PreparedNextStepStartDispatchContinuationFailureDetail:
    classification: Classification


class PreparedNextStepStartDispatchContinuationError(ValueError):
    """Raised when Phase 96 cannot safely dispatch its supplied result."""


class PreparedNextStepStartDispatchContinuationCompatibilityError(
    PreparedNextStepStartDispatchContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("prepared next-step start dispatch continuation boundary inputs are incompatible")
        self.detail = PreparedNextStepStartDispatchContinuationFailureDetail(classification)


def route_prepared_next_step_start_dispatch_continuation_boundary(
    result: object,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    *,
    phase89_function: Phase89Function = route_prepared_next_step_start_dispatch_phase_bridge_cycle_reentry_continuation,
) -> PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome:
    _validate_inputs(result, workflow, state_path, events_path, phase89_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is PreparedWorkflowStep:
        _validate_employee(employee, result)
        assert type(employee) is EmployeeDefinition
        _validate_prepared(result, workflow, employee)
    elif type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow, employee)
    else:
        _validate_failure(result, workflow, employee)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    assert type(result) is PreparedWorkflowStep and type(employee) is EmployeeDefinition
    predecessor = _validate_predecessor(result, workflow, state_path, events_path)
    try:
        value = phase89_function(result, workflow, employee, state_path, events_path)
    except PreparedNextStepStartDispatchPhaseBridgeCycleReentryContinuationError as error:
        _restore(state_path, events_path, original)
        raise error
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _unchanged(state_path, events_path, original, "start_contract")
        _validate_start(value, result, predecessor)
    except PreparedNextStepStartDispatchContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore(state_path, events_path, original)
        raise
    return value


def _validate_inputs(result: object, workflow: object, state: object, events: object, function: object) -> None:
    if type(result) not in (PreparedWorkflowStep, WorkflowProgressionDecision, PersistedExecutionOutcome):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not _workflow_fields(workflow):
        _raise("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _raise("state_target")
    if type(events) is not _PATH_TYPE:
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("start_contract")


def _validate_employee(value: object | None, prepared: PreparedWorkflowStep) -> None:
    if type(value) is not EmployeeDefinition:
        _raise("employee_contract")
    assert type(value) is EmployeeDefinition
    if not (type(value.id) is str and type(value.name) is str and type(value.role) is str
            and type(value.instructions) is str and type(value.model) is str
            and type(value.allowed_tools) is list and all(type(item) is str for item in value.allowed_tools)
            and value.id == prepared.employee_id):
        _raise("employee_contract")


def _workflow_fields(workflow: WorkflowDefinition) -> bool:
    return (type(workflow.id) is str and type(workflow.name) is str
            and type(workflow.description) is str and type(workflow.steps) is list
            and bool(workflow.steps)
            and all(type(step) is WorkflowStepDefinition and type(step.id) is str
                    and type(step.name) is str and type(step.employee) is str
                    and type(step.instructions) is str for step in workflow.steps))


def _validate_prepared(value: PreparedWorkflowStep, workflow: WorkflowDefinition,
                       employee: EmployeeDefinition) -> None:
    if type(value.step_index) is not int or not 2 <= value.step_index <= len(workflow.steps):
        _raise("prepared_step_contract")
    step = workflow.steps[value.step_index - 1]
    if not (type(value.workflow_id) is str and value.workflow_id == workflow.id
            and type(value.step_id) is str and value.step_id == step.id
            and type(value.employee_id) is str and value.employee_id == step.employee
            and value.employee_id == employee.id
            and type(value.employee_instructions) is str and value.employee_instructions == employee.instructions
            and type(value.step_instructions) is str and value.step_instructions == step.instructions
            and type(value.model) is str and value.model == employee.model
            and type(value.allowed_tool_names) is tuple
            and all(type(item) is str for item in value.allowed_tool_names)
            and value.allowed_tool_names == tuple(employee.allowed_tools)):
        _raise("prepared_step_contract")


def _validate_completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition,
                         employee: object | None) -> None:
    final = workflow.steps[-1]
    if not (employee is None and type(value.decision) is str and value.decision == "workflow_complete"
            and type(value.workflow_id) is str and value.workflow_id == workflow.id
            and type(value.current_step_index) is int and value.current_step_index == len(workflow.steps)
            and type(value.current_step_id) is str and value.current_step_id == final.id
            and type(value.current_employee_id) is str and value.current_employee_id == final.employee
            and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None
            and type(value.reason) is str and value.reason == "last_step_succeeded"):
        _raise("completion_contract")


def _validate_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition,
                      employee: object | None) -> None:
    if not (employee is None and type(value.outcome) is str and value.outcome == "persisted_failure"
            and type(value.workflow_id) is str and value.workflow_id == workflow.id
            and type(value.current_step_index) is int and 1 <= value.current_step_index <= len(workflow.steps)
            and type(value.current_step_id) is str
            and value.current_step_id == workflow.steps[value.current_step_index - 1].id
            and type(value.current_employee_id) is str
            and value.current_employee_id == workflow.steps[value.current_step_index - 1].employee
            and type(value.failure_category) is str and value.failure_category in _FAILURE_CATEGORIES):
        _raise("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    for path, classification in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file():
                _raise(classification)
        except OSError:
            _raise(classification)


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _raise("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _raise("event_target")
    return state_bytes, event_bytes


def _validate_terminal(value: WorkflowProgressionDecision | PersistedExecutionOutcome,
                       workflow: WorkflowDefinition, state: Path, events: Path,
                       status: Literal["succeeded", "failed"]) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if persisted.status != status or (persisted.workflow_id, persisted.current_step_id,
        persisted.current_step_index, persisted.current_employee_id) != (
        value.workflow_id, value.current_step_id, value.current_step_index, value.current_employee_id):
        _raise("terminal_contract")
    if type(value) is PersistedExecutionOutcome and persisted.last_failure_category != value.failure_category:
        _raise("terminal_contract")


def _validate_predecessor(prepared: PreparedWorkflowStep, workflow: WorkflowDefinition,
                          state: Path, events: Path) -> WorkflowExecutionState:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    previous_index = prepared.step_index - 1
    previous = workflow.steps[previous_index - 1]
    expected_completed = tuple(step.id for step in workflow.steps[:previous_index])
    if not (type(persisted.workflow_id) is str and persisted.workflow_id == workflow.id
            and type(persisted.status) is str and persisted.status == "succeeded"
            and type(persisted.current_step_id) is str and persisted.current_step_id == previous.id
            and type(persisted.current_step_index) is int and persisted.current_step_index == previous_index
            and type(persisted.current_employee_id) is str and persisted.current_employee_id == previous.employee
            and type(persisted.completed_step_ids) is tuple
            and all(type(item) is str for item in persisted.completed_step_ids)
            and persisted.completed_step_ids == expected_completed
            and persisted.last_failure_category is None):
        _raise("terminal_contract")
    return persisted


def _validate_start(value: object, prepared: PreparedWorkflowStep,
                    predecessor: WorkflowExecutionState) -> None:
    if type(value) is not PreparedStepExecutionStart:
        _raise("start_contract")
    request, running = value.request, value.running_state
    if not (type(request) is ModelInvocationRequest and type(running) is WorkflowExecutionState
            and type(request.model) is str and request.model == prepared.model
            and type(request.system_instructions) is str
            and request.system_instructions == prepared.employee_instructions
            and type(request.task_instructions) is str
            and request.task_instructions == prepared.step_instructions
            and type(request.allowed_tools) is tuple
            and all(type(item) is str for item in request.allowed_tools)
            and request.allowed_tools == prepared.allowed_tool_names
            and type(running.workflow_id) is str and running.workflow_id == prepared.workflow_id
            and type(running.status) is str and running.status == "running"
            and type(running.current_step_id) is str and running.current_step_id == prepared.step_id
            and type(running.current_step_index) is int and running.current_step_index == prepared.step_index
            and type(running.current_employee_id) is str and running.current_employee_id == prepared.employee_id
            and type(running.completed_step_ids) is tuple
            and all(type(item) is str for item in running.completed_step_ids)
            and running.completed_step_ids == predecessor.completed_step_ids
            and running.last_failure_category is None):
        _raise("start_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _unchanged(state: Path, events: Path, original: tuple[bytes, bytes], classification: Classification) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise PreparedNextStepStartDispatchContinuationCompatibilityError(classification) from None
