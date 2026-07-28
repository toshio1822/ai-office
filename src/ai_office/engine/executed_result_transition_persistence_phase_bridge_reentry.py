"""Bridge exact Phase 56 results to the existing Phase 50 boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.executed_result_transition_persistence_bridge_reentry import (
    ExecutedResultTransitionPersistenceBridgeError,
    route_executed_result_transition_persistence_bridge_reentry,
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
    StepRuntimeExecutionFailure,
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
Phase50Function = Callable[
    ..., WorkflowExecutionPersistenceResult | WorkflowProgressionDecision
]


@dataclass(frozen=True)
class ExecutedResultTransitionPersistencePhaseBridgeFailureDetail:
    classification: Classification


class ExecutedResultTransitionPersistencePhaseBridgeError(ValueError):
    """Raised when Phase 57 cannot safely route its supplied result."""


class ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError(
    ExecutedResultTransitionPersistencePhaseBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "executed-result transition persistence phase bridge "
            "inputs are incompatible"
        )
        self.detail = ExecutedResultTransitionPersistencePhaseBridgeFailureDetail(
            classification
        )


def route_executed_result_transition_persistence_phase_bridge_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase50_function: Phase50Function = (
        route_executed_result_transition_persistence_bridge_reentry
    ),
) -> (
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route exactly one Phase 56 result and stop at this boundary."""
    _validate_top_level(result, workflow, state_path, events_path, phase50_function)
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
    _validate_running_history(workflow, state, state_path, events_path)
    if not is_valid_step_runtime_execution_result(
        result,
        workflow_id=state.workflow_id,
        step_id=state.current_step_id,
        step_index=state.current_step_index,
        employee_id=state.current_employee_id,
    ):
        _raise("runtime_contract")
    try:
        value = phase50_function(result, workflow, state_path, events_path)
    except ExecutedResultTransitionPersistenceBridgeError as error:
        _restore_after_mutation(state_path, events_path, original)
        raise error
    except Exception:
        _restore_after_mutation(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _validate_persistence(value, result, state, state_path, events_path, original)
    except ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError:
        _restore_after_mutation(state_path, events_path, original)
        raise
    return value  # type: ignore[return-value]


def _validate_top_level(
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


def _validate_completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        type(value.decision) is str
        and value.decision == "workflow_complete"
        and type(value.workflow_id) is str
        and value.workflow_id == workflow.id
        and type(value.current_step_id) is str
        and value.current_step_id == final.id
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and type(value.current_employee_id) is str
        and value.current_employee_id == final.employee
        and value.next_step_id
        is value.next_step_index
        is value.next_employee_id
        is None
        and type(value.reason) is str
        and value.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_failure(
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    if value.outcome != "persisted_failure" or type(value.outcome) is not str:
        _raise("failure_contract")
    if type(
        value.current_step_index
    ) is not int or not 1 <= value.current_step_index <= len(workflow.steps):
        _raise("failure_contract")
    step = workflow.steps[value.current_step_index - 1]
    if not (
        type(value.workflow_id) is str
        and value.workflow_id == workflow.id
        and type(value.current_step_id) is str
        and value.current_step_id == step.id
        and type(value.current_employee_id) is str
        and value.current_employee_id == step.employee
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _raise("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("state_target")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("state_target")


def _load_running_state(path: Path) -> WorkflowExecutionState:
    try:
        state = load_workflow_execution_state(path)
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _raise("runtime_contract")
    if state.status != "running" or state.last_failure_category is not None:
        _raise("runtime_contract")
    return state


def _validate_running_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    state_path: Path,
    events_path: Path,
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
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("runtime_contract")
    if history.state != state or len(history.events) != len(prefix):
        _raise("runtime_contract")
    for event, expected in zip(
        history.events, workflow.steps[: state.current_step_index - 1], strict=True
    ):
        if not (
            event.event_type == "step_succeeded"
            and event.workflow_id == workflow.id
            and event.step_id == expected.id
            and event.step_index == workflow.steps.index(expected) + 1
            and event.employee_id == expected.employee
            and event.previous_status == "running"
            and event.next_status == "succeeded"
            and event.failure_category is None
            and type(event.response_id) is str
            and event.response_id
            and type(event.output_text) is str
            and event.output_text
            and event.message is None
        ):
            _raise("runtime_contract")


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


def _validate_persistence(
    value: object,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    original_state: WorkflowExecutionState,
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
) -> None:
    if type(value) is not WorkflowExecutionPersistenceResult:
        _raise("persistence_contract")
    if value.state_path != state or value.events_path != events:
        _raise("persistence_contract")
    if type(value.state_bytes_written) is not int or value.state_bytes_written <= 0:
        _raise("persistence_contract")
    if type(value.event_bytes_appended) is not int or value.event_bytes_appended <= 0:
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
        or not event_bytes.startswith(original[1])
        or len(event_bytes) - len(original[1]) != value.event_bytes_appended
        or len(history.events) != len(original[1].splitlines()) + 1
    ):
        _raise("persistence_contract")
    final = history.state
    event = history.events[-1]
    success = type(result) is StepRuntimeExecutionSuccess
    invocation = result.invocation_result
    base = (
        final.workflow_id == original_state.workflow_id
        and final.current_step_id == original_state.current_step_id
        and final.current_step_index == original_state.current_step_index
        and final.current_employee_id == original_state.current_employee_id
        and final.status == ("succeeded" if success else "failed")
        and event.event_type == ("step_succeeded" if success else "step_failed")
        and event.workflow_id == result.workflow_id
        and event.step_id == result.step_id
        and event.step_index == result.step_index
        and event.employee_id == result.employee_id
        and event.previous_status == "running"
        and event.next_status == final.status
        and event.provider == invocation.provider
        and event.request_id == invocation.request_id
    )
    expected = (
        final.completed_step_ids
        == original_state.completed_step_ids + (original_state.current_step_id,)
        and final.last_failure_category is None
        and event.failure_category is None
        and event.response_id == invocation.response_id
        and event.output_text == invocation.text
        and event.message is None
        if success
        else final.completed_step_ids == original_state.completed_step_ids
        and final.last_failure_category == invocation.category
        and event.failure_category == invocation.category
        and event.response_id is None
        and event.output_text is None
        and event.message == invocation.message
    )
    if not base or not expected:
        _raise("persistence_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_after_mutation(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    changed = _changed(state, original[0]) or _changed(events, original[1])
    if not changed:
        return
    failures = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failures = True
    if failures:
        _raise("dependency_rollback")


def _require_unchanged(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_after_mutation(state, events, original)
        _raise("persistence_contract")


def _raise(classification: Classification) -> None:
    raise ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError(
        classification
    ) from None
