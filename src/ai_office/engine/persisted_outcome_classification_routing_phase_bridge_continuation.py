"""Continue exact Phase 71 persistence results through the Phase 65 boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_terminal_outcome_classification_routing_phase_bridge_reentry import (
    PersistedTerminalOutcomeClassificationRoutingPhaseBridgeError,
    route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
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
Phase65Function = Callable[..., PersistedExecutionOutcome | WorkflowProgressionDecision]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PersistedOutcomeClassificationRoutingPhaseBridgeContinuationFailureDetail:
    classification: Classification


class PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError(ValueError):
    """Raised when Phase 72 cannot safely continue the supplied result."""


class PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError(
    PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted outcome classification routing phase bridge continuation inputs are incompatible"
        )
        self.detail = (
            PersistedOutcomeClassificationRoutingPhaseBridgeContinuationFailureDetail(
                classification
            )
        )


def route_persisted_outcome_classification_routing_phase_bridge_continuation(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase65_function: Phase65Function = route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry,
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    _top_level(result, workflow, state_path, events_path, phase65_function)
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
        value = phase65_function(result, workflow, state_path, events_path)
    except PersistedTerminalOutcomeClassificationRoutingPhaseBridgeError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "outcome_contract")
        _validate_outcome(value, state)
    except (
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _top_level(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (
        WorkflowExecutionPersistenceResult,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
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


def _validate_completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        _exact_str(value.decision, "workflow_complete")
        and _exact_str(value.workflow_id, workflow.id)
        and _exact_str(value.current_step_id, final.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact_str(value.current_employee_id, final.employee)
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and _exact_str(value.reason, "last_step_succeeded")
    ):
        _raise("completion_contract")


def _validate_failure(
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    if not (
        _exact_str(value.outcome, "persisted_failure")
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
    ):
        _raise("failure_contract")
    step = workflow.steps[value.current_step_index - 1]
    if not (
        _exact_str(value.workflow_id, workflow.id)
        and _exact_str(value.current_step_id, step.id)
        and _exact_str(value.current_employee_id, step.employee)
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
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


def _validate_terminal(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
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
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.current_employee_id,
    ):
        _raise("terminal_contract")
    if (
        type(value) is PersistedExecutionOutcome
        and persisted.last_failure_category != value.failure_category
    ):
        _raise("terminal_contract")


def _validate_persistence(
    result: WorkflowExecutionPersistenceResult,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> WorkflowExecutionState:
    if not (
        result.state_path is state_path
        and result.events_path is events_path
        and type(result.state_bytes_written) is int
        and type(result.event_bytes_appended) is int
        and result.state_bytes_written > 0
        and result.event_bytes_appended > 0
    ):
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
        state, history = load_strict_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if state.status not in ("succeeded", "failed"):
        _raise("persistence_contract")
    _validate_terminal_event_types(state, history[-1])
    final_event = serialize_runtime_step_event_jsonl(history[-1]).encode()
    if (
        len(state_bytes) != result.state_bytes_written
        or len(final_event) != result.event_bytes_appended
        or not events_bytes.endswith(final_event)
    ):
        _raise("persistence_contract")
    return state


def _validate_terminal_event_types(
    state: WorkflowExecutionState, event: object
) -> None:
    if type(event.event_type) is not str or type(event.workflow_id) is not str:
        _raise("terminal_contract")
    if type(event.step_id) is not str or type(event.step_index) is not int:
        _raise("terminal_contract")
    if type(event.employee_id) is not str or type(event.previous_status) is not str:
        _raise("terminal_contract")
    if type(event.next_status) is not str or type(event.provider) is not str:
        _raise("terminal_contract")
    if type(event.request_id) is not str:
        _raise("terminal_contract")
    if state.status == "succeeded":
        if type(event.response_id) is not str or type(event.output_text) is not str:
            _raise("terminal_contract")
        if event.failure_category is not None or event.message is not None:
            _raise("terminal_contract")
    else:
        if event.response_id is not None or event.output_text is not None:
            _raise("terminal_contract")
        if type(event.failure_category) is not str or type(event.message) is not str:
            _raise("terminal_contract")


def _validate_outcome(value: object, state: WorkflowExecutionState) -> None:
    if type(value) is not PersistedExecutionOutcome:
        _raise("outcome_contract")
    expected_outcome = (
        "persisted_success" if state.status == "succeeded" else "persisted_failure"
    )
    if not (
        type(value.outcome) is str
        and value.outcome == expected_outcome
        and _exact_str(value.workflow_id, state.workflow_id)
        and _exact_str(value.current_step_id, state.current_step_id)
        and type(value.current_step_index) is int
        and value.current_step_index == state.current_step_index
        and _exact_str(value.current_employee_id, state.current_employee_id)
        and ((value.failure_category is None) == (state.status == "succeeded"))
        and (
            value.failure_category is None
            or (
                type(value.failure_category) is str
                and value.failure_category in _FAILURE_CATEGORIES
            )
        )
        and value.failure_category == state.last_failure_category
    ):
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


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore(state, events, original)


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError(
        classification
    ) from None


def _exact_str(value: object, expected: str) -> bool:
    return type(value) is str and value == expected
