"""Bridge an exact Phase 44 outcome to the existing Phase 38 routing boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_execution_outcome_routing_reentry import (
    PersistedExecutionOutcomeRoutingError,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
)

RoutingClassification = Literal[
    "result_type",
    "workflow_definition",
    "completion_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "outcome_contract",
    "terminal_contract",
    "routing_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "classified persisted outcome routing inputs are incompatible"
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
RoutingFunction = Callable[..., WorkflowProgressionDecision | PersistedExecutionOutcome]


@dataclass(frozen=True)
class ClassifiedPersistedOutcomeRoutingFailureDetail:
    classification: RoutingClassification


class ClassifiedPersistedOutcomeRoutingError(ValueError):
    pass


class ClassifiedPersistedOutcomeRoutingCompatibilityError(
    ClassifiedPersistedOutcomeRoutingError
):
    def __init__(self, classification: RoutingClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ClassifiedPersistedOutcomeRoutingFailureDetail(classification)


def route_classified_persisted_outcome_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    routing_function: RoutingFunction = route_persisted_execution_outcome_reentry,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact classified terminal outcome through Phase 38 once."""
    _validate_inputs(result, workflow, state_path, events_path, routing_function)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _require_unchanged(state_path, events_path, original)
        return result

    assert type(result) is PersistedExecutionOutcome
    _validate_outcome(result, workflow)
    history = _load_terminal_history(workflow, state_path, events_path)
    if not _matches_state(result, history.state):
        _raise("terminal_contract")
    try:
        value = routing_function(result, workflow, state_path, events_path)
    except PersistedExecutionOutcomeRoutingError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    _require_unchanged(state_path, events_path, original)
    _validate_return(value, result, workflow)
    return value


def _validate_inputs(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (PersistedExecutionOutcome, WorkflowProgressionDecision):
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
        _raise("routing_contract")


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


def _validate_outcome(
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    valid = (
        value.outcome in {"persisted_success", "persisted_failure"}
        and value.workflow_id == workflow.id
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
        and value.current_step_id == workflow.steps[value.current_step_index - 1].id
        and value.current_employee_id
        == workflow.steps[value.current_step_index - 1].employee
        and (
            (value.outcome == "persisted_success" and value.failure_category is None)
            or (
                value.outcome == "persisted_failure"
                and value.failure_category in _FAILURE_CATEGORIES
            )
        )
    )
    if not valid:
        _raise("outcome_contract")


def _load_terminal_history(workflow: WorkflowDefinition, state: Path, events: Path):
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
    except (
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("terminal_contract")
    _validate_terminal_history(workflow, history.state, history.events)
    return history


def _validate_terminal_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> None:
    if state.status not in {"succeeded", "failed"} or state.workflow_id != workflow.id:
        _raise("terminal_contract")
    if not 1 <= state.current_step_index <= len(workflow.steps):
        _raise("terminal_contract")
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _raise("terminal_contract")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        completed = [positions[item] for item in state.completed_step_ids]
    except KeyError:
        _raise("terminal_contract")
    expected = (
        state.current_step_index
        if state.status == "succeeded"
        else state.current_step_index - 1
    )
    compressed = tuple(
        value
        for index, value in enumerate(completed)
        if index == 0 or completed[index - 1] != value
    )
    if compressed != tuple(range(1, expected + 1)) or not events:
        _raise("terminal_contract")
    prior_ids: list[str] = []
    expected_prior = (
        state.completed_step_ids[:-1]
        if state.status == "succeeded"
        else state.completed_step_ids
    )
    for event in events[:-1]:
        if not (
            event.workflow_id == state.workflow_id
            and event.step_id in positions
            and event.step_index == positions[event.step_id]
            and event.employee_id == workflow.steps[event.step_index - 1].employee
            and event.step_index < state.current_step_index
            and event.step_id != state.current_step_id
            and event.event_type == "step_succeeded"
            and event.previous_status == "running"
            and event.next_status == "succeeded"
        ):
            _raise("terminal_contract")
        prior_ids.append(event.step_id)
    if tuple(prior_ids) != expected_prior:
        _raise("terminal_contract")
    event = events[-1]
    base = (
        event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
    )
    valid = (
        (
            base
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and state.last_failure_category is None
            and event.failure_category is None
            and event.message is None
        )
        if state.status == "succeeded"
        else (
            base
            and event.event_type == "step_failed"
            and event.next_status == "failed"
            and state.last_failure_category in _FAILURE_CATEGORIES
            and event.failure_category == state.last_failure_category
            and event.response_id is None
            and event.output_text is None
            and isinstance(event.message, str)
        )
    )
    if not valid:
        _raise("terminal_contract")


def _matches_state(value: PersistedExecutionOutcome, state: object) -> bool:
    return (
        value.workflow_id == state.workflow_id
        and value.current_step_id == state.current_step_id
        and value.current_step_index == state.current_step_index
        and value.current_employee_id == state.current_employee_id
        and value.outcome
        == ("persisted_success" if state.status == "succeeded" else "persisted_failure")
        and value.failure_category == state.last_failure_category
    )


def _validate_return(
    value: object, outcome: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    if outcome.outcome == "persisted_failure":
        if value is not outcome:
            _raise("routing_contract")
        return
    if type(value) is not WorkflowProgressionDecision:
        _raise("routing_contract")
    base = (
        value.workflow_id == outcome.workflow_id
        and value.current_step_id == outcome.current_step_id
        and value.current_step_index == outcome.current_step_index
        and value.current_employee_id == outcome.current_employee_id
    )
    if outcome.current_step_index == len(workflow.steps):
        valid = (
            base
            and value.decision == "workflow_complete"
            and value.next_step_id is None
            and value.next_step_index is None
            and value.next_employee_id is None
            and value.reason == "last_step_succeeded"
        )
    else:
        next_step = workflow.steps[outcome.current_step_index]
        valid = (
            base
            and value.decision == "prepare_next_step"
            and value.next_step_id == next_step.id
            and value.next_step_index == outcome.current_step_index + 1
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
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_changed(state, events, original)
        _raise("dependency_error")


def _raise(classification: RoutingClassification) -> None:
    raise ClassifiedPersistedOutcomeRoutingCompatibilityError(classification) from None
