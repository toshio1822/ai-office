"""Bridge one exact Phase 50 result to the read-only Phase 44 classifier."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_terminal_outcome_classification_routing_reentry import (
    PersistedTerminalOutcomeClassificationRoutingError,
    route_persisted_terminal_outcome_classification_reentry,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
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
    "classification_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = (
    "persisted terminal outcome classification bridge inputs are incompatible"
)
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
Phase44Function = Callable[..., PersistedExecutionOutcome | WorkflowProgressionDecision]


@dataclass(frozen=True)
class PersistedTerminalOutcomeClassificationBridgeFailureDetail:
    """Safe category for a Phase 51 compatibility rejection."""

    classification: Classification


class PersistedTerminalOutcomeClassificationBridgeError(ValueError):
    """Raised when the Phase 51 bridge cannot safely proceed."""


class PersistedTerminalOutcomeClassificationBridgeCompatibilityError(
    PersistedTerminalOutcomeClassificationBridgeError
):
    """A public, detail-safe Phase 51 compatibility error."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedTerminalOutcomeClassificationBridgeFailureDetail(
            classification
        )


def route_persisted_terminal_outcome_classification_bridge_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    classification_routing_function: Phase44Function = (
        route_persisted_terminal_outcome_classification_reentry
    ),
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    """Route exactly one Phase 50 result and stop at this boundary."""
    _validate_inputs(
        result, workflow, state_path, events_path, classification_routing_function
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
        _validate_terminal_result(
            result, workflow, state_path, events_path, "succeeded"
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal_result(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is WorkflowExecutionPersistenceResult
    state, events = _validate_persistence_result(
        result, workflow, state_path, events_path
    )
    try:
        outcome = classification_routing_function(
            result, workflow, state_path, events_path
        )
    except PersistedTerminalOutcomeClassificationRoutingError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "classification_contract")
        _validate_outcome(outcome, state)
    except PersistedTerminalOutcomeClassificationBridgeCompatibilityError:
        _restore_changed(state_path, events_path, original)
        raise
    return outcome


def _validate_inputs(
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


def _validate_failure(
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    if not (
        value.outcome == "persisted_failure"
        and value.workflow_id == workflow.id
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
        and value.current_step_id == workflow.steps[value.current_step_index - 1].id
        and value.current_employee_id
        == workflow.steps[value.current_step_index - 1].employee
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
        _raise("terminal_contract")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("terminal_contract")


def _validate_terminal_result(
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


def _validate_persistence_result(
    value: WorkflowExecutionPersistenceResult,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
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
        state_bytes, event_bytes = state_path.read_bytes(), events_path.read_bytes()
        state, events = load_strict_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    final_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode()
    if (
        len(state_bytes) != value.state_bytes_written
        or len(final_bytes) != value.event_bytes_appended
        or not event_bytes.endswith(final_bytes)
    ):
        _raise("persistence_contract")
    return state, events


def _validate_outcome(value: object, state: WorkflowExecutionState) -> None:
    success = state.status == "succeeded"
    if not (
        type(value) is PersistedExecutionOutcome
        and value.workflow_id == state.workflow_id
        and value.current_step_id == state.current_step_id
        and value.current_step_index == state.current_step_index
        and value.current_employee_id == state.current_employee_id
        and value.outcome == ("persisted_success" if success else "persisted_failure")
        and value.failure_category == state.last_failure_category
        and ((value.failure_category is None) == success)
    ):
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
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_changed(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise PersistedTerminalOutcomeClassificationBridgeCompatibilityError(
        classification
    ) from None
