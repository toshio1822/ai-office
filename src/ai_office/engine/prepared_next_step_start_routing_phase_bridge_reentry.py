"""Read-only bridge from one exact Phase 60 result to Phase 54."""

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
from ai_office.engine.prepared_step_start_phase_bridge_reentry import (
    PreparedStepStartPhaseBridgeError,
    route_prepared_step_start_phase_bridge_reentry,
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
Phase54Function = Callable[
    [object, object, object, object, object],
    PreparedStepExecutionStart
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())
_ERROR_MESSAGE = "prepared next-step start routing phase bridge inputs are incompatible"


@dataclass(frozen=True)
class PreparedNextStepStartRoutingPhaseBridgeFailureDetail:
    classification: Classification


class PreparedNextStepStartRoutingPhaseBridgeError(ValueError):
    """Raised when Phase 61 cannot safely route its supplied result."""


class PreparedNextStepStartRoutingPhaseBridgeCompatibilityError(
    PreparedNextStepStartRoutingPhaseBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PreparedNextStepStartRoutingPhaseBridgeFailureDetail(
            classification
        )


def route_prepared_next_step_start_routing_phase_bridge_reentry(
    result: object,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    *,
    phase54_function: Phase54Function = route_prepared_step_start_phase_bridge_reentry,
) -> (
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome
):
    """Route one exact Phase 60 result through Phase 54 or stop unchanged."""
    _validate_inputs(result, workflow, state_path, events_path, phase54_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
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
    if type(result) is WorkflowProgressionDecision:
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is PreparedWorkflowStep
    assert type(employee) is EmployeeDefinition
    state = _validate_predecessor(result, workflow, state_path, events_path)
    try:
        started = phase54_function(result, workflow, employee, state_path, events_path)
    except PreparedStepStartPhaseBridgeError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "start_contract")
        _validate_start(started, result, state)
    except PreparedNextStepStartRoutingPhaseBridgeCompatibilityError:
        _restore_changed(state_path, events_path, original)
        raise
    return started


def _validate_inputs(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (
        PreparedWorkflowStep,
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
        _raise("start_contract")


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
        and _exact_tuple(value.allowed_tool_names, tuple(employee.allowed_tools))
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
        and _exact_str(value.workflow_id, workflow.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact_str(value.current_step_id, final.id)
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
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
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
        result.workflow_id,
        result.current_step_id,
        result.current_step_index,
        result.current_employee_id,
    ):
        _raise("terminal_contract")
    if (
        type(result) is PersistedExecutionOutcome
        and persisted.last_failure_category != result.failure_category
    ):
        _raise("terminal_contract")


def _validate_predecessor(
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> WorkflowExecutionState:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    previous_index = prepared.step_index - 1
    previous = workflow.steps[previous_index - 1]
    expected_completed = tuple(step.id for step in workflow.steps[:previous_index])
    if not (
        _exact_str(persisted.workflow_id, workflow.id)
        and _exact_str(persisted.status, "succeeded")
        and _exact_str(persisted.current_step_id, previous.id)
        and type(persisted.current_step_index) is int
        and persisted.current_step_index == previous_index
        and _exact_str(persisted.current_employee_id, previous.employee)
        and _exact_tuple(persisted.completed_step_ids, expected_completed)
        and persisted.last_failure_category is None
    ):
        _raise("terminal_contract")
    return persisted


def _validate_start(
    value: object,
    prepared: PreparedWorkflowStep,
    state: WorkflowExecutionState,
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
        and _exact_tuple(request.allowed_tools, prepared.allowed_tool_names)
        and _exact_str(running.workflow_id, prepared.workflow_id)
        and _exact_str(running.status, "running")
        and _exact_str(running.current_step_id, prepared.step_id)
        and type(running.current_step_index) is int
        and running.current_step_index == prepared.step_index
        and _exact_str(running.current_employee_id, prepared.employee_id)
        and _exact_tuple(running.completed_step_ids, state.completed_step_ids)
        and running.last_failure_category is None
    ):
        _raise("start_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            if _changed(path, before):
                path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_changed(state, events, original)
        _raise(classification)


def _exact_str(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _exact_tuple(value: object, expected: tuple[str, ...]) -> bool:
    return (
        type(value) is tuple
        and all(type(item) is str for item in value)
        and value == expected
    )


def _raise(classification: Classification) -> None:
    raise PreparedNextStepStartRoutingPhaseBridgeCompatibilityError(
        classification
    ) from None
