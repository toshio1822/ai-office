"""Read-only routing from a persisted success decision to Phase 32."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.approved_next_step_reentry import (
    ApprovedNextStepReentryError,
    prepare_approved_next_step_reentry,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_success_progression import (
    PersistedSuccessProgressionError,
    decide_persisted_success_progression,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision

RoutingClassification = Literal[
    "decision_type",
    "decision_contract",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "approval_contract",
    "employee_contract",
    "progression_contract",
    "preparation_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "persisted success preparation routing inputs are incompatible"
ProgressionFunction = Callable[[object, object, object], WorkflowProgressionDecision]
PreparationFunction = Callable[
    [object, object, object, object, object, object], PreparedWorkflowStep
]


@dataclass(frozen=True)
class PersistedSuccessPreparationRoutingFailureDetail:
    classification: RoutingClassification


class PersistedSuccessPreparationRoutingError(ValueError):
    pass


class PersistedSuccessPreparationRoutingCompatibilityError(
    PersistedSuccessPreparationRoutingError
):
    def __init__(self, classification: RoutingClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedSuccessPreparationRoutingFailureDetail(classification)


def route_persisted_success_progression_reentry(
    decision: object,
    workflow: object,
    state_path: object,
    events_path: object,
    approval: object | None,
    employee: object | None,
    *,
    progression_function: ProgressionFunction = decide_persisted_success_progression,
    preparation_function: PreparationFunction = prepare_approved_next_step_reentry,
) -> PreparedWorkflowStep | WorkflowProgressionDecision:
    """Re-decide once, routing preparation but never completion finalization."""
    _validate_inputs(
        decision,
        workflow,
        state_path,
        events_path,
        approval,
        employee,
        progression_function,
        preparation_function,
    )
    assert (
        type(decision) is WorkflowProgressionDecision
        and type(workflow) is WorkflowDefinition
    )
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    original = _capture(state_path, events_path)
    decided = _call(
        progression_function,
        workflow,
        state_path,
        events_path,
        original,
        PersistedSuccessProgressionError,
    )
    _validate_decision(decided, workflow, "progression_contract")
    if decided != decision:
        _raise("progression_contract")
    if decision.decision == "workflow_complete":
        return decision
    prepared = _call_preparation(
        preparation_function,
        workflow,
        state_path,
        events_path,
        decision,
        approval,
        employee,
        original,
    )
    _validate_prepared(prepared, workflow, decision, employee)
    return prepared


def _validate_inputs(
    decision: object,
    workflow: object,
    state_path: object,
    events_path: object,
    approval: object | None,
    employee: object | None,
    progression_function: object,
    preparation_function: object,
) -> None:
    if type(decision) is not WorkflowProgressionDecision:
        _raise("decision_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    _validate_decision(decision, workflow, "decision_contract")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(progression_function):
        _raise("progression_contract")
    if not callable(preparation_function):
        _raise("preparation_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("dependency_error")
    if decision.decision == "prepare_next_step":
        if type(approval) is not NextStepPreparationApproval:
            _raise("approval_contract")
        if type(employee) is not EmployeeDefinition:
            _raise("employee_contract")
        if not _valid_approval(approval, decision):
            _raise("approval_contract")
        if employee.id != decision.next_employee_id:
            _raise("employee_contract")
    elif approval is not None or employee is not None:
        _raise("decision_contract")


def _validate_decision(
    value: object, workflow: WorkflowDefinition, classification: RoutingClassification
) -> None:
    if type(value) is not WorkflowProgressionDecision:
        _raise(classification)
    if value.workflow_id != workflow.id or not 1 <= value.current_step_index <= len(
        workflow.steps
    ):
        _raise(classification)
    current = workflow.steps[value.current_step_index - 1]
    if (
        value.current_step_id != current.id
        or value.current_employee_id != current.employee
    ):
        _raise(classification)
    if value.decision == "prepare_next_step":
        if value.current_step_index == len(workflow.steps):
            _raise(classification)
        next_step = workflow.steps[value.current_step_index]
        valid = (
            value.next_step_id == next_step.id
            and value.next_step_index == value.current_step_index + 1
            and value.next_employee_id == next_step.employee
            and value.reason == "next_step_available"
        )
    elif value.decision == "workflow_complete":
        valid = (
            value.current_step_index == len(workflow.steps)
            and value.next_step_id is None
            and value.next_step_index is None
            and value.next_employee_id is None
            and value.reason == "last_step_succeeded"
        )
    else:
        valid = False
    if not valid:
        _raise(classification)


def _valid_approval(
    approval: NextStepPreparationApproval, decision: WorkflowProgressionDecision
) -> bool:
    return approval.approved is True and (
        approval.workflow_id,
        approval.current_step_id,
        approval.current_step_index,
        approval.next_step_id,
        approval.next_step_index,
        approval.next_employee_id,
    ) == (
        decision.workflow_id,
        decision.current_step_id,
        decision.current_step_index,
        decision.next_step_id,
        decision.next_step_index,
        decision.next_employee_id,
    )


def _capture(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        return state_path.read_bytes(), events_path.read_bytes()
    except OSError:
        _raise("dependency_error")


def _call(
    function: ProgressionFunction,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    safe_type: type[Exception],
) -> object:
    try:
        result = function(workflow, state_path, events_path)
    except safe_type:
        _restore(state_path, events_path, original)
        raise
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    _reject_changed(state_path, events_path, original)
    return result


def _call_preparation(
    function: PreparationFunction,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    decision: WorkflowProgressionDecision,
    approval: object | None,
    employee: object | None,
    original: tuple[bytes, bytes],
) -> object:
    try:
        result = function(
            workflow, state_path, events_path, decision, approval, employee
        )
    except ApprovedNextStepReentryError:
        _restore(state_path, events_path, original)
        raise
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    _reject_changed(state_path, events_path, original)
    return result


def _restore(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        for path, value in ((state_path, original[0]), (events_path, original[1])):
            try:
                changed = path.read_bytes() != value
            except FileNotFoundError:
                changed = True
            if changed:
                path.write_bytes(value)
    except OSError:
        _raise("dependency_rollback")


def _reject_changed(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        changed = (
            state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except OSError:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    if changed:
        _restore(state_path, events_path, original)
        _raise("dependency_error")


def _validate_prepared(
    value: object,
    workflow: WorkflowDefinition,
    decision: WorkflowProgressionDecision,
    employee: object | None,
) -> None:
    if (
        type(value) is not PreparedWorkflowStep
        or type(employee) is not EmployeeDefinition
    ):
        _raise("preparation_contract")
    step = workflow.steps[decision.next_step_index - 1]  # type: ignore[operator]
    if not (
        value.workflow_id == workflow.id
        and value.step_id == step.id
        and value.step_index == decision.next_step_index
        and value.employee_id == employee.id == step.employee
        and value.employee_instructions == employee.instructions
        and value.step_instructions == step.instructions
        and value.model == employee.model
        and value.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _raise("preparation_contract")


def _raise(classification: RoutingClassification) -> None:
    raise PersistedSuccessPreparationRoutingCompatibilityError(classification) from None
