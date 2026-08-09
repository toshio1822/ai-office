"""Phase 127 runtime-result persistence handoff-chain boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffReentryContinuationError as Phase120Error,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary import (
    route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary,
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
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_workflow_execution_state_json,
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
Phase120Function = Callable[
    [object, object, object, object],
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationFailureDetail:
    """Safe classification for one Phase 127 compatibility failure."""

    classification: Classification


class RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationError(
    ValueError
):
    """Base error for the Phase 127 boundary."""


class RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError(
    RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationError
):
    """Raised when one runtime result cannot safely cross Phase 127."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "runtime-result transition persistence cycle handoff chain reentry continuation inputs are incompatible"
        )
        self.detail = (
            RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationFailureDetail(
                classification
            )
        )


def route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase120_function: Phase120Function = (
        route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary
    ),
) -> (
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route one exact Phase 126 result through the public Phase 120 boundary."""
    _check_inputs(result, workflow, state_path, events_path, phase120_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow)
    elif type(result) is PersistedExecutionOutcome:
        _check_failure(result, workflow)

    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _check_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(
            state_path, events_path, original, "terminal_contract"
        )
        return result
    if type(result) is PersistedExecutionOutcome:
        _check_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(
            state_path, events_path, original, "terminal_contract"
        )
        return result

    assert type(result) in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
    state = _load_running_state(state_path)
    _check_predecessor_history(workflow, state, state_path, events_path)
    try:
        valid_result = is_valid_step_runtime_execution_result(
            result,
            workflow_id=state.workflow_id,
            step_id=state.current_step_id,
            step_index=state.current_step_index,
            employee_id=state.current_employee_id,
        )
    except Exception:
        valid_result = False
    if not valid_result:
        _fail("runtime_contract")

    try:
        value = phase120_function(result, workflow, state_path, events_path)
    except Phase120Error as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _fail("dependency_error")

    try:
        _check_persistence(
            value,
            result,
            state,
            workflow,
            state_path,
            events_path,
            original,
        )
    except RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    function: object,
) -> None:
    if type(result) not in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")
    if not callable(function):
        _fail("persistence_contract")


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    if type(workflow.steps) is not list or not workflow.steps:
        return False
    if not all(
        type(step) is WorkflowStepDefinition
        and _nonempty_string(step.id)
        and _nonempty_string(step.name)
        and _nonempty_string(step.employee)
        and _nonempty_string(step.instructions)
        for step in workflow.steps
    ):
        return False
    step_ids = tuple(step.id for step in workflow.steps)
    return (
        _nonempty_string(workflow.id)
        and _nonempty_string(workflow.name)
        and _nonempty_string(workflow.description)
        and len(step_ids) == len(set(step_ids))
    )


def _check_completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        _exact_string(value.decision, "workflow_complete")
        and _exact_string(value.workflow_id, workflow.id)
        and _exact_string(value.current_step_id, final.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact_string(value.current_employee_id, final.employee)
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and _exact_string(value.reason, "last_step_succeeded")
    ):
        _fail("completion_contract")


def _check_failure(
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    index = value.current_step_index
    if not (
        _exact_string(value.outcome, "persisted_failure")
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
    ):
        _fail("failure_contract")
    step = workflow.steps[index - 1]
    if not (
        _exact_string(value.workflow_id, workflow.id)
        and _exact_string(value.current_step_id, step.id)
        and _exact_string(value.current_employee_id, step.employee)
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _fail("failure_contract")


def _check_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _fail("state_target")
    except OSError:
        _fail("state_target")
    try:
        if not events.is_file():
            _fail("event_target")
    except OSError:
        _fail("event_target")


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _fail("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _fail("event_target")
    return state_bytes, event_bytes


def _load_running_state(path: Path) -> WorkflowExecutionState:
    try:
        state = load_workflow_execution_state(path)
    except (OSError, WorkflowExecutionDataError, WorkflowExecutionLoadError):
        _fail("runtime_contract")
    if not (
        type(state) is WorkflowExecutionState
        and _exact_string(state.status, "running")
        and state.last_failure_category is None
    ):
        _fail("runtime_contract")
    return state


def _check_predecessor_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    state_path: Path,
    events_path: Path,
) -> None:
    index = state.current_step_index
    if type(index) is not int or not 3 <= index <= len(workflow.steps):
        _fail("runtime_contract")
    current = workflow.steps[index - 1]
    prefix = tuple(step.id for step in workflow.steps[: index - 1])
    if not (
        _exact_string(state.workflow_id, workflow.id)
        and _exact_string(state.current_step_id, current.id)
        and type(state.current_step_index) is int
        and _exact_string(state.current_employee_id, current.employee)
        and type(state.completed_step_ids) is tuple
        and all(type(item) is str for item in state.completed_step_ids)
        and state.completed_step_ids == prefix
        and state.last_failure_category is None
    ):
        _fail("runtime_contract")
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
        _fail("runtime_contract")
    if not (
        type(history.state) is WorkflowExecutionState
        and history.state == state
        and type(history.events) is tuple
        and len(history.events) == len(prefix)
    ):
        _fail("runtime_contract")
    for event, step in zip(
        history.events, workflow.steps[: index - 1], strict=True
    ):
        if not (
            type(event) is RuntimeStepEvent
            and _exact_string(event.event_type, "step_succeeded")
            and _exact_string(event.workflow_id, workflow.id)
            and _exact_string(event.step_id, step.id)
            and type(event.step_index) is int
            and event.step_index == workflow.steps.index(step) + 1
            and _exact_string(event.employee_id, step.employee)
            and _exact_string(event.previous_status, "running")
            and _exact_string(event.next_status, "succeeded")
            and _nonempty_string(event.provider)
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and _nonempty_string(event.output_text)
            and event.message is None
        ):
            _fail("runtime_contract")


def _check_terminal(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    status: Literal["succeeded", "failed"],
) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _fail("terminal_contract")
    if not (
        type(persisted) is WorkflowExecutionState
        and _exact_string(persisted.status, status)
        and (
            persisted.workflow_id,
            persisted.current_step_id,
            persisted.current_step_index,
            persisted.current_employee_id,
        )
        == (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.current_employee_id,
        )
    ):
        _fail("terminal_contract")
    if (
        type(value) is PersistedExecutionOutcome
        and persisted.last_failure_category != value.failure_category
    ):
        _fail("terminal_contract")


def _check_persistence(
    value: object,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    original_state: WorkflowExecutionState,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
) -> None:
    if type(value) is not WorkflowExecutionPersistenceResult:
        _fail("persistence_contract")
    if value.state_path is not state or value.events_path is not events:
        _fail("persistence_contract")
    if (
        type(value.state_bytes_written) is not int
        or value.state_bytes_written <= 0
        or type(value.event_bytes_appended) is not int
        or value.event_bytes_appended <= 0
    ):
        _fail("persistence_contract")
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
        state_bytes = state.read_bytes()
        event_bytes = events.read_bytes()
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _fail("persistence_contract")
    if not (
        type(history.state) is WorkflowExecutionState
        and type(history.events) is tuple
        and history.events
        and len(state_bytes) == value.state_bytes_written
        and state_bytes
        == serialize_workflow_execution_state_json(history.state).encode("utf-8")
        and event_bytes.startswith(original[1])
        and len(event_bytes) - len(original[1]) == value.event_bytes_appended
        and len(history.events) == len(original[1].splitlines()) + 1
    ):
        _fail("persistence_contract")

    final = history.state
    event = history.events[-1]
    invocation = result.invocation_result
    successful = type(result) is StepRuntimeExecutionSuccess
    base = (
        final.workflow_id == original_state.workflow_id == workflow.id
        and final.current_step_id == original_state.current_step_id == result.step_id
        and final.current_step_index
        == original_state.current_step_index
        == result.step_index
        and final.current_employee_id
        == original_state.current_employee_id
        == result.employee_id
        and final.status == ("succeeded" if successful else "failed")
        and event.event_type == ("step_succeeded" if successful else "step_failed")
        and event.workflow_id == workflow.id == result.workflow_id
        and event.step_id == result.step_id
        and event.step_index == result.step_index
        and event.employee_id == result.employee_id
        and event.previous_status == "running"
        and event.next_status == final.status
        and event.provider == invocation.provider
        and event.request_id == invocation.request_id
    )
    if successful:
        details = (
            final.completed_step_ids
            == original_state.completed_step_ids + (original_state.current_step_id,)
            and final.last_failure_category is None
            and event.failure_category is None
            and event.response_id == invocation.response_id
            and event.output_text == invocation.text
            and event.message is None
        )
    else:
        details = (
            final.completed_step_ids == original_state.completed_step_ids
            and final.last_failure_category == invocation.category
            and event.failure_category == invocation.category
            and event.response_id is None
            and event.output_text is None
            and event.message == invocation.message
        )
    if not base or not details:
        _fail("persistence_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]):
        _fail("dependency_rollback")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _fail(classification: Classification) -> None:
    raise RuntimeResultTransitionPersistenceCycleHandoffChainReentryContinuationCompatibilityError(
        classification
    ) from None


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected
