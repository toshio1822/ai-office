"""Read-only routing between persisted outcome classification and progression."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeCompatibilityError,
    PersistedExecutionOutcomeError,
    classify_loaded_persisted_execution_outcome,
)
from ai_office.engine.persisted_success_progression import (
    PersistedSuccessProgressionError,
    _decide_loaded_persisted_success_progression,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.storage.workflow_execution_history import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionLoadError,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceTargets,
)

PersistedExecutionOutcomeRoutingClassification = Literal[
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
    workflow: object,
    state_path: object,
    events_path: object,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Classify one persisted target and route only persisted success onward."""
    _validate_inputs(workflow, state_path, events_path)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    original = _capture(state_path, events_path)
    outcome, history = _call_classification(workflow, state_path, events_path, original)
    _validate_outcome_route(outcome)
    if outcome.outcome == "persisted_failure":
        return outcome
    decision = _call_progression(workflow, history, state_path, events_path, original)
    _validate_decision_route(decision)
    return decision


def _validate_inputs(
    workflow: object,
    state_path: object,
    events_path: object,
) -> None:
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("dependency_error")


def _validate_outcome_route(value: object) -> None:
    """Guard only the classified result family and route discriminator."""
    if type(value) is not PersistedExecutionOutcome:
        _raise("classification_contract")
    if type(value.outcome) is not str or value.outcome not in {
        "persisted_success",
        "persisted_failure",
    }:
        _raise("classification_contract")


def _capture(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        return state_path.read_bytes(), events_path.read_bytes()
    except OSError:
        _raise("dependency_error")


def _call_classification(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> tuple[object, LoadedWorkflowExecutionHistory]:
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except WorkflowExecutionLoadError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        raise PersistedExecutionOutcomeCompatibilityError("history_data") from None
    try:
        result = classify_loaded_persisted_execution_outcome(workflow, history)
    except PersistedExecutionOutcomeError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    _reject_changed(state_path, events_path, original)
    if type(history) is not LoadedWorkflowExecutionHistory:
        _raise("classification_contract")
    return result, history


def _call_progression(
    workflow: WorkflowDefinition,
    history: LoadedWorkflowExecutionHistory,
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> object:
    try:
        result = _decide_loaded_persisted_success_progression(workflow, history)
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


def _validate_decision_route(value: object) -> None:
    """Guard only the decision family and routing discriminator."""
    if type(value) is not WorkflowProgressionDecision:
        _raise("progression_contract")
    if type(value.decision) is not str or value.decision not in {
        "prepare_next_step",
        "workflow_complete",
    }:
        _raise("progression_contract")


def _raise(classification: PersistedExecutionOutcomeRoutingClassification) -> None:
    raise PersistedExecutionOutcomeRoutingCompatibilityError(classification) from None
