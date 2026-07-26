"""Guard one Phase 30 persistence call for one persisted running step result."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionResult,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.runtime.executed_step_transition_persistence import (
    persist_executed_step_transition,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
)
from ai_office.storage.workflow_execution_history import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
)

ExecutedResultTransitionReentryClassification = Literal[
    "workflow_definition",
    "result_type",
    "result_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "state_data",
    "state_identity",
    "workflow_identity",
    "persistence_contract",
    "persistence_rollback",
]
_ERROR_MESSAGE = "executed-result transition persistence inputs are incompatible"
PersistenceFunction = Callable[
    [object, object, object], WorkflowExecutionPersistenceResult
]


@dataclass(frozen=True)
class ExecutedResultTransitionReentryFailureDetail:
    classification: ExecutedResultTransitionReentryClassification


class ExecutedResultTransitionReentryError(ValueError):
    """Raised when the Phase 36 reentry contract cannot be satisfied."""


class ExecutedResultTransitionReentryCompatibilityError(
    ExecutedResultTransitionReentryError
):
    def __init__(
        self, classification: ExecutedResultTransitionReentryClassification
    ) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ExecutedResultTransitionReentryFailureDetail(classification)


def persist_executed_result_transition_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    persistence_function: PersistenceFunction = persist_executed_step_transition,
) -> WorkflowExecutionPersistenceResult:
    """Validate a persisted running step, then delegate to Phase 30 exactly once."""
    _validate_inputs(result, workflow, state_path, events_path, persistence_function)
    assert type(result) in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path)
    assert isinstance(events_path, Path)

    state = _load_running_state(state_path)
    _validate_workflow_state(workflow, state)
    history = _load_running_history(state_path, events_path)
    _validate_running_history(workflow, state, history)
    _validate_result(result, state)
    original_state, original_events = _capture_targets(state_path, events_path)

    try:
        persisted = persistence_function(result, state_path, events_path)
    except Exception as error:
        _restore_if_changed(state_path, events_path, original_state, original_events)
        raise error
    try:
        _validate_persistence(
            persisted,
            result,
            state,
            state_path,
            events_path,
            original_events,
        )
    except ExecutedResultTransitionReentryCompatibilityError:
        _restore_if_changed(state_path, events_path, original_state, original_events)
        raise
    return persisted


def _validate_inputs(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    persistence_function: object,
) -> None:
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(result) not in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure):
        _raise("result_type")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(persistence_function):
        _raise("persistence_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("event_target")


def _load_running_state(state_path: Path) -> WorkflowExecutionState:
    try:
        state = load_workflow_execution_state(state_path)
    except WorkflowExecutionDataError:
        _raise("state_data")
    except WorkflowExecutionLoadError:
        _raise("state_target")
    if state.status != "running" or state.last_failure_category is not None:
        _raise("state_identity")
    return state


def _load_running_history(
    state_path: Path, events_path: Path
) -> LoadedWorkflowExecutionHistory:
    try:
        return load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (WorkflowExecutionDataError, WorkflowExecutionHistoryInconsistencyError):
        _raise("state_data")
    except WorkflowExecutionLoadError:
        _raise("event_target")


def _validate_workflow_state(
    workflow: WorkflowDefinition, state: WorkflowExecutionState
) -> None:
    if workflow.id != state.workflow_id or not 1 <= state.current_step_index <= len(
        workflow.steps
    ):
        _raise("workflow_identity")
    step = workflow.steps[state.current_step_index - 1]
    if step.id != state.current_step_id or step.employee != state.current_employee_id:
        _raise("workflow_identity")
    positions = {item.id: index for index, item in enumerate(workflow.steps, 1)}
    try:
        completed = [positions[item] for item in state.completed_step_ids]
    except KeyError:
        _raise("workflow_identity")
    if any(index >= state.current_step_index for index in completed):
        _raise("workflow_identity")
    compressed = tuple(
        index
        for position, index in enumerate(completed)
        if position == 0 or completed[position - 1] != index
    )
    if compressed != tuple(range(1, state.current_step_index)):
        _raise("workflow_identity")


def _validate_running_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    history: LoadedWorkflowExecutionHistory,
) -> None:
    """Bind Phase 24's strict history result to the supplied running identity."""
    if history.state != state:
        _raise("state_identity")
    if not history.events:
        if state.completed_step_ids or state.current_step_index != 1:
            _raise("state_identity")
        return
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    succeeded_ids: list[str] = []
    for event in history.events:
        if (
            event.workflow_id != state.workflow_id
            or event.step_id not in positions
            or event.step_index != positions[event.step_id]
            or event.employee_id != workflow.steps[event.step_index - 1].employee
            or event.step_index >= state.current_step_index
            or event.step_id == state.current_step_id
            or event.event_type != "step_succeeded"
        ):
            _raise("workflow_identity")
        succeeded_ids.append(event.step_id)
    if tuple(succeeded_ids) != state.completed_step_ids:
        _raise("state_identity")


