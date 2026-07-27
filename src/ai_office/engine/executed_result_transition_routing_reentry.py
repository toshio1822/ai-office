"""Route one exact Phase 42 result to the executed-result persistence boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.executed_result_transition_reentry import (
    ExecutedResultTransitionReentryError,
    persist_executed_result_transition_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionResult,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
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

RoutingClassification = Literal[
    "result_type",
    "workflow_definition",
    "completion_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "result_contract",
    "persistence_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "executed-result transition routing inputs are incompatible"
TransitionReentryFunction = Callable[..., WorkflowExecutionPersistenceResult]


@dataclass(frozen=True)
class ExecutedResultTransitionRoutingFailureDetail:
    classification: RoutingClassification


class ExecutedResultTransitionRoutingError(ValueError):
    """Raised when the Phase 43 routing contract cannot be satisfied."""


class ExecutedResultTransitionRoutingCompatibilityError(
    ExecutedResultTransitionRoutingError
):
    def __init__(self, classification: RoutingClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ExecutedResultTransitionRoutingFailureDetail(classification)


def route_executed_result_transition_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    transition_reentry_function: TransitionReentryFunction = (
        persist_executed_result_transition_reentry
    ),
) -> WorkflowExecutionPersistenceResult | WorkflowProgressionDecision:
    """Persist one verified runtime result, or stop an exact completion decision."""
    _validate_inputs(
        result, workflow, state_path, events_path, transition_reentry_function
    )
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _require_unchanged(state_path, events_path, original)
        return result

    assert type(result) in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
    state = _load_running_state(state_path)
    _validate_workflow_state(workflow, state)
    history = _load_running_history(state_path, events_path)
    _validate_running_history(workflow, state, history)
    if not is_valid_step_runtime_execution_result(
        result,
        workflow_id=state.workflow_id,
        step_id=state.current_step_id,
        step_index=state.current_step_index,
        employee_id=state.current_employee_id,
    ):
        _raise("result_contract")
    try:
        persisted = transition_reentry_function(
            result, workflow, state_path, events_path
        )
    except ExecutedResultTransitionReentryError:
        _restore_if_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _validate_persistence(
            persisted, result, state, state_path, events_path, original[1]
        )
    except ExecutedResultTransitionRoutingCompatibilityError:
        _restore_if_changed(state_path, events_path, original)
        raise
    return persisted


def _validate_inputs(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
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
        _raise("persistence_contract")


def _validate_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("persistence_contract")


def _validate_completion(
    decision: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        decision.decision == "workflow_complete"
        and decision.workflow_id == workflow.id
        and decision.current_step_id == final.id
        and decision.current_step_index == len(workflow.steps)
        and decision.current_employee_id == final.employee
        and decision.next_step_id is None
        and decision.next_step_index is None
        and decision.next_employee_id is None
        and decision.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _load_running_state(path: Path) -> WorkflowExecutionState:
    try:
        state = load_workflow_execution_state(path)
    except (WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _raise("result_contract")
    if state.status != "running" or state.last_failure_category is not None:
        _raise("result_contract")
    return state


def _load_running_history(state: Path, events: Path) -> LoadedWorkflowExecutionHistory:
    try:
        return load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
    except (
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("result_contract")


def _validate_workflow_state(
    workflow: WorkflowDefinition, state: WorkflowExecutionState
) -> None:
    if state.workflow_id != workflow.id or not 1 <= state.current_step_index <= len(
        workflow.steps
    ):
        _raise("result_contract")
    step = workflow.steps[state.current_step_index - 1]
    if step.id != state.current_step_id or step.employee != state.current_employee_id:
        _raise("result_contract")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        completed = [positions[item] for item in state.completed_step_ids]
    except KeyError:
        _raise("result_contract")
    if any(index >= state.current_step_index for index in completed):
        _raise("result_contract")
    compressed = tuple(
        index
        for position, index in enumerate(completed)
        if position == 0 or completed[position - 1] != index
    )
    if compressed != tuple(range(1, state.current_step_index)):
        _raise("result_contract")


def _validate_running_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    history: LoadedWorkflowExecutionHistory,
) -> None:
    if history.state != state:
        _raise("result_contract")
    if not history.events:
        if state.completed_step_ids or state.current_step_index != 1:
            _raise("result_contract")
        return
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    succeeded: list[str] = []
    for event in history.events:
        if not (
            event.workflow_id == state.workflow_id
            and event.step_id in positions
            and event.step_index == positions[event.step_id]
            and event.employee_id == workflow.steps[event.step_index - 1].employee
            and event.step_index < state.current_step_index
            and event.event_type == "step_succeeded"
        ):
            _raise("result_contract")
        succeeded.append(event.step_id)
    if tuple(succeeded) != state.completed_step_ids:
        _raise("result_contract")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("persistence_contract")


def _validate_persistence(
    value: object,
    result: StepRuntimeExecutionResult,
    original: WorkflowExecutionState,
    state: Path,
    events: Path,
    original_events: bytes,
) -> None:
    if type(value) is not WorkflowExecutionPersistenceResult:
        _raise("persistence_contract")
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
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
        state_bytes, event_bytes = state.read_bytes(), events.read_bytes()
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("persistence_contract")
    if (
        len(state_bytes) != value.state_bytes_written
        or not event_bytes.startswith(original_events)
        or len(event_bytes) - len(original_events) != value.event_bytes_appended
        or len(history.events)
        != (0 if not original_events else original_events.count(b"\n")) + 1
    ):
        _raise("persistence_contract")
    _validate_final(history.state, history.events[-1], result, original)


def _validate_final(
    state: WorkflowExecutionState,
    event: RuntimeStepEvent,
    result: StepRuntimeExecutionResult,
    original: WorkflowExecutionState,
) -> None:
    success = type(result) is StepRuntimeExecutionSuccess
    invocation = result.invocation_result
    base = (
        state.workflow_id == original.workflow_id
        and state.current_step_id == original.current_step_id
        and state.current_step_index == original.current_step_index
        and state.current_employee_id == original.current_employee_id
        and state.status == ("succeeded" if success else "failed")
        and event.event_type == ("step_succeeded" if success else "step_failed")
        and event.workflow_id == result.workflow_id
        and event.step_id == result.step_id
        and event.step_index == result.step_index
        and event.employee_id == result.employee_id
        and event.previous_status == "running"
        and event.next_status == state.status
        and event.provider == invocation.provider
        and event.request_id == invocation.request_id
    )
    valid = base and (
        (
            state.completed_step_ids
            == original.completed_step_ids + (original.current_step_id,)
            and state.last_failure_category is None
            and event.failure_category is None
            and event.response_id == invocation.response_id
            and event.output_text == invocation.text
            and event.message is None
        )
        if success
        else (
            state.completed_step_ids == original.completed_step_ids
            and state.last_failure_category == invocation.category
            and event.failure_category == invocation.category
            and event.response_id is None
            and event.output_text is None
            and event.message == invocation.message
        )
    )
    if not valid:
        _raise("persistence_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
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
        _raise("persistence_contract")


def _raise(classification: RoutingClassification) -> None:
    raise ExecutedResultTransitionRoutingCompatibilityError(classification) from None
