"""Bridge exact Phase 61 starts to the existing Phase 55 persistence boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_start_persistence_phase_bridge_reentry import (
    PreparedStartPersistencePhaseBridgeError,
    route_prepared_start_persistence_phase_bridge_reentry,
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
Phase55Function = Callable[
    [object, object, object, object, object],
    RunningStatePersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())
_ERROR_MESSAGE = (
    "prepared-start persistence routing phase bridge inputs are incompatible"
)


@dataclass(frozen=True)
class PreparedStartPersistenceRoutingPhaseBridgeFailureDetail:
    classification: Classification


class PreparedStartPersistenceRoutingPhaseBridgeError(ValueError):
    """Raised when Phase 62 cannot safely route its supplied result."""


class PreparedStartPersistenceRoutingPhaseBridgeCompatibilityError(
    PreparedStartPersistenceRoutingPhaseBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PreparedStartPersistenceRoutingPhaseBridgeFailureDetail(
            classification
        )


def route_prepared_start_persistence_routing_phase_bridge_reentry(
    result: object,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    *,
    phase55_function: Phase55Function = (
        route_prepared_start_persistence_phase_bridge_reentry
    ),
) -> (
    RunningStatePersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route one exact Phase 61 result through Phase 55 or stop unchanged."""
    _top_level(result, workflow, state_path, events_path, phase55_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is PreparedStepExecutionStart:
        if type(employee) is not EmployeeDefinition:
            _raise("employee_contract")
        _validate_start(result, workflow, employee)
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

    assert type(result) is PreparedStepExecutionStart
    assert type(employee) is EmployeeDefinition
    _validate_predecessor(result, workflow, state_path, events_path)
    try:
        value = phase55_function(result, workflow, employee, state_path, events_path)
    except PreparedStartPersistencePhaseBridgeError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    _validate_persisted(value, result, state_path, events_path, original)
    return value


def _top_level(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (
        PreparedStepExecutionStart,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _raise("state_target")
    if type(events) is not _PATH_TYPE:
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("persistence_contract")


def _validate_start(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    request, state = start.request, start.running_state
    if (
        type(request) is not ModelInvocationRequest
        or type(state) is not WorkflowExecutionState
    ):
        _raise("start_contract")
    if type(
        state.current_step_index
    ) is not int or not 2 <= state.current_step_index <= len(workflow.steps):
        _raise("start_contract")
    step = workflow.steps[state.current_step_index - 1]
    prefix = tuple(item.id for item in workflow.steps[: state.current_step_index - 1])
    if not (
        _exact_str(state.status, "running")
        and _exact_str(state.workflow_id, workflow.id)
        and _exact_str(state.current_step_id, step.id)
        and _exact_str(state.current_employee_id, step.employee)
        and _exact_str(state.current_employee_id, employee.id)
        and _exact_tuple(state.completed_step_ids, prefix)
        and state.last_failure_category is None
        and _exact_str(request.model, employee.model)
        and _exact_str(request.system_instructions, employee.instructions)
        and _exact_str(request.task_instructions, step.instructions)
        and _exact_tuple(request.allowed_tools, tuple(employee.allowed_tools))
    ):
        _raise("start_contract")


def _validate_completion(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    final = workflow.steps[-1]
    if not (
        employee is None
        and _exact_str(value.decision, "workflow_complete")
        and _exact_str(value.workflow_id, workflow.id)
        and _exact_str(value.current_step_id, final.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact_str(value.current_employee_id, final.employee)
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and _exact_str(value.reason, "last_step_succeeded")
    ):
        _raise("completion_contract")


def _validate_failure(
    value: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    if employee is not None or not _exact_str(value.outcome, "persisted_failure"):
        _raise("failure_contract")
    if type(
        value.current_step_index
    ) is not int or not 1 <= value.current_step_index <= len(workflow.steps):
        _raise("failure_contract")
    step = workflow.steps[value.current_step_index - 1]
    if not (
        _exact_str(value.workflow_id, workflow.id)
        and _exact_str(value.current_step_id, step.id)
        and _exact_str(value.current_employee_id, step.employee)
        and type(value.failure_category) is str
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
        _raise("terminal_contract")
    if persisted.status != status or (
        persisted.workflow_id,
        persisted.current_step_id,
        persisted.current_step_index,
        persisted.current_employee_id,
    ) != (
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.current_employee_id,
    ):
        _raise("terminal_contract")
    if (
        type(value) is PersistedExecutionOutcome
        and persisted.last_failure_category != value.failure_category
    ):
        _raise("terminal_contract")


def _validate_predecessor(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    current = start.running_state
    prior = workflow.steps[current.current_step_index - 2]
    if not (
        persisted.status == "succeeded"
        and (
            persisted.workflow_id,
            persisted.current_step_id,
            persisted.current_step_index,
            persisted.current_employee_id,
        )
        == (workflow.id, prior.id, current.current_step_index - 1, prior.employee)
        and _exact_tuple(persisted.completed_step_ids, current.completed_step_ids)
        and persisted.last_failure_category is None
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
        state_bytes = state.read_bytes()
        event_unchanged = events.is_file() and events.read_bytes() == original[1]
        loaded = load_workflow_execution_state(state)
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _restore(state, events, original)
        _raise("persistence_contract")
    expected = serialize_workflow_execution_state_json(start.running_state).encode()
    if not (
        type(value) is RunningStatePersistenceResult
        and type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(state_bytes)
        and state_bytes == expected
        and event_unchanged
        and loaded == start.running_state
    ):
        _restore(state, events, original)
        _raise("persistence_contract")


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


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
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


def _raise(classification: Classification) -> None:
    raise PreparedStartPersistenceRoutingPhaseBridgeCompatibilityError(
        classification
    ) from None


def _exact_str(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _exact_tuple(value: object, expected: tuple[str, ...]) -> bool:
    return (
        type(value) is tuple
        and all(type(item) is str for item in value)
        and value == expected
    )
