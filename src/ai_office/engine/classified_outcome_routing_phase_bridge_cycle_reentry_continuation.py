"""Continue one exact Phase 86 classified outcome through Phase 80."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.classified_outcome_routing_phase_bridge_cycle_continuation import (
    ClassifiedOutcomeRoutingPhaseBridgeContinuationError,
    route_classified_outcome_routing_phase_bridge_cycle_continuation,
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
    "result_type", "workflow_definition", "success_contract", "failure_contract",
    "completion_contract", "state_target", "event_target", "target_conflict",
    "terminal_contract", "outcome_contract", "progression_contract", "dependency_error",
    "dependency_rollback",
]
Phase80Function = Callable[..., WorkflowProgressionDecision | PersistedExecutionOutcome]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationFailureDetail:
    classification: Classification


class ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationError(ValueError):
    """Raised when Phase 87 cannot safely continue the supplied result."""


class ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError(
    ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("classified outcome routing phase bridge cycle reentry continuation inputs are incompatible")
        self.detail = ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationFailureDetail(classification)


def route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase80_function: Phase80Function = route_classified_outcome_routing_phase_bridge_cycle_continuation,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    _inputs(result, workflow, state_path, events_path, phase80_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is WorkflowProgressionDecision:
        _completion(result, workflow)
    else:
        assert type(result) is PersistedExecutionOutcome
        if result.outcome == "persisted_success":
            _success(result, workflow)
        else:
            _failure(result, workflow)
    _targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _terminal(result, workflow, state_path, events_path, "succeeded")
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    assert type(result) is PersistedExecutionOutcome
    status = "succeeded" if result.outcome == "persisted_success" else "failed"
    _terminal(result, workflow, state_path, events_path, status)
    if result.outcome == "persisted_failure":
        _unchanged(state_path, events_path, original, "terminal_contract")
        return result
    try:
        value = phase80_function(result, workflow, state_path, events_path)
    except ClassifiedOutcomeRoutingPhaseBridgeContinuationError as error:
        _restore(state_path, events_path, original)
        raise error
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _unchanged(state_path, events_path, original, "outcome_contract")
        _progression(value, result, workflow)
    except ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore(state_path, events_path, original)
        raise
    return value


def _inputs(result: object, workflow: object, state: object, events: object, function: object) -> None:
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
        _raise("dependency_error")


def _identity(value: PersistedExecutionOutcome, workflow: WorkflowDefinition, category: Classification) -> None:
    if not (type(value.workflow_id) is str and value.workflow_id == workflow.id
            and type(value.current_step_index) is int and 1 <= value.current_step_index <= len(workflow.steps)):
        _raise(category)
    step = workflow.steps[value.current_step_index - 1]
    if not (type(value.current_step_id) is str and value.current_step_id == step.id
            and type(value.current_employee_id) is str and value.current_employee_id == step.employee):
        _raise(category)


def _success(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if not (type(value.outcome) is str and value.outcome == "persisted_success" and value.failure_category is None):
        _raise("success_contract")
    _identity(value, workflow, "success_contract")


def _failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if not (type(value.outcome) is str and value.outcome == "persisted_failure"
            and type(value.failure_category) is str and value.failure_category in _FAILURE_CATEGORIES):
        _raise("failure_contract")
    _identity(value, workflow, "failure_contract")


def _completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition) -> None:
    final = workflow.steps[-1]
    if not (type(value.decision) is str and value.decision == "workflow_complete"
            and type(value.workflow_id) is str and value.workflow_id == workflow.id
            and type(value.current_step_id) is str and value.current_step_id == final.id
            and type(value.current_step_index) is int and value.current_step_index == len(workflow.steps)
            and type(value.current_employee_id) is str and value.current_employee_id == final.employee
            and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None
            and type(value.reason) is str and value.reason == "last_step_succeeded"):
        _raise("completion_contract")


def _targets(state: Path, events: Path) -> None:
    for path, category in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file():
                _raise(category)
        except OSError:
            _raise(category)


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


def _terminal(value: object, workflow: WorkflowDefinition, state: Path, events: Path, status: str) -> None:
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


def _progression(value: object, outcome: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if type(value) is not WorkflowProgressionDecision:
        _raise("progression_contract")
    if not (type(value.decision) is str and type(value.workflow_id) is str and type(value.current_step_id) is str
            and type(value.current_step_index) is int and type(value.current_employee_id) is str
            and type(value.reason) is str and (value.workflow_id, value.current_step_id, value.current_step_index,
                                                value.current_employee_id) == (outcome.workflow_id,
                                                                                outcome.current_step_id,
                                                                                outcome.current_step_index,
                                                                                outcome.current_employee_id)):
        _raise("progression_contract")
    if outcome.current_step_index == len(workflow.steps):
        valid = (value.decision == "workflow_complete" and value.next_step_id is None
                 and value.next_step_index is None and value.next_employee_id is None
                 and value.reason == "last_step_succeeded")
    else:
        step = workflow.steps[outcome.current_step_index]
        valid = (value.decision == "prepare_next_step" and type(value.next_step_id) is str
                 and value.next_step_id == step.id and type(value.next_step_index) is int
                 and value.next_step_index == outcome.current_step_index + 1
                 and type(value.next_employee_id) is str and value.next_employee_id == step.employee
                 and value.reason == "next_step_available")
    if not valid:
        _raise("progression_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _unchanged(state: Path, events: Path, original: tuple[bytes, bytes], category: Classification) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore(state, events, original)
        _raise(category)


def _raise(classification: Classification) -> None:
    raise ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError(classification) from None
