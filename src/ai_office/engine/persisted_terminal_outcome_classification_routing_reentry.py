"""Route one exact Phase 43 result to the read-only Phase 37 classifier."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeError,
    classify_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
)
from ai_office.storage.workflow_execution_history import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
)

RoutingClassification = Literal[
    "result_type",
    "workflow_definition",
    "completion_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "persistence_contract",
    "terminal_contract",
    "classification_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "persisted terminal outcome routing inputs are incompatible"
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
ClassificationFunction = Callable[..., PersistedExecutionOutcome]


@dataclass(frozen=True)
class PersistedTerminalOutcomeClassificationRoutingFailureDetail:
    classification: RoutingClassification


class PersistedTerminalOutcomeClassificationRoutingError(ValueError):
    """Raised when the Phase 44 routing contract cannot be satisfied."""


class PersistedTerminalOutcomeClassificationRoutingCompatibilityError(
    PersistedTerminalOutcomeClassificationRoutingError
):
    def __init__(self, classification: RoutingClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedTerminalOutcomeClassificationRoutingFailureDetail(
            classification
        )


def route_persisted_terminal_outcome_classification_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    classification_function: ClassificationFunction = (
        classify_persisted_execution_outcome_reentry
    ),
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    """Classify one verified terminal persistence result, or stop completion."""
    _validate_inputs(result, workflow, state_path, events_path, classification_function)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _require_unchanged(state_path, events_path, original)
        return result

    assert type(result) is WorkflowExecutionPersistenceResult
    history = _validate_persistence_result(result, workflow, state_path, events_path)
    try:
        outcome = classification_function(workflow, state_path, events_path)
    except PersistedExecutionOutcomeError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    _require_unchanged(state_path, events_path, original)
    _validate_outcome(outcome, history.state)
    return outcome


def _validate_inputs(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (
        WorkflowExecutionPersistenceResult,
        WorkflowProgressionDecision,
    ):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state, Path):
        _raise("state_target")
    if not isinstance(events, Path):
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("classification_contract")


def _validate_completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        value.decision == "workflow_complete"
        and value.workflow_id == workflow.id
        and value.current_step_id == final.id
        and value.current_step_index == len(workflow.steps)
        and value.current_employee_id == final.employee
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and value.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("persistence_contract")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("persistence_contract")


def _validate_persistence_result(
    value: WorkflowExecutionPersistenceResult,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> LoadedWorkflowExecutionHistory:
    if (
        value.state_path != state
        or value.events_path != events
        or type(value.state_bytes_written) is not int
        or type(value.event_bytes_appended) is not int
        or value.state_bytes_written < 1
        or value.event_bytes_appended < 1
    ):
        _raise("persistence_contract")
    try:
        state_bytes, event_bytes = state.read_bytes(), events.read_bytes()
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("terminal_contract")
    if not history.events or len(state_bytes) != value.state_bytes_written:
        _raise("persistence_contract")
    final_bytes = serialize_runtime_step_event_jsonl(history.events[-1]).encode()
    if value.event_bytes_appended != len(final_bytes) or not event_bytes.endswith(
        final_bytes
    ):
        _raise("persistence_contract")
    _validate_terminal_history(workflow, history)
    return history


def _validate_terminal_history(
    workflow: WorkflowDefinition, history: LoadedWorkflowExecutionHistory
) -> None:
    state, events = history.state, history.events
    if state.status not in {"succeeded", "failed"} or state.workflow_id != workflow.id:
        _raise("terminal_contract")
    if not 1 <= state.current_step_index <= len(workflow.steps):
        _raise("terminal_contract")
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _raise("terminal_contract")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        completed = [positions[item] for item in state.completed_step_ids]
    except KeyError:
        _raise("terminal_contract")
    expected = (
        state.current_step_index
        if state.status == "succeeded"
        else state.current_step_index - 1
    )
    compressed = tuple(
        value
        for index, value in enumerate(completed)
        if index == 0 or completed[index - 1] != value
    )
    if compressed != tuple(range(1, expected + 1)):
        _raise("terminal_contract")
    event = events[-1]
    base = (
        event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
    )
    valid = (
        (
            base
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and state.last_failure_category is None
            and event.failure_category is None
            and event.message is None
        )
        if state.status == "succeeded"
        else (
            base
            and event.event_type == "step_failed"
            and event.next_status == "failed"
            and state.last_failure_category in _FAILURE_CATEGORIES
            and event.failure_category == state.last_failure_category
            and event.response_id is None
            and event.output_text is None
            and isinstance(event.message, str)
        )
    )
    if not valid:
        _raise("terminal_contract")


def _validate_outcome(value: object, state: WorkflowExecutionState) -> None:
    success = state.status == "succeeded"
    valid = (
        type(value) is PersistedExecutionOutcome
        and value.workflow_id == state.workflow_id
        and value.current_step_id == state.current_step_id
        and value.current_step_index == state.current_step_index
        and value.current_employee_id == state.current_employee_id
        and value.outcome == ("persisted_success" if success else "persisted_failure")
        and value.failure_category == state.last_failure_category
        and ((value.failure_category is None) == success)
    )
    if not valid:
        _raise("classification_contract")


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
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_changed(state, events, original)
        _raise("dependency_error")


def _raise(classification: RoutingClassification) -> None:
    raise PersistedTerminalOutcomeClassificationRoutingCompatibilityError(
        classification
    ) from None
