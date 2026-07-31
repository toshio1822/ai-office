"""Dispatch the Phase 94 progression boundary through Phase 88 once."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.approved_next_step_preparation_routing_phase_bridge_cycle_reentry_continuation import (
    ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationError,
    route_approved_next_step_preparation_routing_phase_bridge_cycle_reentry_continuation,
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
Phase88Function = Callable[..., PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome]
_PATH_TYPE = type(Path())
_FAILURES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationFailureDetail:
    classification: Classification


class ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationError(ValueError):
    """Raised when Phase 95 cannot safely dispatch its supplied result."""


class ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError(
    ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("approved next-step preparation dispatch phase bridge cycle reentry continuation inputs are incompatible")
        self.detail = ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationFailureDetail(classification)


def route_approved_next_step_preparation_dispatch_phase_bridge_cycle_reentry_continuation(
    result: object,
    workflow: object,
    approval: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase88_function: Phase88Function = route_approved_next_step_preparation_routing_phase_bridge_cycle_reentry_continuation,
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    _validate_inputs(result, workflow, state_path, events_path, phase88_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is WorkflowProgressionDecision:
        if result.decision == "prepare_next_step":
            _validate_prepare(result, workflow, approval, employee)
        elif result.decision == "workflow_complete":
            _validate_complete(result, workflow, approval, employee)
        else:
            _raise("decision_contract")
        terminal_status = "succeeded"
    else:
        _validate_failure(result, workflow, approval, employee)
        terminal_status = "failed"
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    _validate_terminal(result, workflow, state_path, events_path, terminal_status)
    _require_unchanged(state_path, events_path, original, "terminal_contract")
    if type(result) is not WorkflowProgressionDecision or result.decision == "workflow_complete":
        return result

    try:
        value = phase88_function(result, workflow, approval, employee, state_path, events_path)
    except ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationError as error:
        _compensate_or_raise(state_path, events_path, original)
        raise error
    except Exception:
        _compensate_or_raise(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "preparation_contract")
        _validate_prepared(value, result, workflow, employee)
    except ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError:
        raise
    return value


def _validate_inputs(result: object, workflow: object, state: object, events: object, dependency: object) -> None:
    if type(result) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
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
    if not callable(dependency):
        _raise("dependency_error")


def _validate_prepare(value: WorkflowProgressionDecision, workflow: WorkflowDefinition, approval: object, employee: object) -> None:
    if type(approval) is not NextStepPreparationApproval:
        _raise("approval_contract")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if not _employee_fields(employee):
        _raise("employee_contract")
    ci, ni = value.current_step_index, value.next_step_index
    if type(ci) is not int or type(ni) is not int or not (1 <= ci < len(workflow.steps) and ni == ci + 1):
        _raise("decision_contract")
    current, nxt = workflow.steps[ci - 1], workflow.steps[ni - 1]
    if not (type(value.decision) is str and value.decision == "prepare_next_step"
            and type(value.workflow_id) is str and value.workflow_id == workflow.id
            and type(value.current_step_id) is str and value.current_step_id == current.id
            and type(value.current_employee_id) is str and value.current_employee_id == current.employee
            and type(value.next_step_id) is str and value.next_step_id == nxt.id
            and type(value.next_employee_id) is str and value.next_employee_id == nxt.employee
            and type(value.reason) is str and value.reason == "next_step_available"
            and employee.id == value.next_employee_id):
        _raise("decision_contract")
    if not (type(approval.approved) is bool and approval.approved is True
            and type(approval.workflow_id) is str and approval.workflow_id == value.workflow_id
            and type(approval.current_step_id) is str and approval.current_step_id == value.current_step_id
            and type(approval.current_step_index) is int and approval.current_step_index == value.current_step_index
            and type(approval.next_step_id) is str and approval.next_step_id == value.next_step_id
            and type(approval.next_step_index) is int and approval.next_step_index == value.next_step_index
            and type(approval.next_employee_id) is str and approval.next_employee_id == value.next_employee_id):
        _raise("approval_contract")


def _validate_complete(value: WorkflowProgressionDecision, workflow: WorkflowDefinition, approval: object, employee: object) -> None:
    final = workflow.steps[-1]
    if not (approval is None and employee is None and type(value.decision) is str and value.decision == "workflow_complete"
            and type(value.workflow_id) is str and value.workflow_id == workflow.id
            and type(value.current_step_index) is int and value.current_step_index == len(workflow.steps)
            and type(value.current_step_id) is str and value.current_step_id == final.id
            and type(value.current_employee_id) is str and value.current_employee_id == final.employee
            and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None
            and type(value.reason) is str and value.reason == "last_step_succeeded"):
        _raise("completion_contract")


def _validate_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition, approval: object, employee: object) -> None:
    i = value.current_step_index
    if not (approval is None and employee is None and type(value.outcome) is str and value.outcome == "persisted_failure"
            and type(value.workflow_id) is str and value.workflow_id == workflow.id and type(i) is int
            and 1 <= i <= len(workflow.steps) and type(value.current_step_id) is str
            and value.current_step_id == workflow.steps[i - 1].id and type(value.current_employee_id) is str
            and value.current_employee_id == workflow.steps[i - 1].employee and type(value.failure_category) is str
            and value.failure_category in _FAILURES):
        _raise("failure_contract")


def _employee_fields(employee: EmployeeDefinition) -> bool:
    return (type(employee.id) is str and type(employee.name) is str and type(employee.role) is str
            and type(employee.instructions) is str and type(employee.model) is str and type(employee.allowed_tools) is list
            and all(type(item) is str for item in employee.allowed_tools))


def _workflow_fields(workflow: WorkflowDefinition) -> bool:
    return (type(workflow.id) is str and type(workflow.name) is str and type(workflow.description) is str
            and type(workflow.steps) is list and bool(workflow.steps)
            and all(type(step) is WorkflowStepDefinition and type(step.id) is str
                    and type(step.name) is str and type(step.employee) is str
                    and type(step.instructions) is str for step in workflow.steps))


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


def _validate_terminal(value: WorkflowProgressionDecision | PersistedExecutionOutcome, workflow: WorkflowDefinition,
                       state: Path, events: Path, status: str) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if persisted.status != status or (persisted.workflow_id, persisted.current_step_id, persisted.current_step_index,
        persisted.current_employee_id) != (value.workflow_id, value.current_step_id, value.current_step_index, value.current_employee_id):
        _raise("terminal_contract")
    if type(value) is PersistedExecutionOutcome and persisted.last_failure_category != value.failure_category:
        _raise("terminal_contract")


def _validate_prepared(value: object, decision: WorkflowProgressionDecision, workflow: WorkflowDefinition, employee: EmployeeDefinition) -> None:
    if type(value) is not PreparedWorkflowStep:
        _raise("preparation_contract")
    step = workflow.steps[decision.next_step_index - 1]  # type: ignore[operator]
    if not (type(value.workflow_id) is str and value.workflow_id == workflow.id and type(value.step_id) is str and value.step_id == step.id
            and type(value.step_index) is int and value.step_index == decision.next_step_index and type(value.employee_id) is str
            and value.employee_id == employee.id and type(value.employee_instructions) is str and value.employee_instructions == employee.instructions
            and type(value.step_instructions) is str and value.step_instructions == step.instructions and type(value.model) is str
            and value.model == employee.model and type(value.allowed_tool_names) is tuple
            and all(type(item) is str for item in value.allowed_tool_names) and value.allowed_tool_names == tuple(employee.allowed_tools)):
        _raise("preparation_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _compensate_or_raise(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
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
        _compensate_or_raise(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError(classification) from None
