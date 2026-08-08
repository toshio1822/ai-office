"""Phase 121 persisted-transition outcome classification cycle handoff reentry continuation boundary."""

# ruff: noqa: E501,E701

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleReentryContinuationError,
    route_persisted_transition_outcome_classification_cycle_reentry_continuation_boundary,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

Classification = Literal[
    "result_type", "workflow_definition", "completion_contract", "failure_contract",
    "state_target", "event_target", "target_conflict", "terminal_contract",
    "persistence_contract", "outcome_contract", "dependency_error", "dependency_rollback",
]
Phase114Function = Callable[..., PersistedExecutionOutcome]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationFailureDetail:
    classification: Classification


class PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationError(ValueError):
    """Raised when Phase 114 cannot safely classify a persisted transition."""


class PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError(PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationError):
    def __init__(self, classification: Classification) -> None:
        super().__init__("persisted transition outcome classification cycle continuation inputs are incompatible")
        self.detail = PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationFailureDetail(classification)


def route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary(
    result: object, workflow: object, state_path: object, events_path: object, *,
    phase114_function: Phase114Function = route_persisted_transition_outcome_classification_cycle_reentry_continuation_boundary,
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    _top_level(result, workflow, state_path, events_path, phase114_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is WorkflowProgressionDecision:
        _stop(result, workflow, state_path, events_path, "succeeded")
        return result
    if type(result) is PersistedExecutionOutcome:
        _stop(result, workflow, state_path, events_path, "failed")
        return result
    _result_fields(result, state_path, events_path)
    _targets(state_path, events_path)
    original = _capture(state_path, events_path)
    state = _persistence(result, workflow, state_path, events_path)
    try:
        value = phase114_function(result, workflow, state_path, events_path)
    except PersistedTransitionOutcomeClassificationCycleReentryContinuationError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        if _restore_if_changed(state_path, events_path, original):
            _raise("outcome_contract")
        _outcome(value, state)
    except PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError:
        raise
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("outcome_contract")
    return value


def _top_level(result: object, workflow: object, state: object, events: object, function: object) -> None:
    if type(result) not in (WorkflowExecutionPersistenceResult, WorkflowProgressionDecision, PersistedExecutionOutcome):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition or not _workflow(workflow):
        _raise("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _raise("state_target")
    if type(events) is not _PATH_TYPE:
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("persistence_contract")


def _workflow(workflow: WorkflowDefinition) -> bool:
    return (type(workflow.id) is str and type(workflow.name) is str and type(workflow.description) is str
            and type(workflow.steps) is list and bool(workflow.steps)
            and all(type(step) is WorkflowStepDefinition and type(step.id) is str and type(step.name) is str
                    and type(step.employee) is str and type(step.instructions) is str for step in workflow.steps))


def _result_fields(result: object, state: object, events: object) -> None:
    if type(result) is not WorkflowExecutionPersistenceResult:
        _raise("result_type")
    if result.state_path is not state or result.events_path is not events:
        _raise("persistence_contract")
    if type(result.state_bytes_written) is not int or type(result.event_bytes_appended) is not int:
        _raise("persistence_contract")
    if result.state_bytes_written <= 0 or result.event_bytes_appended <= 0:
        _raise("persistence_contract")


def _targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file(): _raise("state_target")
    except OSError: _raise("state_target")
    try:
        if not events.is_file(): _raise("event_target")
    except OSError: _raise("event_target")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try: state_bytes = state.read_bytes()
    except OSError: _raise("state_target")
    try: event_bytes = events.read_bytes()
    except OSError: _raise("event_target")
    return state_bytes, event_bytes


def _persistence(result: WorkflowExecutionPersistenceResult, workflow: WorkflowDefinition, state_path: Path, events_path: Path):
    try:
        state, history = load_strict_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    state_bytes, event_bytes = _capture(state_path, events_path)
    if type(state.current_step_index) is not int or state.current_step_index < 2:
        _raise("persistence_contract")
    terminal_bytes = serialize_runtime_step_event_jsonl(history[-1]).encode()
    if (len(state_bytes) != result.state_bytes_written or len(event_bytes) < len(terminal_bytes)
            or len(terminal_bytes) != result.event_bytes_appended or not event_bytes.endswith(terminal_bytes)
            or state_bytes != serialize_workflow_execution_state_json(state).encode()):
        _raise("persistence_contract")
    return state


def _stop(value: WorkflowProgressionDecision | PersistedExecutionOutcome, workflow: WorkflowDefinition, state: Path, events: Path, status: Literal["succeeded", "failed"]) -> None:
    if type(value) is WorkflowProgressionDecision:
        final = workflow.steps[-1]
        if not (type(value.decision) is str and value.decision == "workflow_complete"
                and type(value.workflow_id) is str and value.workflow_id == workflow.id
                and type(value.current_step_id) is str and value.current_step_id == final.id
                and type(value.current_step_index) is int and value.current_step_index == len(workflow.steps)
                and type(value.current_employee_id) is str and value.current_employee_id == final.employee
                and value.next_step_id is None and value.next_step_index is None and value.next_employee_id is None
                and type(value.reason) is str and value.reason == "last_step_succeeded"):
            _raise("completion_contract")
    else:
        if not (type(value.outcome) is str and value.outcome == "persisted_failure"
                and type(value.current_step_index) is int and 1 <= value.current_step_index <= len(workflow.steps)
                and type(value.failure_category) is str and value.failure_category in _FAILURE_CATEGORIES):
            _raise("failure_contract")
        step = workflow.steps[value.current_step_index - 1]
        if not (type(value.workflow_id) is str and value.workflow_id == workflow.id
                and type(value.current_step_id) is str and value.current_step_id == step.id
                and type(value.current_employee_id) is str and value.current_employee_id == step.employee):
            _raise("failure_contract")
    _targets(state, events)
    try: persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError): _raise("terminal_contract")
    if persisted.status != status or (persisted.workflow_id, persisted.current_step_id, persisted.current_step_index, persisted.current_employee_id) != (value.workflow_id, value.current_step_id, value.current_step_index, value.current_employee_id):
        _raise("terminal_contract")
    if type(value) is PersistedExecutionOutcome and persisted.last_failure_category != value.failure_category:
        _raise("terminal_contract")


def _outcome(value: object, state: object) -> None:
    if type(value) is not PersistedExecutionOutcome: _raise("outcome_contract")
    expected = "persisted_success" if state.status == "succeeded" else "persisted_failure"
    if not (type(value.outcome) is str and value.outcome == expected and type(value.workflow_id) is str and value.workflow_id == state.workflow_id
            and type(value.current_step_id) is str and value.current_step_id == state.current_step_id
            and type(value.current_step_index) is int and value.current_step_index == state.current_step_index
            and type(value.current_employee_id) is str and value.current_employee_id == state.current_employee_id
            and ((value.failure_category is None) if state.status == "succeeded" else (type(value.failure_category) is str and value.failure_category in _FAILURE_CATEGORIES))
            and value.failure_category == state.last_failure_category):
        _raise("outcome_contract")


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> bool:
    changed = False
    try: changed = not state.is_file() or state.read_bytes() != original[0]
    except OSError: changed = True
    try: changed = changed or not events.is_file() or events.read_bytes() != original[1]
    except OSError: changed = True
    if not changed:
        return False
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try: path.write_bytes(contents)
        except OSError: failed = True
    if failed: _raise("dependency_rollback")
    return True


def _changed(path: Path, before: bytes) -> bool:
    try: return not path.is_file() or path.read_bytes() != before
    except OSError: return True


def _raise(classification: Classification) -> None:
    raise PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError(classification) from None
