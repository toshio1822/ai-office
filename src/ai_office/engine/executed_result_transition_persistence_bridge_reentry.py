"""Bridge one exact Phase 49 result to Phase 43 transition routing."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.executed_result_transition_routing_reentry import (
    ExecutedResultTransitionRoutingError,
    route_executed_result_transition_reentry,
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

Classification = Literal[
    "result_type",
    "workflow_definition",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "runtime_contract",
    "persistence_contract",
    "dependency_error",
    "dependency_rollback",
]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_ERROR_MESSAGE = "executed-result transition persistence bridge inputs are incompatible"
Phase43Function = Callable[
    ..., WorkflowExecutionPersistenceResult | WorkflowProgressionDecision
]


@dataclass(frozen=True)
class ExecutedResultTransitionPersistenceBridgeFailureDetail:
    classification: Classification


class ExecutedResultTransitionPersistenceBridgeError(ValueError):
    """Raised when the Phase 50 bridge cannot safely proceed."""


class ExecutedResultTransitionPersistenceBridgeCompatibilityError(
    ExecutedResultTransitionPersistenceBridgeError
):
    """A public, detail-safe Phase 50 compatibility error."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ExecutedResultTransitionPersistenceBridgeFailureDetail(
            classification
        )


def route_executed_result_transition_persistence_bridge_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    transition_routing_function: Phase43Function = (
        route_executed_result_transition_reentry
    ),
) -> (
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route exactly one Phase 49 result and stop at this boundary."""
    _validate_inputs(
        result, workflow, state_path, events_path, transition_routing_function
    )
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
    elif type(result) is PersistedExecutionOutcome:
        _validate_failure(result, workflow)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original)
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original)
        return result

    assert type(result) in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
    state = _load_running_state(state_path)
    _validate_running_state(workflow, state)
    history = _load_running_history(state_path, events_path)
    _validate_running_history(workflow, state, history)
    if not is_valid_step_runtime_execution_result(
        result,
        workflow_id=state.workflow_id,
        step_id=state.current_step_id,
        step_index=state.current_step_index,
        employee_id=state.current_employee_id,
    ):
        _raise("runtime_contract")
    try:
        persisted = transition_routing_function(
            result, workflow, state_path, events_path
        )
    except ExecutedResultTransitionRoutingError:
        _restore_if_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _validate_persistence(
            persisted, result, state, state_path, events_path, original[1]
        )
    except ExecutedResultTransitionPersistenceBridgeCompatibilityError:
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
        PersistedExecutionOutcome,
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
    result: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        result.decision == "workflow_complete"
        and result.workflow_id == workflow.id
        and result.current_step_id == final.id
        and result.current_step_index == len(workflow.steps)
        and result.current_employee_id == final.employee
        and result.next_step_id
        is result.next_step_index
        is result.next_employee_id
        is None
        and result.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_failure(
    result: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    if not (
        result.outcome == "persisted_failure"
        and result.workflow_id == workflow.id
        and type(result.current_step_index) is int
        and 1 <= result.current_step_index <= len(workflow.steps)
        and result.current_step_id == workflow.steps[result.current_step_index - 1].id
        and result.current_employee_id
        == workflow.steps[result.current_step_index - 1].employee
        and result.failure_category in _FAILURE_CATEGORIES
    ):
        _raise("failure_contract")


def _validate_terminal(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    status: Literal["succeeded", "failed"],
) -> None:
    try:
        state, _ = load_strict_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if not (
        state.status == status
        and (
            state.workflow_id,
            state.current_step_id,
            state.current_step_index,
            state.current_employee_id,
        )
        == (
            result.workflow_id,
            result.current_step_id,
            result.current_step_index,
            result.current_employee_id,
        )
    ):
        _raise("terminal_contract")
    if type(result) is PersistedExecutionOutcome and (
        state.last_failure_category != result.failure_category
    ):
        _raise("terminal_contract")


def _load_running_state(path: Path) -> WorkflowExecutionState:
    try:
        state = load_workflow_execution_state(path)
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _raise("runtime_contract")
    if state.status != "running" or state.last_failure_category is not None:
        _raise("runtime_contract")
    return state


def _validate_running_state(
    workflow: WorkflowDefinition, state: WorkflowExecutionState
) -> None:
    if not 1 <= state.current_step_index <= len(workflow.steps):
        _raise("runtime_contract")
    step = workflow.steps[state.current_step_index - 1]
    prefix = tuple(item.id for item in workflow.steps[: state.current_step_index - 1])
    if not (
        state.workflow_id == workflow.id
        and state.current_step_id == step.id
        and state.current_employee_id == step.employee
        and state.completed_step_ids == prefix
    ):
        _raise("runtime_contract")


def _load_running_history(state: Path, events: Path) -> LoadedWorkflowExecutionHistory:
    try:
        return load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("runtime_contract")


def _validate_running_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    history: LoadedWorkflowExecutionHistory,
) -> None:
    prefix = workflow.steps[: state.current_step_index - 1]
    if history.state != state or len(history.events) != len(prefix):
        _raise("runtime_contract")
    for event, step in zip(history.events, prefix, strict=True):
        if not (
            event.event_type == "step_succeeded"
            and event.workflow_id == workflow.id
            and event.step_id == step.id
            and event.step_index == workflow.steps.index(step) + 1
            and event.employee_id == step.employee
            and event.previous_status == "running"
            and event.next_status == "succeeded"
            and event.failure_category is None
            and type(event.response_id) is str
            and event.response_id != ""
            and type(event.output_text) is str
            and event.message is None
        ):
            _raise("runtime_contract")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("persistence_contract")


def _validate_persistence(
    value: object,
    result: StepRuntimeExecutionResult,
    original: WorkflowExecutionState,
    state_path: Path,
    events_path: Path,
    original_events: bytes,
) -> None:
    if type(value) is not WorkflowExecutionPersistenceResult:
        _raise("persistence_contract")
    if (
        value.state_path != state_path
        or value.events_path != events_path
        or type(value.state_bytes_written) is not int
        or type(value.event_bytes_appended) is not int
        or value.state_bytes_written < 1
        or value.event_bytes_appended < 1
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
        _restore_if_changed(state, events, original)
        _raise("persistence_contract")


def _raise(classification: Classification) -> None:
    raise ExecutedResultTransitionPersistenceBridgeCompatibilityError(
        classification
    ) from None
