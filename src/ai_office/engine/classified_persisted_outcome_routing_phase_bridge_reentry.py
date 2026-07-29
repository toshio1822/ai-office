"""Bridge exact Phase 58 outcomes to the existing Phase 52 routing bridge."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.classified_persisted_outcome_routing_bridge_reentry import (
    ClassifiedPersistedOutcomeRoutingBridgeError,
    route_classified_persisted_outcome_bridge_reentry,
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

Classification = Literal[
    "result_type",
    "workflow_definition",
    "completion_contract",
    "outcome_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "routing_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase52Function = Callable[
    [object, object, object, object],
    WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())
_ERROR_MESSAGE = (
    "classified persisted outcome routing phase bridge inputs are incompatible"
)


@dataclass(frozen=True)
class ClassifiedPersistedOutcomeRoutingPhaseBridgeFailureDetail:
    """Safe category for a Phase 59 compatibility rejection."""

    classification: Classification


class ClassifiedPersistedOutcomeRoutingPhaseBridgeError(ValueError):
    """Raised when Phase 59 cannot safely route its supplied result."""


class ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError(
    ClassifiedPersistedOutcomeRoutingPhaseBridgeError
):
    """A public, detail-safe Phase 59 compatibility error."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ClassifiedPersistedOutcomeRoutingPhaseBridgeFailureDetail(
            classification
        )


def route_classified_persisted_outcome_routing_phase_bridge_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase52_function: Phase52Function = (
        route_classified_persisted_outcome_bridge_reentry
    ),
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact Phase 58 result through Phase 52, or stop completion."""
    _validate_inputs(result, workflow, state_path, events_path, phase52_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
    else:
        _validate_outcome(result, workflow)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is PersistedExecutionOutcome
    expected_status = "succeeded" if result.outcome == "persisted_success" else "failed"
    _validate_terminal(result, workflow, state_path, events_path, expected_status)
    try:
        routed = phase52_function(result, workflow, state_path, events_path)
    except ClassifiedPersistedOutcomeRoutingBridgeError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "routing_contract")
        _validate_return(routed, result, workflow)
    except ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError:
        _restore_changed(state_path, events_path, original)
        raise
    return routed


def _validate_inputs(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (PersistedExecutionOutcome, WorkflowProgressionDecision):
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
        _raise("routing_contract")


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
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and type(value.reason) is str
        and value.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_outcome(
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    valid_identity = (
        type(value.workflow_id) is str
        and value.workflow_id == workflow.id
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
        and type(value.current_step_id) is str
        and value.current_step_id == workflow.steps[value.current_step_index - 1].id
        and type(value.current_employee_id) is str
        and value.current_employee_id
        == workflow.steps[value.current_step_index - 1].employee
    )
    if not valid_identity or type(value.outcome) is not str:
        _raise("outcome_contract")
    if value.outcome == "persisted_success":
        valid = value.failure_category is None
    elif value.outcome == "persisted_failure":
        valid = (
            type(value.failure_category) is str
            and value.failure_category in _FAILURE_CATEGORIES
        )
    else:
        valid = False
    if not valid:
        _raise("outcome_contract")


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
        return state_bytes, events.read_bytes()
    except OSError:
        _raise("event_target")


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


def _validate_return(
    value: object,
    outcome: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
) -> None:
    if outcome.outcome == "persisted_failure":
        if value is not outcome:
            _raise("routing_contract")
        return
    if type(value) is not WorkflowProgressionDecision:
        _raise("routing_contract")
    if not (
        type(value.workflow_id) is str
        and value.workflow_id == outcome.workflow_id
        and type(value.current_step_id) is str
        and value.current_step_id == outcome.current_step_id
        and type(value.current_step_index) is int
        and value.current_step_index == outcome.current_step_index
        and type(value.current_employee_id) is str
        and value.current_employee_id == outcome.current_employee_id
        and type(value.decision) is str
        and type(value.reason) is str
    ):
        _raise("routing_contract")
    if outcome.current_step_index == len(workflow.steps):
        valid = (
            value.decision == "workflow_complete"
            and value.next_step_id is None
            and value.next_step_index is None
            and value.next_employee_id is None
            and value.reason == "last_step_succeeded"
        )
    else:
        next_step = workflow.steps[outcome.current_step_index]
        valid = (
            value.decision == "prepare_next_step"
            and type(value.next_step_id) is str
            and value.next_step_id == next_step.id
            and type(value.next_step_index) is int
            and value.next_step_index == outcome.current_step_index + 1
            and type(value.next_employee_id) is str
            and value.next_employee_id == next_step.employee
            and value.reason == "next_step_available"
        )
    if not valid:
        _raise("routing_contract")


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
    raise ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError(
        classification
    ) from None
