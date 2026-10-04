"""Read-only progression decision for one persisted successful step."""

from dataclasses import dataclass
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.workflow_progression import (
    WorkflowProgressionDecision,
    decide_workflow_progression,
)
from ai_office.storage.workflow_execution_history import LoadedWorkflowExecutionHistory

PersistedSuccessProgressionClassification = Literal[
    "workflow_definition",
    "history_data",
    "state_status",
    "state_identity",
    "event_history",
    "workflow_identity",
    "decision_contract",
]
_ERROR_MESSAGE = "persisted-success progression inputs are incompatible"


@dataclass(frozen=True)
class PersistedSuccessProgressionFailureDetail:
    classification: PersistedSuccessProgressionClassification


class PersistedSuccessProgressionError(ValueError):
    """Raised when persisted success cannot safely reach Phase 25."""


class PersistedSuccessProgressionCompatibilityError(PersistedSuccessProgressionError):
    """Raised for a safe incompatibility before the decision delegation."""

    def __init__(
        self, classification: PersistedSuccessProgressionClassification
    ) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedSuccessProgressionFailureDetail(classification)


def _decide_loaded_persisted_success_progression(
    workflow: object, history: object
) -> WorkflowProgressionDecision:
    """Decide from loaded success after canonical persisted routing gated Artifacts."""
    if not isinstance(workflow, WorkflowDefinition):
        _raise("workflow_definition")
    if type(history) is not LoadedWorkflowExecutionHistory:
        _raise("history_data")
    assert isinstance(workflow, WorkflowDefinition)
    assert type(history) is LoadedWorkflowExecutionHistory
    _validate_persisted_success(history)
    _validate_workflow_identity(workflow, history)
    decision = decide_workflow_progression(workflow, history)
    _validate_decision_contract(decision, workflow, history)
    return decision


def _validate_persisted_success(history: LoadedWorkflowExecutionHistory) -> None:
    state = history.state
    if state.status != "succeeded":
        _raise("state_status")
    if state.last_failure_category is not None:
        _raise("state_identity")
    if not history.events:
        _raise("event_history")
    event = history.events[-1]
    valid = (
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
    )
    if not valid:
        _raise("event_history")


def _validate_workflow_identity(
    workflow: WorkflowDefinition, history: LoadedWorkflowExecutionHistory
) -> None:
    state = history.state
    if workflow.id != state.workflow_id:
        _raise("workflow_identity")
    if not 1 <= state.current_step_index <= len(workflow.steps):
        _raise("workflow_identity")
    step = workflow.steps[state.current_step_index - 1]
    if step.id != state.current_step_id or step.employee != state.current_employee_id:
        _raise("workflow_identity")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        ordered = [positions[item] for item in state.completed_step_ids]
    except KeyError:
        _raise("workflow_identity")
    compressed = tuple(
        item
        for index, item in enumerate(ordered)
        if index == 0 or ordered[index - 1] != item
    )
    if compressed != tuple(range(1, state.current_step_index + 1)):
        _raise("workflow_identity")


def _validate_decision_contract(
    decision: object,
    workflow: WorkflowDefinition,
    history: LoadedWorkflowExecutionHistory,
) -> None:
    if not isinstance(decision, WorkflowProgressionDecision):
        _raise("decision_contract")
    state = history.state
    valid = (
        decision.workflow_id == state.workflow_id
        and decision.current_step_id == state.current_step_id
        and decision.current_step_index == state.current_step_index
        and decision.current_employee_id == state.current_employee_id
    )
    if state.current_step_index == len(workflow.steps):
        valid = valid and (
            decision.decision == "workflow_complete"
            and decision.next_step_id is None
            and decision.next_step_index is None
            and decision.next_employee_id is None
            and decision.reason == "last_step_succeeded"
        )
    else:
        next_step = workflow.steps[state.current_step_index]
        valid = valid and (
            decision.decision == "prepare_next_step"
            and decision.next_step_id == next_step.id
            and decision.next_step_index == state.current_step_index + 1
            and decision.next_employee_id == next_step.employee
            and decision.reason == "next_step_available"
        )
    if not valid:
        _raise("decision_contract")


def _raise(classification: PersistedSuccessProgressionClassification) -> None:
    raise PersistedSuccessProgressionCompatibilityError(classification) from None
