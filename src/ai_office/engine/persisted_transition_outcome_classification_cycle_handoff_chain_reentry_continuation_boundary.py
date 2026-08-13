"""Phase 128 persisted-transition outcome-classification handoff boundary."""

# ruff: noqa: E501,E701

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationError as Phase121Error,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
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
Phase121Function = Callable[
    [object, object, object, object], PersistedExecutionOutcome | WorkflowProgressionDecision
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationFailureDetail:
    """Safe classification for one Phase 128 compatibility failure."""

    classification: Classification


class PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationError(
    ValueError
):
    """Base error for the Phase 128 boundary."""


class PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError(
    PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationError
):
    """Raised when one persisted transition cannot safely cross Phase 128."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted-transition outcome classification cycle handoff chain reentry continuation inputs are incompatible"
        )
        self.detail = PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationFailureDetail(
            classification
        )


def route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase121_function: Phase121Function = (
        route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary
    ),
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    """Route one exact Phase 127 persistence result through public Phase 121."""
    _check_inputs(result, workflow, state_path, events_path, phase121_function)
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
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _check_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is WorkflowExecutionPersistenceResult
    state = _check_persistence(result, workflow, state_path, events_path)
    try:
        value = phase121_function(result, workflow, state_path, events_path)
    except Phase121Error as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _fail("dependency_error")

    try:
        _check_outcome(value, state)
    except PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError:
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
    state, history = _load_compatible_terminal_history(
        workflow, state_path, events_path
    )
    if type(state) is not WorkflowExecutionState or type(history) is not tuple or not history:
        _fail("terminal_contract")
    if type(state.current_step_index) is not int or not 3 <= state.current_step_index <= len(workflow.steps):
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
        and _valid_terminal_event(history[-1], state)
    ):
        _fail("persistence_contract")
    return state


def _valid_terminal_event(event: RuntimeStepEvent, state: WorkflowExecutionState) -> bool:
    if event.provider != "openai":
        return False
    if event.request_id is not None and not _nonempty_string(event.request_id):
        return False
    if state.status == "succeeded":
        return (
            event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and type(event.output_text) is str
            and event.message is None
        )
    return (
        event.event_type == "step_failed"
        and event.next_status == "failed"
        and event.failure_category == state.last_failure_category
        and event.response_id is None
        and event.output_text is None
        and _nonempty_string(event.message)
    )


def _load_compatible_terminal_history(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    """Load the one valid empty-success-output case without weakening others."""
    try:
        return load_strict_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        pass
    try:
        loaded = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (OSError, WorkflowExecutionLoadError):
        _fail("terminal_contract")
    state = loaded.state
    history = loaded.events
    if not (
        _valid_empty_success_history(workflow, state, history)
        or _valid_phase155_compatible_history(workflow, state, history)
    ):
        _fail("terminal_contract")
    return state, history


def _valid_empty_success_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    history: tuple[RuntimeStepEvent, ...],
) -> bool:
    if type(state) is not WorkflowExecutionState or type(history) is not tuple:
        return False
    if not (
        _nonempty_string(state.workflow_id)
        and state.workflow_id == workflow.id
        and _exact_string(state.status, "succeeded")
        and _nonempty_string(state.current_step_id)
        and type(state.current_step_index) is int
        and 1 <= state.current_step_index <= len(workflow.steps)
        and state.current_step_id == workflow.steps[state.current_step_index - 1].id
        and _nonempty_string(state.current_employee_id)
        and state.current_employee_id == workflow.steps[state.current_step_index - 1].employee
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
        and state.completed_step_ids
        == tuple(step.id for step in workflow.steps[: state.current_step_index])
        and state.last_failure_category is None
    ):
        return False
    prior_steps = workflow.steps[: state.current_step_index - 1]
    if len(history) != len(prior_steps) + 1:
        return False
    for position, (event, step) in enumerate(zip(history[:-1], prior_steps, strict=True), 1):
        if not (
            type(event) is RuntimeStepEvent
            and _exact_string(event.event_type, "step_succeeded")
            and _exact_string(event.workflow_id, state.workflow_id)
            and _exact_string(event.step_id, step.id)
            and type(event.step_index) is int
            and event.step_index == position
            and _exact_string(event.employee_id, step.employee)
            and _exact_string(event.previous_status, "running")
            and _exact_string(event.next_status, "succeeded")
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and _nonempty_string(event.output_text)
            and event.message is None
        ):
            return False
    terminal = history[-1]
    return (
        type(terminal) is RuntimeStepEvent
        and _exact_string(terminal.event_type, "step_succeeded")
        and _exact_string(terminal.workflow_id, state.workflow_id)
        and _exact_string(terminal.step_id, state.current_step_id)
        and type(terminal.step_index) is int
        and terminal.step_index == state.current_step_index
        and _exact_string(terminal.employee_id, state.current_employee_id)
        and _exact_string(terminal.previous_status, "running")
        and _exact_string(terminal.next_status, "succeeded")
        and _exact_string(terminal.provider, "openai")
        and terminal.failure_category is None
        and _nonempty_string(terminal.response_id)
        and (terminal.request_id is None or _nonempty_string(terminal.request_id))
        and type(terminal.output_text) is str
        and terminal.output_text == ""
        and terminal.message is None
    )


def _valid_phase155_compatible_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    history: tuple[RuntimeStepEvent, ...],
) -> bool:
    """Bounded Phase-155 provenance compatibility: current_step_index >= 6.

    Allows exact built-in str predecessor output_text to be empty or non-empty
    (None / non-string are rejected). Terminal event semantics are inherited
    from the shared validator. Only reachable from the persistence route.
    """
    if type(state) is not WorkflowExecutionState or type(history) is not tuple:
        return False
    if not (
        _nonempty_string(state.workflow_id)
        and state.workflow_id == workflow.id
        and state.status in {"succeeded", "failed"}
        and _nonempty_string(state.current_step_id)
        and type(state.current_step_index) is int
        and state.current_step_index >= 6
        and state.current_step_index <= len(workflow.steps)
        and state.current_step_id == workflow.steps[state.current_step_index - 1].id
        and _nonempty_string(state.current_employee_id)
        and state.current_employee_id
        == workflow.steps[state.current_step_index - 1].employee
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
        and state.completed_step_ids
        == tuple(
            step.id
            for step in workflow.steps[
                : state.current_step_index
                if state.status == "succeeded"
                else state.current_step_index - 1
            ]
        )
        and (
            state.last_failure_category is None
            if state.status == "succeeded"
            else type(state.last_failure_category) is str
            and state.last_failure_category in _FAILURE_CATEGORIES
        )
    ):
        return False
    prior_steps = workflow.steps[: state.current_step_index - 1]
    if len(history) != len(prior_steps) + 1:
        return False
    for position, (event, step) in enumerate(
        zip(history[:-1], prior_steps, strict=True), 1
    ):
        if not (
            type(event) is RuntimeStepEvent
            and _exact_string(event.event_type, "step_succeeded")
            and _exact_string(event.workflow_id, state.workflow_id)
            and _exact_string(event.step_id, step.id)
            and type(event.step_index) is int
            and event.step_index == position
            and _exact_string(event.employee_id, step.employee)
            and _exact_string(event.previous_status, "running")
            and _exact_string(event.next_status, "succeeded")
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and type(event.output_text) is str
            and event.message is None
        ):
            return False
    return _valid_terminal_event(history[-1], state)


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


def _fail(classification: Classification) -> None:
    raise PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError(
        classification
    ) from None


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected
