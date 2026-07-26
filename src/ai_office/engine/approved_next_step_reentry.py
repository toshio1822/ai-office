"""Read-only reentry from a persisted success to one approved next step."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
    prepare_approved_next_workflow_step,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.storage.workflow_execution_history import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceTargets,
)

ApprovedNextStepReentryClassification = Literal[
    "workflow_definition",
    "decision_type",
    "decision_identity",
    "approval_contract",
    "state_target",
    "event_target",
    "history_data",
    "history_identity",
    "workflow_identity",
    "preparation_contract",
]
_ERROR_MESSAGE = "approved next-step reentry inputs are incompatible"
PreparationFunction = Callable[
    [
        WorkflowDefinition,
        LoadedWorkflowExecutionHistory,
        WorkflowProgressionDecision,
        NextStepPreparationApproval,
        EmployeeDefinition,
    ],
    PreparedWorkflowStep,
]


@dataclass(frozen=True)
class ApprovedNextStepReentryFailureDetail:
    """Safe classification for an incompatible Phase 32 input or result."""

    classification: ApprovedNextStepReentryClassification


class ApprovedNextStepReentryError(ValueError):
    """Raised when Phase 32 cannot safely reenter Phase 26."""


class ApprovedNextStepReentryCompatibilityError(ApprovedNextStepReentryError):
    """Raised before or after the single Phase 26 preparation call."""

    def __init__(self, classification: ApprovedNextStepReentryClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ApprovedNextStepReentryFailureDetail(classification)


def prepare_approved_next_step_reentry(
    workflow: object,
    state_path: object,
    events_path: object,
    decision: object,
    approval: object,
    employee: object,
    *,
    preparation_function: PreparationFunction = prepare_approved_next_workflow_step,
) -> PreparedWorkflowStep:
    """Reload one persisted success and delegate exactly once to Phase 26."""
    _validate_explicit_inputs(
        workflow,
        state_path,
        events_path,
        decision,
        approval,
        employee,
        preparation_function,
    )
    assert isinstance(workflow, WorkflowDefinition)
    assert isinstance(state_path, Path)
    assert isinstance(events_path, Path)
    assert isinstance(decision, WorkflowProgressionDecision)
    assert isinstance(approval, NextStepPreparationApproval)
    assert isinstance(employee, EmployeeDefinition)
    history = _load_history(state_path, events_path)
    _validate_persisted_success(workflow, history)
    _validate_decision(workflow, history, decision)
    _validate_approval(decision, approval)
    prepared = preparation_function(workflow, history, decision, approval, employee)
    _validate_prepared_step(prepared, workflow, decision)
    return prepared


def _validate_explicit_inputs(
    workflow: object,
    state_path: object,
    events_path: object,
    decision: object,
    approval: object,
    employee: object,
    preparation_function: object,
) -> None:
    if not isinstance(workflow, WorkflowDefinition):
        _raise("workflow_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if not isinstance(decision, WorkflowProgressionDecision):
        _raise("decision_type")
    if not isinstance(approval, NextStepPreparationApproval):
        _raise("approval_contract")
    if not isinstance(employee, EmployeeDefinition) or not callable(
        preparation_function
    ):
        _raise("preparation_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("history_data")


def _load_history(
    state_path: Path, events_path: Path
) -> LoadedWorkflowExecutionHistory:
    try:
        return load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("history_data")


def _validate_persisted_success(
    workflow: WorkflowDefinition, history: LoadedWorkflowExecutionHistory
) -> None:
    state = history.state
    if (
        state.status != "succeeded"
        or state.last_failure_category is not None
        or not history.events
    ):
        _raise("history_identity")
    event = history.events[-1]
    if not (
        event.event_type == "step_succeeded"
        and event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
        and event.next_status == "succeeded"
        and event.failure_category is None
        and event.message is None
        and state.completed_step_ids
        and state.completed_step_ids[-1] == state.current_step_id
    ):
        _raise("history_identity")
    if workflow.id != state.workflow_id:
        _raise("workflow_identity")
    if not 1 <= state.current_step_index <= len(workflow.steps):
        _raise("workflow_identity")
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _raise("workflow_identity")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        completed = [positions[step_id] for step_id in state.completed_step_ids]
    except KeyError:
        _raise("workflow_identity")
    compressed = tuple(
        position
        for index, position in enumerate(completed)
        if index == 0 or completed[index - 1] != position
    )
    if compressed != tuple(range(1, state.current_step_index + 1)):
        _raise("workflow_identity")


def _validate_decision(
    workflow: WorkflowDefinition,
    history: LoadedWorkflowExecutionHistory,
    decision: WorkflowProgressionDecision,
) -> None:
    if decision.decision != "prepare_next_step":
        _raise("decision_type")
    state = history.state
    if state.current_step_index == len(workflow.steps):
        _raise("decision_identity")
    next_step = workflow.steps[state.current_step_index]
    valid = (
        _valid_decision_values(decision)
        and
        decision.workflow_id == state.workflow_id
        and decision.current_step_id == state.current_step_id
        and decision.current_step_index == state.current_step_index
        and decision.current_employee_id == state.current_employee_id
        and decision.next_step_id == next_step.id
        and decision.next_step_index == state.current_step_index + 1
        and decision.next_employee_id == next_step.employee
        and decision.reason == "next_step_available"
    )
    if not valid:
        _raise("decision_identity")


def _valid_decision_values(decision: WorkflowProgressionDecision) -> bool:
    return all(
        isinstance(value, str) and bool(value)
        for value in (
            decision.workflow_id,
            decision.current_step_id,
            decision.current_employee_id,
            decision.next_step_id,
            decision.next_employee_id,
            decision.reason,
        )
    ) and all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (decision.current_step_index, decision.next_step_index)
    )


def _validate_approval(
    decision: WorkflowProgressionDecision,
    approval: NextStepPreparationApproval,
) -> None:
    valid = approval.approved is True and _valid_approval_values(approval) and (
        approval.workflow_id == decision.workflow_id
        and approval.current_step_id == decision.current_step_id
        and approval.current_step_index == decision.current_step_index
        and approval.next_step_id == decision.next_step_id
        and approval.next_step_index == decision.next_step_index
        and approval.next_employee_id == decision.next_employee_id
    )
    if not valid:
        _raise("approval_contract")


def _valid_approval_values(approval: NextStepPreparationApproval) -> bool:
    return all(
        isinstance(value, str) and bool(value)
        for value in (
            approval.workflow_id,
            approval.current_step_id,
            approval.next_step_id,
            approval.next_employee_id,
        )
    ) and all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (approval.current_step_index, approval.next_step_index)
    )


def _validate_prepared_step(
    prepared: object,
    workflow: WorkflowDefinition,
    decision: WorkflowProgressionDecision,
) -> None:
    if not isinstance(prepared, PreparedWorkflowStep):
        _raise("preparation_contract")
    valid = (
        _valid_prepared_values(prepared)
        and
        prepared.workflow_id == workflow.id
        and prepared.step_id == decision.next_step_id
        and prepared.step_index == decision.next_step_index
        and prepared.employee_id == decision.next_employee_id
    )
    if not valid:
        _raise("preparation_contract")


def _valid_prepared_values(prepared: PreparedWorkflowStep) -> bool:
    return all(
        isinstance(value, str) and bool(value)
        for value in (
            prepared.workflow_id,
            prepared.step_id,
            prepared.employee_id,
            prepared.employee_instructions,
            prepared.step_instructions,
            prepared.model,
        )
    ) and (
        isinstance(prepared.step_index, int)
        and not isinstance(prepared.step_index, bool)
        and prepared.step_index > 0
        and isinstance(prepared.allowed_tool_names, tuple)
        and all(
            isinstance(tool, str) and bool(tool) for tool in prepared.allowed_tool_names
        )
    )


def _raise(classification: ApprovedNextStepReentryClassification) -> None:
    raise ApprovedNextStepReentryCompatibilityError(classification) from None
