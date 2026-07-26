"""Read-only classification for one persisted Phase 36 execution outcome."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage.workflow_execution_history import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionLoadError,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceTargets,
)

PersistedExecutionOutcomeClassification = Literal[
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "history_data",
    "state_status",
    "state_identity",
    "workflow_identity",
    "event_history",
    "classification_contract",
    "history_rollback",
]
PersistedExecutionOutcomeType = Literal["persisted_success", "persisted_failure"]
_ERROR_MESSAGE = "persisted execution outcome inputs are incompatible"
HistoryLoader = Callable[
    [WorkflowExecutionPersistenceTargets], LoadedWorkflowExecutionHistory
]


@dataclass(frozen=True)
class PersistedExecutionOutcome:
    """Minimal immutable classification of one persisted terminal outcome."""

    outcome: PersistedExecutionOutcomeType
    workflow_id: str
    current_step_id: str
    current_step_index: int
    current_employee_id: str
    failure_category: ModelInvocationFailureCategory | None


@dataclass(frozen=True)
class PersistedExecutionOutcomeFailureDetail:
    """Safe category for a Phase 37 compatibility rejection."""

    classification: PersistedExecutionOutcomeClassification


class PersistedExecutionOutcomeError(ValueError):
    """Raised when a persisted terminal outcome cannot be classified safely."""


class PersistedExecutionOutcomeCompatibilityError(PersistedExecutionOutcomeError):
    """Raised for a safe incompatibility in the Phase 37 boundary."""

    def __init__(self, classification: PersistedExecutionOutcomeClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedExecutionOutcomeFailureDetail(classification)


def classify_persisted_execution_outcome_reentry(
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    history_loader: HistoryLoader = load_workflow_execution_history,
) -> PersistedExecutionOutcome:
    """Classify one persisted Phase 36 outcome without progressing."""
    _validate_inputs(workflow, state_path, events_path, history_loader)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path)
    assert isinstance(events_path, Path)

    original = _capture_targets(state_path, events_path)
    try:
        history = history_loader(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except WorkflowExecutionLoadError:
        _restore_if_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("history_data")
    _reject_changed_targets(state_path, events_path, original)

    _validate_history_type(history)
    _validate_history_contents(history)
    _validate_terminal_state(history.state)
    _validate_workflow_identity(workflow, history.state)
    _validate_event_history(workflow, history.state, history.events)
    result = _build_result(history.state)
    _validate_result_contract(result, history.state)
    return result


def _validate_inputs(
    workflow: object,
    state_path: object,
    events_path: object,
    history_loader: object,
) -> None:
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(history_loader):
        _raise("classification_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("history_data")


def _capture_targets(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        return state_path.read_bytes(), events_path.read_bytes()
    except OSError:
        _raise("history_data")


def _restore_if_changed(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        if _target_needs_restore(state_path, original[0]):
            state_path.write_bytes(original[0])
        if _target_needs_restore(events_path, original[1]):
            events_path.write_bytes(original[1])
    except OSError:
        _raise("history_rollback")


def _target_needs_restore(path: Path, original: bytes) -> bool:
    try:
        return path.read_bytes() != original
    except FileNotFoundError:
        return True


def _reject_changed_targets(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        changed = (
            state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except OSError:
        _restore_if_changed(state_path, events_path, original)
        _raise("history_data")
    if changed:
        _restore_if_changed(state_path, events_path, original)
        _raise("history_data")


def _validate_history_type(history: object) -> None:
    if type(history) is not LoadedWorkflowExecutionHistory:
        _raise("history_data")


def _validate_history_contents(history: LoadedWorkflowExecutionHistory) -> None:
    if (
        type(history.state) is not WorkflowExecutionState
        or type(history.events) is not tuple
        or any(type(event) is not RuntimeStepEvent for event in history.events)
    ):
        _raise("history_data")


def _validate_terminal_state(state: WorkflowExecutionState) -> None:
    if state.status not in {"succeeded", "failed"}:
        _raise("state_status")


def _validate_workflow_identity(
    workflow: WorkflowDefinition, state: WorkflowExecutionState
) -> None:
    if workflow.id != state.workflow_id or not 1 <= state.current_step_index <= len(
        workflow.steps
    ):
        _raise("workflow_identity")
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _raise("workflow_identity")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        completed = tuple(positions[step_id] for step_id in state.completed_step_ids)
    except KeyError:
        _raise("workflow_identity")
    compressed = tuple(
        index
        for position, index in enumerate(completed)
        if position == 0 or completed[position - 1] != index
    )
    final_index = (
        state.current_step_index
        if state.status == "succeeded"
        else state.current_step_index - 1
    )
    if compressed != tuple(range(1, final_index + 1)):
        _raise("workflow_identity")
    if state.status == "succeeded":
        if (
            not state.completed_step_ids
            or state.completed_step_ids[-1] != state.current_step_id
        ):
            _raise("state_identity")
    elif state.current_step_id in state.completed_step_ids:
        _raise("state_identity")


def _validate_event_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> None:
    if not events:
        _raise("event_history")
    terminal, earlier = events[-1], events[:-1]
    prior_completed = (
        state.completed_step_ids[:-1]
        if state.status == "succeeded"
        else state.completed_step_ids
    )
    earlier_ids: list[str] = []
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    for event in earlier:
        if (
            event.workflow_id != state.workflow_id
            or event.step_id not in positions
            or event.step_index != positions[event.step_id]
            or event.employee_id != workflow.steps[event.step_index - 1].employee
            or event.step_index >= state.current_step_index
            or event.step_id == state.current_step_id
            or event.event_type != "step_succeeded"
            or event.previous_status != "running"
            or event.next_status != "succeeded"
        ):
            _raise("event_history")
        earlier_ids.append(event.step_id)
    if tuple(earlier_ids) != prior_completed:
        _raise("event_history")
    valid_terminal_identity = (
        terminal.workflow_id == state.workflow_id
        and terminal.step_id == state.current_step_id
        and terminal.step_index == state.current_step_index
        and terminal.employee_id == state.current_employee_id
        and terminal.previous_status == "running"
    )
    if state.status == "succeeded":
        valid_terminal = (
            valid_terminal_identity
            and terminal.event_type == "step_succeeded"
            and terminal.next_status == "succeeded"
            and state.last_failure_category is None
            and terminal.failure_category is None
            and terminal.message is None
            and isinstance(terminal.response_id, str)
            and isinstance(terminal.output_text, str)
        )
    else:
        valid_terminal = (
            valid_terminal_identity
            and terminal.event_type == "step_failed"
            and terminal.next_status == "failed"
            and terminal.failure_category == state.last_failure_category
            and terminal.failure_category is not None
            and terminal.response_id is None
            and terminal.output_text is None
            and isinstance(terminal.message, str)
        )
    if not valid_terminal:
        _raise("event_history")


def _build_result(state: WorkflowExecutionState) -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        outcome=(
            "persisted_success" if state.status == "succeeded" else "persisted_failure"
        ),
        workflow_id=state.workflow_id,
        current_step_id=state.current_step_id,
        current_step_index=state.current_step_index,
        current_employee_id=state.current_employee_id,
        failure_category=state.last_failure_category,
    )


def _validate_result_contract(result: object, state: WorkflowExecutionState) -> None:
    expected_outcome = (
        "persisted_success" if state.status == "succeeded" else "persisted_failure"
    )
    valid = (
        type(result) is PersistedExecutionOutcome
        and result.outcome == expected_outcome
        and result.workflow_id == state.workflow_id
        and result.current_step_id == state.current_step_id
        and result.current_step_index == state.current_step_index
        and result.current_employee_id == state.current_employee_id
        and result.failure_category == state.last_failure_category
        and (
            (result.outcome == "persisted_success") == (result.failure_category is None)
        )
    )
    if not valid:
        _raise("classification_contract")


def _raise(classification: PersistedExecutionOutcomeClassification) -> None:
    raise PersistedExecutionOutcomeCompatibilityError(classification) from None
