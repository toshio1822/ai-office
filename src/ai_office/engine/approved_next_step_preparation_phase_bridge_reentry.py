"""Read-only bridge from one exact Phase 59 result to Phase 53."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.approved_next_step_preparation_bridge_reentry import (
    ApprovedNextStepPreparationBridgeError,
    route_approved_next_step_preparation_bridge_reentry,
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
    "result_type",
    "workflow_definition",
    "decision_contract",
    "completion_contract",
    "failure_contract",
    "approval_contract",
    "employee_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "preparation_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase53Function = Callable[
    [object, object, object, object, object, object],
    PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())
_ERROR_MESSAGE = "approved next-step preparation phase bridge inputs are incompatible"


@dataclass(frozen=True)
class ApprovedNextStepPreparationPhaseBridgeFailureDetail:
    classification: Classification


class ApprovedNextStepPreparationPhaseBridgeError(ValueError):
    """Raised when Phase 60 cannot safely route its supplied result."""


class ApprovedNextStepPreparationPhaseBridgeCompatibilityError(
    ApprovedNextStepPreparationPhaseBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ApprovedNextStepPreparationPhaseBridgeFailureDetail(
            classification
        )


def route_approved_next_step_preparation_phase_bridge_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    approval: object | None,
    employee: object | None,
    *,
    phase53_function: Phase53Function = (
        route_approved_next_step_preparation_bridge_reentry
    ),
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact Phase 59 result through Phase 53 or stop unchanged."""
    _validate_inputs(result, workflow, state_path, events_path, phase53_function)
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
    if (
        type(result) is WorkflowProgressionDecision
        and result.decision == "workflow_complete"
    ):
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is WorkflowProgressionDecision
    assert type(approval) is NextStepPreparationApproval
    assert type(employee) is EmployeeDefinition
    _validate_terminal(result, workflow, state_path, events_path, "succeeded")
    try:
        prepared = phase53_function(
            workflow, state_path, events_path, result, approval, employee
        )
    except ApprovedNextStepPreparationBridgeError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "preparation_contract")
        _validate_prepared(prepared, result, workflow, employee)
    except ApprovedNextStepPreparationPhaseBridgeCompatibilityError:
        _restore_changed(state_path, events_path, original)
        raise
    return prepared


def _validate_inputs(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
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


def _validate_prepare(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    approval: object | None,
    employee: object | None,
) -> None:
    if type(approval) is not NextStepPreparationApproval:
        _raise("approval_contract")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if not (
        _exact_str(value.decision, "prepare_next_step")
        and _exact_str(value.workflow_id, workflow.id)
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index < len(workflow.steps)
        and _exact_str(
            value.current_step_id, workflow.steps[value.current_step_index - 1].id
        )
        and _exact_str(
            value.current_employee_id,
            workflow.steps[value.current_step_index - 1].employee,
        )
        and type(value.next_step_index) is int
        and value.next_step_index == value.current_step_index + 1
        and _exact_str(value.next_step_id, workflow.steps[value.current_step_index].id)
        and _exact_str(
            value.next_employee_id, workflow.steps[value.current_step_index].employee
        )
        and _exact_str(value.reason, "next_step_available")
    ):
        _raise("decision_contract")
    assert type(approval) is NextStepPreparationApproval
    if not (
        approval.approved is True
        and type(approval.current_step_index) is int
        and type(approval.next_step_index) is int
        and (
            approval.workflow_id,
            approval.current_step_id,
            approval.current_step_index,
            approval.next_step_id,
            approval.next_step_index,
            approval.next_employee_id,
        )
        == (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.next_step_id,
            value.next_step_index,
            value.next_employee_id,
        )
    ):
        _raise("approval_contract")
    if employee.id != value.next_employee_id:
        _raise("employee_contract")


def _validate_completion(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    approval: object | None,
    employee: object | None,
) -> None:
    final = workflow.steps[-1]
    if not (
        approval is None
        and employee is None
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
    approval: object | None,
    employee: object | None,
) -> None:
    if not (
        approval is None
        and employee is None
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


def _validate_prepared(
    value: object,
    decision: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    next_step = workflow.steps[decision.current_step_index]
    if type(value) is not PreparedWorkflowStep or not (
        value.workflow_id == workflow.id
        and value.step_id == next_step.id
        and value.step_index == decision.next_step_index
        and value.employee_id == employee.id
        and value.employee_instructions == employee.instructions
        and value.step_instructions == next_step.instructions
        and value.model == employee.model
        and value.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _raise("preparation_contract")


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


def _raise(classification: Classification) -> None:
    raise ApprovedNextStepPreparationPhaseBridgeCompatibilityError(
        classification
    ) from None
