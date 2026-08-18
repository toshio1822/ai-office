"""Phase 135 persisted-transition outcome-classification outer bridge."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationError as Phase128Error,
    route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
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
    "persistence_contract",
    "outcome_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase128Function = Callable[
    [object, object, object, object], PersistedExecutionOutcome | WorkflowProgressionDecision
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationFailureDetail:
    """Safe classification for one Phase 135 compatibility failure."""

    classification: Classification


class PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationError(
    ValueError
):
    """Base error for the Phase 135 boundary."""


class PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError(
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationError
):
    """Raised when one Phase 134 result cannot safely cross Phase 135."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted-transition outcome classification cycle handoff chain bridge inputs are incompatible"
        )
        self.detail = PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationFailureDetail(
            classification
        )


def route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase128_function: Phase128Function = (
        route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary
    ),
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    """Route one exact Phase 134 result through public Phase 128 once."""
    _check_inputs(result, workflow, state_path, events_path, phase128_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow)
    elif type(result) is PersistedExecutionOutcome:
        _check_failure(result, workflow)

    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _check_terminal_history(
            workflow,
            state_path,
            events_path,
            "succeeded",
            result,
            classification="terminal_contract",
            minimum_index=1,
            require_immediate_openai=False,
            allow_empty_success_output=False,
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _check_terminal_history(
            workflow,
            state_path,
            events_path,
            "failed",
            result,
            classification="terminal_contract",
            minimum_index=1,
            require_immediate_openai=False,
            allow_empty_success_output=False,
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is WorkflowExecutionPersistenceResult
    state = _check_persistence(result, workflow, state_path, events_path)
    try:
        value = phase128_function(result, workflow, state_path, events_path)
    except Phase128Error as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _fail("dependency_error")

    try:
        _check_outcome(value, state)
    except PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError:
        _restore_if_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _fail("outcome_contract")
    if _changed(state_path, original[0]) or _changed(events_path, original[1]):
        _restore_if_changed(state_path, events_path, original)
        _fail("outcome_contract")
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    function: object,
) -> None:
    if type(result) not in (
        WorkflowExecutionPersistenceResult,
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
    if not (
        _nonempty_string(workflow.id)
        and _nonempty_string(workflow.name)
        and _nonempty_string(workflow.description)
        and type(workflow.steps) is list
        and bool(workflow.steps)
    ):
        return False
    if any(
        type(step) is not WorkflowStepDefinition
        or not _nonempty_string(step.id)
        or not _nonempty_string(step.name)
        or not _nonempty_string(step.employee)
        or not _nonempty_string(step.instructions)
        for step in workflow.steps
    ):
        return False
    step_ids = tuple(step.id for step in workflow.steps)
    return len(step_ids) == len(set(step_ids))


def _check_completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition) -> None:
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


def _check_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
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


def _check_terminal_history(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    expected_status: Literal["succeeded", "failed"],
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    *,
    classification: Classification,
    minimum_index: int,
    require_immediate_openai: bool,
    allow_empty_success_output: bool,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    state, history = _load_history(workflow, state_path, events_path, classification)
    expected_failure = (
        result.failure_category if type(result) is PersistedExecutionOutcome else None
    )
    if not _valid_history(
        workflow,
        state,
        history,
        expected_status,
        result,
        expected_failure,
        minimum_index,
        require_immediate_openai,
        allow_empty_success_output,
    ):
        _fail(classification)
    return state, history


def _load_history(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    classification: Classification,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    del workflow
    try:
        loaded = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (OSError, WorkflowExecutionLoadError):
        _fail(classification)
    state = loaded.state
    history = loaded.events
    if type(state) is not WorkflowExecutionState or type(history) is not tuple:
        _fail(classification)
    return state, history


def _valid_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    history: tuple[RuntimeStepEvent, ...],
    expected_status: Literal["succeeded", "failed"],
    result: object | None,
    expected_failure: object,
    minimum_index: int,
    require_immediate_openai: bool,
    allow_empty_success_output: bool,
    allow_empty_predecessor_output: bool = False,
    allow_immediate_none_request_id: bool = False,
    allow_accumulated_none_request_id: bool = False,
) -> bool:
    index = state.current_step_index
    result_identity_valid = result is None or (
        _exact_string(result.workflow_id, state.workflow_id)
        and _exact_string(result.current_step_id, state.current_step_id)
        and type(result.current_step_index) is int
        and result.current_step_index == state.current_step_index
        and _exact_string(result.current_employee_id, state.current_employee_id)
    )
    if not (
        _valid_state(state, workflow)
        and state.status == expected_status
        and type(index) is int
        and minimum_index <= index <= len(workflow.steps)
        and result_identity_valid
        and state.last_failure_category == expected_failure
    ):
        return False
    expected_completed = (
        tuple(step.id for step in workflow.steps[:index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: index - 1])
    )
    if state.completed_step_ids != expected_completed:
        return False
    prior_steps = workflow.steps[: index - 1]
    if len(history) != len(prior_steps) + 1:
        return False
    if any(type(event) is not RuntimeStepEvent for event in history):
        return False
    for position, (event, step) in enumerate(zip(history[:-1], prior_steps, strict=True), 1):
        last_position = len(prior_steps)
        allow_none = (
            allow_immediate_none_request_id and position == last_position
        ) or (
            allow_accumulated_none_request_id
            and last_position >= 6
            and position >= 5
        )
        if not _valid_predecessor(
            event,
            step,
            position,
            state,
            require_openai=require_immediate_openai and position == last_position,
            allow_empty_output=allow_empty_predecessor_output,
            allow_none_request_id=allow_none,
        ):
            return False
        if allow_none and event.request_id is None and event.provider != "openai":
            return False
    return _valid_terminal_event(
        history[-1],
        state,
        expected_failure,
        require_openai=require_immediate_openai,
        allow_empty_success_output=allow_empty_success_output,
    )


def _valid_state(state: WorkflowExecutionState, workflow: WorkflowDefinition) -> bool:
    if type(state.current_step_index) is not int or not 1 <= state.current_step_index <= len(workflow.steps):
        return False
    current = workflow.steps[state.current_step_index - 1]
    return (
        _nonempty_string(state.workflow_id)
        and state.workflow_id == workflow.id
        and type(state.status) is str
        and state.status in {"succeeded", "failed"}
        and _nonempty_string(state.current_step_id)
        and state.current_step_id == current.id
        and _nonempty_string(state.current_employee_id)
        and state.current_employee_id == current.employee
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
        and (
            state.last_failure_category is None
            or (
                type(state.last_failure_category) is str
                and state.last_failure_category in _FAILURE_CATEGORIES
            )
        )
    )


def _valid_predecessor(
    event: RuntimeStepEvent,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
    *,
    require_openai: bool,
    allow_empty_output: bool = False,
    allow_none_request_id: bool = False,
) -> bool:
    return (
        type(event) is RuntimeStepEvent
        and _exact_string(event.event_type, "step_succeeded")
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, step.id)
        and type(event.step_index) is int
        and event.step_index == position
        and _exact_string(event.employee_id, step.employee)
        and _exact_string(event.previous_status, "running")
        and _exact_string(event.next_status, "succeeded")
        and _nonempty_string(event.provider)
        and (not require_openai or event.provider == "openai")
        and event.failure_category is None
        and _nonempty_string(event.response_id)
        and (
            (event.request_id is None or _nonempty_string(event.request_id))
            if allow_none_request_id
            else _nonempty_string(event.request_id)
        )
        and type(event.output_text) is str
        and (allow_empty_output or bool(event.output_text))
        and event.message is None
    )


def _valid_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
    expected_failure: object,
    *,
    require_openai: bool,
    allow_empty_success_output: bool,
) -> bool:
    base = (
        type(event) is RuntimeStepEvent
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, state.current_step_id)
        and type(event.step_index) is int
        and event.step_index == state.current_step_index
        and _exact_string(event.employee_id, state.current_employee_id)
        and _exact_string(event.previous_status, "running")
        and _nonempty_string(event.provider)
        and (not require_openai or event.provider == "openai")
        and (event.request_id is None or _nonempty_string(event.request_id))
    )
    if state.status == "succeeded":
        return (
            base
            and _exact_string(event.event_type, "step_succeeded")
            and _exact_string(event.next_status, "succeeded")
            and expected_failure is None
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and type(event.output_text) is str
            and (allow_empty_success_output or bool(event.output_text))
            and event.message is None
        )
    return (
        base
        and _exact_string(event.event_type, "step_failed")
        and _exact_string(event.next_status, "failed")
        and event.failure_category == expected_failure
        and type(expected_failure) is str
        and expected_failure in _FAILURE_CATEGORIES
        and event.response_id is None
        and event.output_text is None
        and _nonempty_string(event.message)
    )


