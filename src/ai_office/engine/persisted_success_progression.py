"""Read-only progression decision for one persisted successful step."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.workflow_progression import (
    WorkflowProgressionDecision,
    decide_workflow_progression,
)
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

PersistedSuccessProgressionClassification = Literal[
    "workflow_definition",
    "state_target",
    "event_target",
    "history_data",
    "state_status",
    "state_identity",
    "event_history",
    "workflow_identity",
    "decision_contract",
]
_ERROR_MESSAGE = "persisted-success progression inputs are incompatible"
DecisionFunction = Callable[
    [WorkflowDefinition, LoadedWorkflowExecutionHistory], WorkflowProgressionDecision
]


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


def decide_persisted_success_progression(
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    decision_function: DecisionFunction = decide_workflow_progression,
) -> WorkflowProgressionDecision:
    """Load one persisted success and delegate exactly once to Phase 25."""
    _validate_explicit_inputs(workflow, state_path, events_path, decision_function)
    assert isinstance(workflow, WorkflowDefinition)
    assert isinstance(state_path, Path)
    assert isinstance(events_path, Path)
    history = _load_history(state_path, events_path)
    _validate_persisted_success(history)
    _validate_workflow_identity(workflow, history)
    decision = decision_function(workflow, history)
    _validate_decision_contract(decision, workflow, history)
    return decision


def _validate_explicit_inputs(
    workflow: object, state_path: object, events_path: object, decision_function: object
) -> None:
    if not isinstance(workflow, WorkflowDefinition):
        _raise("workflow_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if not callable(decision_function):
        _raise("decision_contract")
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
    except (WorkflowExecutionDataError, WorkflowExecutionHistoryInconsistencyError):
        _raise("history_data")
    except WorkflowExecutionLoadError:
        _raise("history_data")


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
