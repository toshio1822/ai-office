"""Read-only routing between persisted outcome classification and progression."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeError,
    classify_persisted_execution_outcome_reentry,
)
from ai_office.engine.persisted_success_progression import (
    PersistedSuccessProgressionError,
    decide_persisted_success_progression,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory

PersistedExecutionOutcomeRoutingClassification = Literal[
    "outcome_type",
    "outcome_contract",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "classification_contract",
    "progression_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "persisted execution outcome routing inputs are incompatible"
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
ClassificationFunction = Callable[[object, object, object], PersistedExecutionOutcome]
ProgressionFunction = Callable[[object, object, object], WorkflowProgressionDecision]


@dataclass(frozen=True)
class PersistedExecutionOutcomeRoutingFailureDetail:
    classification: PersistedExecutionOutcomeRoutingClassification


class PersistedExecutionOutcomeRoutingError(ValueError):
    """Raised when Phase 38 cannot safely route a persisted outcome."""


class PersistedExecutionOutcomeRoutingCompatibilityError(
    PersistedExecutionOutcomeRoutingError
):
    def __init__(
        self, classification: PersistedExecutionOutcomeRoutingClassification
    ) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedExecutionOutcomeRoutingFailureDetail(classification)


def route_persisted_execution_outcome_reentry(
    outcome: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    classification_function: ClassificationFunction = (
        classify_persisted_execution_outcome_reentry
    ),
    progression_function: ProgressionFunction = decide_persisted_success_progression,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Reclassify once and route only persisted success to Phase 31."""
    _validate_inputs(
        outcome,
        workflow,
        state_path,
        events_path,
        classification_function,
        progression_function,
    )
    assert type(outcome) is PersistedExecutionOutcome
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    original = _capture(state_path, events_path)
    reclassified = _call_classification(
        classification_function, workflow, state_path, events_path, original
    )
    _validate_outcome(reclassified, "classification_contract", workflow)
    if not _same_outcome(outcome, reclassified):
        _raise("classification_contract")
    if outcome.outcome == "persisted_failure":
        return outcome
    decision = _call_progression(
        progression_function, workflow, state_path, events_path, original
    )
    _validate_decision(decision, workflow, outcome)
    return decision


def _validate_inputs(
    outcome: object,
    workflow: object,
    state_path: object,
    events_path: object,
    classification_function: object,
    progression_function: object,
) -> None:
    if type(outcome) is not PersistedExecutionOutcome:
        _raise("outcome_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    _validate_outcome(outcome, "outcome_contract", workflow)
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(classification_function):
        _raise("classification_contract")
    if not callable(progression_function):
        _raise("progression_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("dependency_error")


def _validate_outcome(
    value: object,
    classification: PersistedExecutionOutcomeRoutingClassification,
    workflow: WorkflowDefinition | None = None,
) -> None:
    if type(value) is not PersistedExecutionOutcome:
        _raise(classification)
    valid_identity = (
        all(
            type(item) is str and item
            for item in (
                value.workflow_id,
                value.current_step_id,
                value.current_employee_id,
            )
        )
        and type(value.current_step_index) is int
        and value.current_step_index > 0
    )
    valid = valid_identity and (
        (value.outcome == "persisted_success" and value.failure_category is None)
        or (
            value.outcome == "persisted_failure"
            and value.failure_category in _FAILURE_CATEGORIES
        )
    )
    if not valid:
        _raise(classification)
    if workflow is not None:
        if value.workflow_id != workflow.id or not 1 <= value.current_step_index <= len(
            workflow.steps
        ):
            _raise(classification)
        step = workflow.steps[value.current_step_index - 1]
        if (
            value.current_step_id != step.id
            or value.current_employee_id != step.employee
        ):
            _raise(classification)


def _capture(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        return state_path.read_bytes(), events_path.read_bytes()
    except OSError:
        _raise("dependency_error")


def _call_classification(
    function: ClassificationFunction,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> object:
    try:
        result = function(workflow, state_path, events_path)
    except PersistedExecutionOutcomeError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    _reject_changed(state_path, events_path, original)
    return result


def _call_progression(
    function: ProgressionFunction,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> object:
    try:
        result = function(workflow, state_path, events_path)
    except PersistedSuccessProgressionError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    _reject_changed(state_path, events_path, original)
    return result


def _restore_changed(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        for path, contents in ((state_path, original[0]), (events_path, original[1])):
            try:
                changed = path.read_bytes() != contents
            except FileNotFoundError:
                changed = True
            if changed:
                path.write_bytes(contents)
    except OSError:
        _raise("dependency_rollback")


def _reject_changed(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        changed = (
            state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except OSError:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    if changed:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")


def _same_outcome(
    left: PersistedExecutionOutcome, right: PersistedExecutionOutcome
) -> bool:
    return left == right


def _validate_decision(
    value: object, workflow: WorkflowDefinition, outcome: PersistedExecutionOutcome
) -> None:
    if type(value) is not WorkflowProgressionDecision:
        _raise("progression_contract")
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
        _raise("progression_contract")


def _raise(classification: PersistedExecutionOutcomeRoutingClassification) -> None:
    raise PersistedExecutionOutcomeRoutingCompatibilityError(classification) from None