def _check_persistence(
    result: WorkflowExecutionPersistenceResult,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> WorkflowExecutionState:
    if result.state_path is not state_path or result.events_path is not events_path:
        _fail("persistence_contract")
    if (
        type(result.state_bytes_written) is not int
        or result.state_bytes_written <= 0
        or type(result.event_bytes_appended) is not int
        or result.event_bytes_appended <= 0
    ):
        _fail("persistence_contract")
    state, history = _load_history(
        workflow, state_path, events_path, "persistence_contract"
    )
    if not _valid_history(
        workflow,
        state,
        history,
        state.status if state.status in {"succeeded", "failed"} else "succeeded",
        None,
        state.last_failure_category,
        4,
        require_immediate_openai=True,
        allow_empty_success_output=True,
        allow_empty_predecessor_output=True,
        allow_immediate_none_request_id=True,
        allow_accumulated_none_request_id=True,
    ):
        _fail("persistence_contract")
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
    except OSError:
        _fail("persistence_contract")
    terminal_bytes = serialize_runtime_step_event_jsonl(history[-1]).encode("utf-8")
    expected_event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in history
    )
    if not (
        state_bytes == serialize_workflow_execution_state_json(state).encode("utf-8")
        and len(state_bytes) == result.state_bytes_written
        and event_bytes == expected_event_bytes
        and event_bytes.endswith(terminal_bytes)
        and result.event_bytes_appended == len(terminal_bytes)
    ):
        _fail("persistence_contract")
    return state


def _check_outcome(value: object, state: WorkflowExecutionState) -> None:
    if type(value) is not PersistedExecutionOutcome:
        _fail("outcome_contract")
    expected = "persisted_success" if state.status == "succeeded" else "persisted_failure"
    valid_failure = (
        value.failure_category is None
        if state.status == "succeeded"
        else type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
        and value.failure_category == state.last_failure_category
    )
    if not (
        _exact_string(value.outcome, expected)
        and _exact_string(value.workflow_id, state.workflow_id)
        and _exact_string(value.current_step_id, state.current_step_id)
        and type(value.current_step_index) is int
        and value.current_step_index >= 4
        and value.current_step_index == state.current_step_index
        and _exact_string(value.current_employee_id, state.current_employee_id)
        and valid_failure
    ):
        _fail("outcome_contract")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
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


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _fail(classification: Classification) -> None:
    raise PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError(
        classification
    ) from None
