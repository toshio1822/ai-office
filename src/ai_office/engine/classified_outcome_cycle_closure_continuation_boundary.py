"""Phase 101 classified-outcome cycle closure continuation boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation import (
    ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationError,
    route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation,
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

Classification = Literal[
    "result_type", "workflow_definition", "success_contract", "failure_contract",
    "completion_contract", "state_target", "event_target", "target_conflict",
    "terminal_contract", "outcome_contract", "progression_contract",
    "dependency_error", "dependency_rollback",
]
Phase94Function = Callable[..., WorkflowProgressionDecision | PersistedExecutionOutcome]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class ClassifiedOutcomeCycleClosureContinuationFailureDetail:
    classification: Classification


class ClassifiedOutcomeCycleClosureContinuationError(ValueError):
    """Raised when Phase 101 cannot safely close one classified outcome cycle."""


class ClassifiedOutcomeCycleClosureContinuationCompatibilityError(
    ClassifiedOutcomeCycleClosureContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("classified outcome cycle closure continuation inputs are incompatible")
        self.detail = ClassifiedOutcomeCycleClosureContinuationFailureDetail(classification)


def route_classified_outcome_cycle_closure_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase94_function: Phase94Function = route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    _validate_inputs(result, workflow, state_path, events_path, phase94_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
        _validate_targets(state_path, events_path)
        original = _capture(state_path, events_path)
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is PersistedExecutionOutcome
    if result.outcome == "persisted_success":
        _validate_success(result, workflow)
        terminal_status = "succeeded"
    elif result.outcome == "persisted_failure":
        _validate_failure(result, workflow)
        terminal_status = "failed"
    else:
        _raise("result_type")

    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    _validate_terminal(result, workflow, state_path, events_path, terminal_status)
    _require_unchanged(state_path, events_path, original, "terminal_contract")
    if result.outcome == "persisted_failure":
        return result

    try:
        value = phase94_function(result, workflow, state_path, events_path)
    except ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")

    if _restore_if_changed(state_path, events_path, original):
        _raise("progression_contract")
    _validate_progression(value, result, workflow)
    return value


def _validate_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    dependency: object,
) -> None:
    if type(result) not in (PersistedExecutionOutcome, WorkflowProgressionDecision):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition or not _workflow_fields(workflow):
        _raise("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _raise("state_target")
    if type(events) is not _PATH_TYPE:
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(dependency):
        _raise("dependency_error")


def _workflow_fields(workflow: WorkflowDefinition) -> bool:
    return (
        type(workflow.id) is str
        and type(workflow.name) is str
        and type(workflow.description) is str
        and type(workflow.steps) is list
        and bool(workflow.steps)
        and all(
            type(step) is WorkflowStepDefinition
            and type(step.id) is str
            and type(step.name) is str
            and type(step.employee) is str
            and type(step.instructions) is str
            for step in workflow.steps
        )
    )


def _validate_success(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if not (
        type(value.outcome) is str
        and value.outcome == "persisted_success"
        and value.failure_category is None
    ):
        _raise("success_contract")
    _validate_outcome_identity(value, workflow, "success_contract")


def _validate_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if not (
        type(value.outcome) is str
        and value.outcome == "persisted_failure"
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _raise("failure_contract")
    _validate_outcome_identity(value, workflow, "failure_contract")


def _validate_outcome_identity(
    value: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    classification: Classification,
) -> None:
    if not (
        type(value.workflow_id) is str
        and value.workflow_id == workflow.id
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
    ):
        _raise(classification)
    step = workflow.steps[value.current_step_index - 1]
    if not (
        type(value.current_step_id) is str
        and value.current_step_id == step.id
        and type(value.current_employee_id) is str
        and value.current_employee_id == step.employee
    ):
        _raise(classification)


def _validate_completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition) -> None:
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
    value: PersistedExecutionOutcome | WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    status: Literal["succeeded", "failed"],
) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except TerminalHistoryContractError as error:
        if isinstance(error.__cause__, WorkflowExecutionLoadError):
            # A real storage I/O/load failure on the strict path must remain
            # terminal_contract: it is not a strict-contract violation that the
            # Phase-155 fallback may repair, so we must not retry via the
            # public loader (a transient read failure must not be papered over
            # by entering compatibility fallback).
            _raise("terminal_contract")
        persisted, _ = _load_phase155_terminal_history(value, workflow, state, events)
    except OSError:
        _raise("terminal_contract")
    if (
        persisted.status != status
        or (
            persisted.workflow_id,
            persisted.current_step_id,
            persisted.current_step_index,
            persisted.current_employee_id,
        )
        != (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.current_employee_id,
        )
    ):
        _raise("terminal_contract")
    if type(value) is PersistedExecutionOutcome and persisted.last_failure_category != value.failure_category:
        _raise("terminal_contract")


def _load_phase155_terminal_history(
    result: PersistedExecutionOutcome | WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    """Load Phase-155-compatible terminal history through the public loader.

    The shared strict terminal contract rejects succeeded predecessor empty
    ``output_text`` once the terminal step is the final workflow step.  This
    local bounded fallback reloads the same persisted history through the
    public storage loader and revalidates it with the Phase-155 provenance
    rules below.  It is used only after the strict loader raises
    ``TerminalHistoryContractError`` and only for exact
    ``PersistedExecutionOutcome`` inputs with exact built-in int
    ``current_step_index >= 6``.
    """
    if not (
        type(result) is PersistedExecutionOutcome
        and type(result.current_step_index) is int
        and result.current_step_index >= 6
    ):
        _raise("terminal_contract")
    try:
        loaded = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("terminal_contract")
    state, loaded_events = loaded.state, loaded.events
    if type(state) is not WorkflowExecutionState or type(loaded_events) is not tuple:
        _raise("terminal_contract")
    if not _valid_phase155_terminal_history(workflow, state, loaded_events):
        _raise("terminal_contract")
    return state, loaded_events


def _valid_phase155_terminal_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> bool:
    """Validate Phase-155 terminal history as a strict mirror, relaxing only
    succeeded predecessor empty ``output_text``."""
    if state.status not in {"succeeded", "failed"} or state.workflow_id != workflow.id:
        return False
    index = state.current_step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current = workflow.steps[index - 1]
    if (
        type(state.current_step_id) is not str
        or state.current_step_id != current.id
        or type(state.current_employee_id) is not str
        or state.current_employee_id != current.employee
    ):
        return False
    if state.status == "succeeded":
        expected_completed = tuple(step.id for step in workflow.steps[:index])
    else:
        expected_completed = tuple(step.id for step in workflow.steps[: index - 1])
    if state.completed_step_ids != expected_completed:
        return False
    prior_steps = workflow.steps[: index - 1]
    if len(events) != len(prior_steps) + 1:
        return False
    if any(type(event) is not RuntimeStepEvent for event in events):
        return False
    for position, (event, step) in enumerate(
        zip(events[:-1], prior_steps, strict=True), 1
    ):
        if not _valid_phase155_predecessor(event, step, position, state):
            return False
    return _valid_phase155_terminal_event(events[-1], state)


def _valid_phase155_predecessor(
    event: RuntimeStepEvent,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
) -> bool:
    """Validate one succeeded predecessor with empty ``output_text`` allowed.

    No provider or request-ID validation is added here: the shared strict
    history does not gate those fields, so this local fallback must not invent
    a new provider/request policy.
    """
    return (
        event.event_type == "step_succeeded"
        and event.workflow_id == state.workflow_id
        and event.step_id == step.id
        and event.step_index == position
        and event.employee_id == step.employee
        and event.previous_status == "running"
        and event.next_status == "succeeded"
        and event.failure_category is None
        and type(event.response_id) is str
        and event.response_id != ""
        and type(event.output_text) is str
        and event.message is None
    )


def _valid_phase155_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
) -> bool:
    """Validate the terminal event with strict non-empty terminal success output."""
    base = (
        event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
    )
    if state.status == "succeeded":
        return (
            base
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and state.last_failure_category is None
            and event.failure_category is None
            and type(event.response_id) is str
            and event.response_id != ""
            and type(event.output_text) is str
            and event.output_text != ""
            and event.message is None
        )
    return (
        base
        and event.event_type == "step_failed"
        and event.next_status == "failed"
        and type(state.last_failure_category) is str
        and state.last_failure_category in _FAILURE_CATEGORIES
        and event.failure_category == state.last_failure_category
        and event.response_id is None
        and event.output_text is None
        and isinstance(event.message, str)
    )


def _validate_progression(
    value: object,
    outcome: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
) -> None:
    if type(value) is not WorkflowProgressionDecision:
        _raise("progression_contract")
    if not (
        type(value.decision) is str
        and type(value.workflow_id) is str
        and type(value.current_step_id) is str
        and type(value.current_step_index) is int
        and type(value.current_employee_id) is str
        and type(value.reason) is str
        and (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.current_employee_id,
        )
        == (
            outcome.workflow_id,
            outcome.current_step_id,
            outcome.current_step_index,
            outcome.current_employee_id,
        )
    ):
        _raise("progression_contract")
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
        _raise("progression_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> bool:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return False
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")
    return True


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _restore_if_changed(state, events, original):
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise ClassifiedOutcomeCycleClosureContinuationCompatibilityError(classification) from None
