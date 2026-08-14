"""Continue one exact Phase 85 persistence result through Phase 79."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_outcome_classification_routing_phase_bridge_cycle_continuation import (
    PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationError,
    route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation,
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
)

Classification = Literal[
    "result_type", "workflow_definition", "completion_contract", "failure_contract",
    "state_target", "event_target", "target_conflict", "terminal_contract",
    "persistence_contract", "outcome_contract", "dependency_error", "dependency_rollback",
]
Phase79Function = Callable[..., PersistedExecutionOutcome | WorkflowProgressionDecision]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationFailureDetail:
    classification: Classification


class PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationError(ValueError):
    """Raised when Phase 79 cannot safely continue the supplied result."""


class PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError(
    PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted outcome classification routing phase bridge cycle continuation inputs are incompatible"
        )
        self.detail = PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationFailureDetail(
            classification
        )


def route_persisted_outcome_classification_routing_phase_bridge_cycle_reentry_continuation(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase79_function: Phase79Function = route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation,
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    _top_level(result, workflow, state_path, events_path, phase79_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
    elif type(result) is PersistedExecutionOutcome:
        _validate_failure(result, workflow)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    assert type(result) is WorkflowExecutionPersistenceResult
    state = _validate_persistence(result, workflow, state_path, events_path)
    try:
        value = phase79_function(result, workflow, state_path, events_path)
    except PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "outcome_contract")
        _validate_outcome(value, state)
    except PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _top_level(result: object, workflow: object, state: object, events: object, function: object) -> None:
    if type(result) not in (WorkflowExecutionPersistenceResult, WorkflowProgressionDecision, PersistedExecutionOutcome):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _raise("state_target")
    if type(events) is not _PATH_TYPE:
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("outcome_contract")


def _validate_completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition) -> None:
    final = workflow.steps[-1]
    if not (
        _exact_str(value.decision, "workflow_complete") and _exact_str(value.workflow_id, workflow.id)
        and _exact_str(value.current_step_id, final.id) and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps) and _exact_str(value.current_employee_id, final.employee)
        and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None
        and _exact_str(value.reason, "last_step_succeeded")
    ):
        _raise("completion_contract")


def _validate_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if not (_exact_str(value.outcome, "persisted_failure") and type(value.current_step_index) is int
            and 1 <= value.current_step_index <= len(workflow.steps)):
        _raise("failure_contract")
    step = workflow.steps[value.current_step_index - 1]
    if not (_exact_str(value.workflow_id, workflow.id) and _exact_str(value.current_step_id, step.id)
            and _exact_str(value.current_employee_id, step.employee) and type(value.failure_category) is str
            and value.failure_category in _FAILURE_CATEGORIES):
        _raise("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
    except OSError:
        _raise("state_target")
    try:
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("event_target")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _raise("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _raise("event_target")
    return state_bytes, event_bytes


def _validate_terminal(value: WorkflowProgressionDecision | PersistedExecutionOutcome, workflow: WorkflowDefinition,
                       state: Path, events: Path, status: Literal["succeeded", "failed"]) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if persisted.status != status or (persisted.workflow_id, persisted.current_step_id, persisted.current_step_index,
                                      persisted.current_employee_id) != (value.workflow_id, value.current_step_id,
                                                                         value.current_step_index, value.current_employee_id):
        _raise("terminal_contract")
    if type(value) is PersistedExecutionOutcome and persisted.last_failure_category != value.failure_category:
        _raise("terminal_contract")


def _validate_persistence(result: WorkflowExecutionPersistenceResult, workflow: WorkflowDefinition,
                          state_path: Path, events_path: Path) -> WorkflowExecutionState:
    if not (result.state_path is state_path and result.events_path is events_path
            and type(result.state_bytes_written) is int and type(result.event_bytes_appended) is int
            and result.state_bytes_written > 0 and result.event_bytes_appended > 0):
        _raise("persistence_contract")
    try:
        state_bytes = state_path.read_bytes()
    except OSError:
        _raise("state_target")
    try:
        events_bytes = events_path.read_bytes()
    except OSError:
        _raise("event_target")
    try:
        state, history = _load_compatible_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if state.status not in ("succeeded", "failed"):
        _raise("persistence_contract")
    event = history[-1]
    if not _valid_event_types(state, event):
        _raise("terminal_contract")
    final_event = serialize_runtime_step_event_jsonl(event).encode()
    if (len(state_bytes) != result.state_bytes_written or len(final_event) != result.event_bytes_appended
            or not events_bytes.endswith(final_event)):
        _raise("persistence_contract")
    return state


def _load_compatible_terminal_history(workflow: WorkflowDefinition, state_path: Path, events_path: Path):
    """Load one valid Phase-155 provenance terminal history without weakening the strict contract."""
    try:
        return load_strict_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        pass
    try:
        loaded = load_workflow_execution_history(WorkflowExecutionPersistenceTargets(state_path, events_path))
    except (OSError, WorkflowExecutionLoadError):
        _raise("terminal_contract")
    if not _valid_phase155_compatible_history(workflow, loaded.state, loaded.events):
        _raise("terminal_contract")
    return loaded.state, loaded.events


def _valid_phase155_compatible_history(workflow: WorkflowDefinition, state: WorkflowExecutionState, history: tuple[RuntimeStepEvent, ...]) -> bool:
    """Bounded Phase-155 provenance compatibility: current_step_index >= 6.

    Allows exact built-in str predecessor output_text to be empty or non-empty
    (None / non-string rejected). The terminal event keeps the existing
    _valid_event_types(...) meaning; failed terminal message stays any exact
    built-in str including "" (no provider/request-id gating).
    """
    if type(state) is not WorkflowExecutionState or type(history) is not tuple:
        return False
    if not (type(state.current_step_index) is int and state.current_step_index >= 6
            and state.current_step_index <= len(workflow.steps)
            and _exact_str(state.workflow_id, workflow.id)
            and state.status in {"succeeded", "failed"}
            and _exact_str(state.current_step_id, workflow.steps[state.current_step_index - 1].id)
            and _exact_str(state.current_employee_id, workflow.steps[state.current_step_index - 1].employee)
            and type(state.completed_step_ids) is tuple
            and all(_nonempty_str(item) for item in state.completed_step_ids)
            and state.completed_step_ids == tuple(step.id for step in workflow.steps[: state.current_step_index if state.status == "succeeded" else state.current_step_index - 1])
            and (state.last_failure_category is None if state.status == "succeeded"
                 else type(state.last_failure_category) is str and state.last_failure_category in _FAILURE_CATEGORIES)):
        return False
    prior_steps = workflow.steps[: state.current_step_index - 1]
    if len(history) != len(prior_steps) + 1:
        return False
    for position, (event, step) in enumerate(zip(history[:-1], prior_steps, strict=True), 1):
        if not (type(event) is RuntimeStepEvent
                and _exact_str(event.event_type, "step_succeeded")
                and _exact_str(event.workflow_id, state.workflow_id)
                and _exact_str(event.step_id, step.id)
                and type(event.step_index) is int and event.step_index == position
                and _exact_str(event.employee_id, step.employee)
                and _exact_str(event.previous_status, "running")
                and _exact_str(event.next_status, "succeeded")
                and event.failure_category is None
                and _nonempty_str(event.response_id)
                and type(event.output_text) is str
                and event.message is None):
            return False
    return _valid_terminal_event_types(state, history[-1], workflow)


def _valid_terminal_event_types(state: WorkflowExecutionState, event: object, workflow: WorkflowDefinition) -> bool:
    """Strict succeeded-terminal contract for the Phase-155 fallback.

    The existing succeeded terminal contract is not weakened: response_id stays
    non-empty, final succeeded output_text stays non-empty, intermediate
    succeeded empty output stays allowed, and failed terminal message stays any
    exact built-in str including "".
    """
    if not _valid_event_types(state, event):
        return False
    if state.status == "succeeded":
        allow_empty_success_output = state.current_step_index < len(workflow.steps)
        if event.response_id == "":
            return False
        if not allow_empty_success_output and event.output_text == "":
            return False
    return True


def _valid_event_types(state: WorkflowExecutionState, event: object) -> bool:
    if not (type(event.event_type) is str and type(event.workflow_id) is str and type(event.step_id) is str
            and type(event.step_index) is int and type(event.employee_id) is str
            and type(event.previous_status) is str and type(event.next_status) is str
            and type(event.provider) is str and type(event.request_id) is str):
        return False
    if state.status == "succeeded":
        return (
            event.event_type == "step_succeeded"
            and type(event.response_id) is str
            and type(event.output_text) is str
            and event.failure_category is None
            and event.message is None
        )
    return (
        event.event_type == "step_failed"
        and event.response_id is None
        and event.output_text is None
        and type(event.failure_category) is str
        and event.failure_category in _FAILURE_CATEGORIES
        and type(event.message) is str
    )


def _validate_outcome(value: object, state: WorkflowExecutionState) -> None:
    if type(value) is not PersistedExecutionOutcome:
        _raise("outcome_contract")
    expected = "persisted_success" if state.status == "succeeded" else "persisted_failure"
    if not (type(value.outcome) is str and value.outcome == expected and _exact_str(value.workflow_id, state.workflow_id)
            and _exact_str(value.current_step_id, state.current_step_id) and type(value.current_step_index) is int
            and value.current_step_index == state.current_step_index and _exact_str(value.current_employee_id, state.current_employee_id)
            and ((value.failure_category is None) == (state.status == "succeeded"))
            and (value.failure_category is None or (type(value.failure_category) is str and value.failure_category in _FAILURE_CATEGORIES))
            and value.failure_category == state.last_failure_category):
        _raise("outcome_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore(state, events, original)


def _require_unchanged(state: Path, events: Path, original: tuple[bytes, bytes], classification: Classification) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError(classification) from None


def _exact_str(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _nonempty_str(value: object) -> bool:
    return type(value) is str and bool(value)
