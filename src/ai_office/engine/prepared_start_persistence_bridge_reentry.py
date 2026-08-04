"""Bridge one exact Phase 47 result to the existing Phase 41 boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_start_persistence_routing_reentry import (
    PreparedStartPersistenceRoutingError,
    route_prepared_start_persistence_reentry,
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
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "employee_contract",
    "start_contract",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "persistence_contract",
    "dependency_error",
    "dependency_rollback",
]
_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
Function = Callable[..., RunningStatePersistenceResult | WorkflowProgressionDecision]


@dataclass(frozen=True)
class PreparedStartPersistenceBridgeFailureDetail:
    classification: Classification


class PreparedStartPersistenceBridgeError(ValueError):
    """Raised when Phase 48 cannot safely route its supplied result."""


class PreparedStartPersistenceBridgeCompatibilityError(
    PreparedStartPersistenceBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("prepared-start persistence bridge inputs are incompatible")
        self.detail = PreparedStartPersistenceBridgeFailureDetail(classification)


def route_prepared_start_persistence_bridge_reentry(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    persistence_routing_function: Function = route_prepared_start_persistence_reentry,
) -> (
    RunningStatePersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route Phase 47 output once to Phase 41, or stop a terminal result unchanged."""
    _validate_top_level(
        result,
        workflow,
        employee,
        state_path,
        events_path,
        persistence_routing_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(employee) is EmployeeDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is WorkflowProgressionDecision:
        _completion(result, workflow)
    elif type(result) is PersistedExecutionOutcome:
        _failure(result, workflow)
    else:
        assert type(result) is PreparedStepExecutionStart
        _start(result, workflow, employee)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _terminal(result, workflow, state_path, events_path, "succeeded")
        _unchanged(state_path, events_path, original)
        return result
    if type(result) is PersistedExecutionOutcome:
        _terminal(result, workflow, state_path, events_path, "failed")
        _unchanged(state_path, events_path, original)
        return result
    assert type(result) is PreparedStepExecutionStart
    _prior_success_prefix(result, workflow, state_path, events_path)
    try:
        value = persistence_routing_function(
            result, workflow, employee, state_path, events_path
        )
    except PreparedStartPersistenceRoutingError as error:
        _restore(state_path, events_path, original)
        raise error
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    _validate_persisted(value, result, state_path, events_path, original)
    return value


def _validate_top_level(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    function: object,
) -> None:
    if type(result) not in (
        PreparedStepExecutionStart,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(function):
        _raise("persistence_contract")


def _start(
    value: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    request, state = value.request, value.running_state
    if (
        type(request) is not ModelInvocationRequest
        or type(state) is not WorkflowExecutionState
    ):
        _raise("start_contract")
    if type(state.current_step_index) is not int or not (
        2 <= state.current_step_index <= len(workflow.steps)
    ):
        _raise("start_contract")
    step = workflow.steps[state.current_step_index - 1]
    prefix = tuple(item.id for item in workflow.steps[: state.current_step_index - 1])
    if not (
        state.status == "running"
        and state.workflow_id == workflow.id
        and state.current_step_id == step.id
        and state.current_employee_id == step.employee == employee.id
        and state.completed_step_ids == prefix
        and state.last_failure_category is None
        and request.model == employee.model
        and request.system_instructions == employee.instructions
        and request.task_instructions == step.instructions
        and request.allowed_tools == tuple(employee.allowed_tools)
    ):
        _raise("start_contract")


def _completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        value.decision == "workflow_complete"
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
        and value.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if (
        value.outcome != "persisted_failure"
        or type(value.current_step_index) is not int
        or not 1 <= value.current_step_index <= len(workflow.steps)
    ):
        _raise("failure_contract")
    step = workflow.steps[value.current_step_index - 1]
    if not (
        value.workflow_id == workflow.id
        and value.current_step_id == step.id
        and value.current_employee_id == step.employee
        and value.failure_category in _CATEGORIES
    ):
        _raise("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
    except OSError:
        _raise("state_target")
    try:
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("event_target")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _raise("state_target")
    try:
        return state_bytes, events.read_bytes()
    except OSError:
        _raise("event_target")


def _terminal(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    status: Literal["succeeded", "failed"],
) -> None:
    try:
        state, _ = load_strict_terminal_history(workflow, state_path, events_path)
    except TerminalHistoryContractError:
        _raise("terminal_contract")
    identity = (
        state.workflow_id,
        state.current_step_id,
        state.current_step_index,
        state.current_employee_id,
    )
    expected = (
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.current_employee_id,
    )
    if state.status != status or identity != expected:
        _raise("terminal_contract")
    if (
        type(value) is PersistedExecutionOutcome
        and state.last_failure_category != value.failure_category
    ):
        _raise("terminal_contract")


def _prior_success_prefix(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> None:
    try:
        state, _ = load_strict_terminal_history(workflow, state_path, events_path)
    except TerminalHistoryContractError:
        _raise("terminal_contract")
    prior = workflow.steps[start.running_state.current_step_index - 2]
    if not (
        state.status == "succeeded"
        and (
            state.workflow_id,
            state.current_step_id,
            state.current_step_index,
            state.current_employee_id,
        )
        == (
            workflow.id,
            prior.id,
            start.running_state.current_step_index - 1,
            prior.employee,
        )
        and state.completed_step_ids == start.running_state.completed_step_ids
        and state.last_failure_category is None
    ):
        _raise("terminal_contract")


def _validate_persisted(
    value: object,
    start: PreparedStepExecutionStart,
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
) -> None:
    try:
        event_unchanged = events.is_file() and events.read_bytes() == original[1]
        state_bytes = state.read_bytes()
        loaded = load_workflow_execution_state(state)
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _restore(state, events, original)
        _raise("persistence_contract")
    valid = (
        type(value) is RunningStatePersistenceResult
        and type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(state_bytes)
        and event_unchanged
        and loaded == start.running_state
    )
    if not valid:
        _restore(state, events, original)
        _raise("persistence_contract")


def _unchanged(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
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
        _raise("dependency_error")


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            if not path.is_file() or path.read_bytes() != before:
                path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _raise(classification: Classification) -> None:
    raise PreparedStartPersistenceBridgeCompatibilityError(classification) from None
