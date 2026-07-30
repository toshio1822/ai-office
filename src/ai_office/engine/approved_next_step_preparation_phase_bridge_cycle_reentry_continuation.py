"""Continue one exact Phase 80 result through the Phase 74 boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.approved_next_step_preparation_phase_bridge_continuation import (
    ApprovedNextStepPreparationPhaseBridgeError,
    route_approved_next_step_preparation_phase_bridge_continuation,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory

Classification = Literal[
    "result_type", "workflow_definition", "decision_contract", "completion_contract", "failure_contract",
    "approval_contract", "employee_contract", "state_target", "event_target", "target_conflict",
    "terminal_contract", "preparation_contract", "dependency_error", "dependency_rollback",
]
Phase74Function = Callable[..., PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationFailureDetail:
    classification: Classification


class ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationError(ValueError):
    """Raised when Phase 81 cannot safely continue its supplied result."""


class ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError(
    ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("approved next-step preparation phase bridge cycle reentry continuation inputs are incompatible")
        self.detail = ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationFailureDetail(classification)


def route_approved_next_step_preparation_phase_bridge_cycle_reentry_continuation(
    result: object,
    workflow: object,
    approval: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase74_function: Phase74Function = route_approved_next_step_preparation_phase_bridge_continuation,
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    _validate_inputs(result, workflow, approval, employee, state_path, events_path, phase74_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is WorkflowProgressionDecision:
        if result.decision == "prepare_next_step":
            _validate_prepare(result, workflow, approval, employee)
        else:
            _validate_completion(result, workflow, approval, employee)
    else:
        _validate_failure(result, workflow, approval, employee)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision and result.decision == "workflow_complete":
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    assert type(result) is WorkflowProgressionDecision
    _validate_terminal(result, workflow, state_path, events_path, "succeeded")
    try:
        prepared = phase74_function(result, workflow, state_path, events_path, approval, employee)
    except ApprovedNextStepPreparationPhaseBridgeError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "preparation_contract")
        _validate_prepared(prepared, result, workflow, employee)
    except ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return prepared


def _validate_inputs(result: object, workflow: object, approval: object, employee: object, state: object, events: object, function: object) -> None:
    if type(result) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
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
        _raise("preparation_contract")


def _validate_prepare(value: WorkflowProgressionDecision, workflow: WorkflowDefinition, approval: object, employee: object) -> None:
    if type(approval) is not NextStepPreparationApproval:
        _raise("approval_contract")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if not (type(employee.id) is str and type(employee.name) is str and type(employee.role) is str and type(employee.instructions) is str and type(employee.model) is str and type(employee.allowed_tools) is list and all(type(tool) is str for tool in employee.allowed_tools)):
        _raise("employee_contract")
    current = workflow.steps[value.current_step_index - 1] if type(value.current_step_index) is int and 1 <= value.current_step_index <= len(workflow.steps) else None
    next_step = workflow.steps[value.next_step_index - 1] if type(value.next_step_index) is int and 1 <= value.next_step_index <= len(workflow.steps) else None
    if current is None or next_step is None or not (type(value.decision) is str and value.decision == "prepare_next_step" and type(value.workflow_id) is str and value.workflow_id == workflow.id and type(value.current_step_index) is int and 1 <= value.current_step_index < len(workflow.steps) and type(value.current_step_id) is str and value.current_step_id == current.id and type(value.current_employee_id) is str and value.current_employee_id == current.employee and type(value.next_step_index) is int and value.next_step_index == value.current_step_index + 1 and type(value.next_step_id) is str and value.next_step_id == next_step.id and type(value.next_employee_id) is str and value.next_employee_id == next_step.employee and type(value.reason) is str and value.reason == "next_step_available"):
        _raise("decision_contract")
    if not (type(employee.id) is str and employee.id == value.next_employee_id):
        _raise("employee_contract")
    if not (approval.approved is True and type(approval.workflow_id) is str and approval.workflow_id == value.workflow_id and type(approval.current_step_id) is str and approval.current_step_id == value.current_step_id and type(approval.current_step_index) is int and approval.current_step_index == value.current_step_index and type(approval.next_step_id) is str and approval.next_step_id == value.next_step_id and type(approval.next_step_index) is int and approval.next_step_index == value.next_step_index and type(approval.next_employee_id) is str and approval.next_employee_id == value.next_employee_id):
        _raise("approval_contract")


def _validate_completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition, approval: object, employee: object) -> None:
    final = workflow.steps[-1]
    if not (approval is None and employee is None and type(value.decision) is str and value.decision == "workflow_complete" and type(value.workflow_id) is str and value.workflow_id == workflow.id and type(value.current_step_index) is int and value.current_step_index == len(workflow.steps) and type(value.current_step_id) is str and value.current_step_id == final.id and type(value.current_employee_id) is str and value.current_employee_id == final.employee and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None and type(value.reason) is str and value.reason == "last_step_succeeded"):
        _raise("completion_contract")


def _validate_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition, approval: object, employee: object) -> None:
    if not (approval is None and employee is None and type(value.outcome) is str and value.outcome == "persisted_failure" and type(value.workflow_id) is str and value.workflow_id == workflow.id and type(value.current_step_index) is int and 1 <= value.current_step_index <= len(workflow.steps) and type(value.current_step_id) is str and value.current_step_id == workflow.steps[value.current_step_index - 1].id and type(value.current_employee_id) is str and value.current_employee_id == workflow.steps[value.current_step_index - 1].employee and type(value.failure_category) is str and value.failure_category in _FAILURE_CATEGORIES):
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
        event_bytes = events.read_bytes()
    except OSError:
        _raise("event_target")
    return state_bytes, event_bytes


def _validate_terminal(value: object, workflow: WorkflowDefinition, state: Path, events: Path, status: Literal["succeeded", "failed"]) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if persisted.status != status or (persisted.workflow_id, persisted.current_step_id, persisted.current_step_index, persisted.current_employee_id) != (value.workflow_id, value.current_step_id, value.current_step_index, value.current_employee_id):
        _raise("terminal_contract")
    if type(value) is PersistedExecutionOutcome and persisted.last_failure_category != value.failure_category:
        _raise("terminal_contract")


def _validate_prepared(value: object, decision: WorkflowProgressionDecision, workflow: WorkflowDefinition, employee: EmployeeDefinition) -> None:
    if not (type(value) is PreparedWorkflowStep and type(decision.next_step_index) is int):
        _raise("preparation_contract")
    next_step = workflow.steps[decision.next_step_index - 1]
    if not (type(value.workflow_id) is str and value.workflow_id == workflow.id and type(value.step_id) is str and value.step_id == next_step.id and type(value.step_index) is int and value.step_index == decision.next_step_index and type(value.employee_id) is str and value.employee_id == employee.id and type(value.employee_instructions) is str and value.employee_instructions == employee.instructions and type(value.step_instructions) is str and value.step_instructions == next_step.instructions and type(value.model) is str and value.model == employee.model and type(value.allowed_tool_names) is tuple and all(type(item) is str for item in value.allowed_tool_names) and value.allowed_tool_names == tuple(employee.allowed_tools)):
        _raise("preparation_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
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


def _require_unchanged(state: Path, events: Path, original: tuple[bytes, bytes], classification: Classification) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationCompatibilityError(classification) from None