def _validate_result(
    result: StepRuntimeExecutionResult, state: WorkflowExecutionState
) -> None:
    if not is_valid_step_runtime_execution_result(
        result,
        workflow_id=state.workflow_id,
        step_id=state.current_step_id,
        step_index=state.current_step_index,
        employee_id=state.current_employee_id,
    ):
        _raise("result_contract")


def _capture_targets(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        return state_path.read_bytes(), events_path.read_bytes()
    except OSError:
        _raise("persistence_contract")


def _validate_persistence(
    persisted: object,
    result: StepRuntimeExecutionResult,
    original_state: WorkflowExecutionState,
    state_path: Path,
    events_path: Path,
    original_event_bytes: bytes,
) -> None:
    if type(persisted) is not WorkflowExecutionPersistenceResult:
        _raise("persistence_contract")
    if (
        persisted.state_path != state_path
        or persisted.events_path != events_path
        or type(persisted.state_bytes_written) is not int
        or type(persisted.event_bytes_appended) is not int
        or persisted.state_bytes_written < 1
        or persisted.event_bytes_appended < 1
    ):
        _raise("persistence_contract")
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
        state_bytes, event_bytes = state_path.read_bytes(), events_path.read_bytes()
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("persistence_contract")
    if (
        len(state_bytes) != persisted.state_bytes_written
        or not event_bytes.startswith(original_event_bytes)
        or len(event_bytes) - len(original_event_bytes)
        != persisted.event_bytes_appended
        or len(history.events) != _event_record_count(original_event_bytes) + 1
    ):
        _raise("persistence_contract")
    _validate_final_contract(history.state, history.events[-1], result, original_state)


def _validate_final_contract(
    state: WorkflowExecutionState,
    event: RuntimeStepEvent,
    result: StepRuntimeExecutionResult,
    original: WorkflowExecutionState,
) -> None:
    success = type(result) is StepRuntimeExecutionSuccess
    invocation = result.invocation_result
    expected_status = "succeeded" if success else "failed"
    expected_event = "step_succeeded" if success else "step_failed"
    base = (
        state.workflow_id == original.workflow_id
        and state.current_step_id == original.current_step_id
        and state.current_step_index == original.current_step_index
        and state.current_employee_id == original.current_employee_id
        and state.status == expected_status
        and event.event_type == expected_event
        and event.workflow_id == result.workflow_id
        and event.step_id == result.step_id
        and event.step_index == result.step_index
        and event.employee_id == result.employee_id
        and event.previous_status == "running"
        and event.next_status == expected_status
        and event.provider == invocation.provider
        and event.request_id == invocation.request_id
    )
    if success:
        valid = (
            base
            and state.completed_step_ids
            == original.completed_step_ids + (original.current_step_id,)
            and state.last_failure_category is None
            and event.failure_category is None
            and event.response_id == invocation.response_id
            and event.output_text == invocation.text
            and event.message is None
        )
    else:
        valid = (
            base
            and state.completed_step_ids == original.completed_step_ids
            and state.last_failure_category == invocation.category
            and event.failure_category == invocation.category
            and event.response_id is None
            and event.output_text is None
            and event.message == invocation.message
        )
    if not valid:
        _raise("persistence_contract")


def _event_record_count(contents: bytes) -> int:
    """Count already validated JSONL records without parsing a second time."""
    return 0 if not contents else contents.count(b"\n")


def _restore_if_changed(
    state_path: Path,
    events_path: Path,
    original_state: bytes,
    original_events: bytes,
) -> None:
    try:
        changed = (
            not state_path.is_file()
            or not events_path.is_file()
            or state_path.read_bytes() != original_state
            or events_path.read_bytes() != original_events
        )
        if changed:
            state_path.write_bytes(original_state)
            events_path.write_bytes(original_events)
    except OSError:
        _raise("persistence_rollback")


def _raise(classification: ExecutedResultTransitionReentryClassification) -> None:
    raise ExecutedResultTransitionReentryCompatibilityError(classification) from None
