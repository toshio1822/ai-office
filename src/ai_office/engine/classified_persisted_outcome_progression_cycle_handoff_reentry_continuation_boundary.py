"""Phase 122 classified persisted-outcome progression cycle reentry continuation boundary."""

# ruff: noqa: E501,E701

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.classified_persisted_outcome_progression_cycle_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleReentryContinuationError,
    route_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary,
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
    "terminal_contract", "progression_contract", "dependency_error",
    "dependency_rollback",
]
Phase115Function = Callable[..., WorkflowProgressionDecision | PersistedExecutionOutcome]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationFailureDetail:
    classification: Classification


class ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationError(ValueError):
    """Raised when Phase 122 cannot safely progress one classified persisted outcome."""


class ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError(
    ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "classified persisted outcome progression cycle reentry continuation inputs are incompatible"
        )
        self.detail = ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationFailureDetail(
            classification
        )


def route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase115_function: Phase115Function = route_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Advance only a valid persisted success through the public Phase 115 boundary."""
    _validate_inputs(result, workflow, state_path, events_path, phase115_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
        terminal_status = "succeeded"
    else:
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

    if type(result) is WorkflowProgressionDecision or result.outcome == "persisted_failure":
        return result

    try:
        progressed = phase115_function(result, workflow, state_path, events_path)
    except ClassifiedPersistedOutcomeProgressionCycleReentryContinuationError as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    if _changed(state_path, events_path, original):
        _restore_or_raise(state_path, events_path, original)
        _raise("progression_contract")
    _validate_progression(progressed, result, workflow)
    return progressed


def _raise(classification: Classification) -> None:
    raise ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError(
        classification
    )


def _validate_inputs(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    dependency: object,
) -> None:
    if type(result) not in {PersistedExecutionOutcome, WorkflowProgressionDecision}:
        _raise("result_type")
    if (
        type(workflow) is not WorkflowDefinition
        or type(workflow.id) is not str
        or type(workflow.name) is not str
        or type(workflow.description) is not str
        or type(workflow.steps) is not list
        or not workflow.steps
    ):
        _raise("workflow_definition")
    if any(
        type(step) is not WorkflowStepDefinition
        or type(step.id) is not str
        or type(step.name) is not str
        or type(step.employee) is not str
        or type(step.instructions) is not str
        for step in workflow.steps
    ):
        _raise("workflow_definition")
    if type(state_path) is not _PATH_TYPE:
        _raise("state_target")
    if type(events_path) is not _PATH_TYPE:
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(dependency):
        _raise("dependency_error")


def _exact_str(value: object) -> bool:
    return type(value) is str


def _exact_optional_str(value: object) -> bool:
    return value is None or type(value) is str


def _validate_common_identity(result: object, workflow: WorkflowDefinition) -> bool:
    return (
        _exact_str(result.workflow_id)
        and _exact_str(result.current_step_id)
        and type(result.current_step_index) is int
        and _exact_str(result.current_employee_id)
        and 1 <= result.current_step_index <= len(workflow.steps)
        and result.workflow_id == workflow.id
        and result.current_step_id == workflow.steps[result.current_step_index - 1].id
        and result.current_employee_id
        == workflow.steps[result.current_step_index - 1].employee
    )


def _validate_success(result: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if (
        type(result.outcome) is not str
        or result.outcome != "persisted_success"
        or result.failure_category is not None
        or not _validate_common_identity(result, workflow)
    ):
        _raise("success_contract")


def _validate_failure(result: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if (
        type(result.outcome) is not str
        or result.outcome != "persisted_failure"
        or type(result.failure_category) is not str
        or result.failure_category not in _FAILURE_CATEGORIES
        or not _validate_common_identity(result, workflow)
    ):
        _raise("failure_contract")


def _validate_completion(
    result: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    if (
        type(result.decision) is not str
        or result.decision != "workflow_complete"
        or not _validate_common_identity(result, workflow)
        or result.current_step_index != len(workflow.steps)
        or result.next_step_id is not None
        or result.next_step_index is not None
        or result.next_employee_id is not None
        or type(result.reason) is not str
        or result.reason != "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_targets(state_path: Path, events_path: Path) -> None:
    for path, classification in ((state_path, "state_target"), (events_path, "event_target")):
        try:
            if not path.is_file():
                _raise(classification)
        except OSError:
            _raise(classification)


def _capture(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    values = []
    for path, classification in ((state_path, "state_target"), (events_path, "event_target")):
        try:
            values.append(path.read_bytes())
        except OSError:
            _raise(classification)
    return values[0], values[1]


def _validate_terminal(
    result: PersistedExecutionOutcome | WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    status: str,
) -> None:
    try:
        state, events = load_strict_terminal_history(workflow, state_path, events_path)
    except TerminalHistoryContractError:
        state, events = _load_phase155_terminal_history(
            result, workflow, state_path, events_path
        )
    final = events[-1]
    expected_failure = result.failure_category if type(result) is PersistedExecutionOutcome else None
    if (
        state.status != status
        or state.workflow_id != result.workflow_id
        or state.current_step_id != result.current_step_id
        or state.current_step_index != result.current_step_index
        or state.current_employee_id != result.current_employee_id
        or state.last_failure_category != expected_failure
        or final.workflow_id != result.workflow_id
        or final.step_id != result.current_step_id
        or final.step_index != result.current_step_index
        or final.employee_id != result.current_employee_id
        or final.next_status != status
        or final.failure_category != expected_failure
    ):
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
    state, events = loaded.state, loaded.events
    if type(state) is not WorkflowExecutionState or type(events) is not tuple:
        _raise("terminal_contract")
    if not _valid_phase155_terminal_history(workflow, state, events):
        _raise("terminal_contract")
    return state, events


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
    decision: object,
    outcome: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
) -> None:
    if type(decision) is not WorkflowProgressionDecision:
        _raise("progression_contract")
    expected_final = outcome.current_step_index == len(workflow.steps)
    expected_decision = "workflow_complete" if expected_final else "prepare_next_step"
    expected_reason = "last_step_succeeded" if expected_final else "next_step_available"
    next_step = None if expected_final else workflow.steps[outcome.current_step_index]
    if (
        type(decision.decision) is not str
        or decision.decision != expected_decision
        or not _validate_common_identity(decision, workflow)
        or decision.workflow_id != outcome.workflow_id
        or decision.current_step_id != outcome.current_step_id
        or decision.current_step_index != outcome.current_step_index
        or decision.current_employee_id != outcome.current_employee_id
        or not _exact_optional_str(decision.next_step_id)
        or (decision.next_step_index is not None and type(decision.next_step_index) is not int)
        or not _exact_optional_str(decision.next_employee_id)
        or decision.next_step_id != (None if next_step is None else next_step.id)
        or decision.next_step_index != (None if next_step is None else outcome.current_step_index + 1)
        or decision.next_employee_id != (None if next_step is None else next_step.employee)
        or type(decision.reason) is not str
        or decision.reason != expected_reason
    ):
        _raise("progression_contract")


def _changed(state_path: Path, events_path: Path, original: tuple[bytes, bytes]) -> bool:
    try:
        return state_path.read_bytes() != original[0] or events_path.read_bytes() != original[1]
    except OSError:
        return True


def _restore_or_raise(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    failed = False
    for path, data in ((state_path, original[0]), (events_path, original[1])):
        try:
            path.write_bytes(data)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _require_unchanged(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_raise(state_path, events_path, original)
        _raise(classification)


def _compensate_dependency_error(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    safe_error: ClassifiedPersistedOutcomeProgressionCycleReentryContinuationError | None,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_raise(state_path, events_path, original)
    if safe_error is not None:
        raise safe_error
    _raise("dependency_error")
