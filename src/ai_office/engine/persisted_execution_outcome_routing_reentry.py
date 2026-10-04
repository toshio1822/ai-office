"""Persisted outcome routing with required Artifact completion before progression."""

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
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)
from ai_office.runtime import binding_of
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
    """Raised when persisted outcome routing cannot proceed safely."""


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
    """Classify persisted outcome, complete required Artifacts, then route success."""
    _validate_inputs(workflow, state_path, events_path)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    original = _capture(state_path, events_path)
    outcome, history = _call_classification(workflow, state_path, events_path, original)
    _validate_outcome_route(outcome)
    _ensure_required_run_artifacts(workflow, state_path, events_path, history)
    if outcome.outcome == "persisted_failure":
        return outcome
    decision = _call_progression(workflow, history, state_path, events_path, original)
    _validate_decision_route(decision)
    return decision


def _ensure_required_run_artifacts(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    history: LoadedWorkflowExecutionHistory,
) -> None:
    """Use the authoritative Manifest to gate Run-bound persisted progression."""
    binding = binding_of(history.state)
    if binding is None:
        return
    manifest_path = state_path.parent / f"{binding.run_id}.manifest.json"
    try:
        manifest_is_present = manifest_path.exists() or manifest_path.is_symlink()
    except OSError:
        _raise("dependency_error")
    if not manifest_is_present:
        if any(step.artifact_content_type is not None for step in workflow.steps):
            _raise("dependency_error")
        return

    try:
        store = WorkflowRunManifestStore(state_path.parent)
        if store.execution_paths(binding.run_id) != (state_path, events_path):
            _raise("dependency_error")
        manifest = load_workflow_run_manifest(store, binding.run_id)
        marker = f"{binding.run_id}.artifact.*.json"
        has_artifact_records = any(state_path.parent.glob(marker))
        has_artifact_policy = any(
            step.artifact_content_type is not None
            for step in manifest.workflow_snapshot.steps
        )
        if manifest.workflow_id != workflow.id:
            _raise("dependency_error")
        if not has_artifact_policy and not has_artifact_records:
            return
        if manifest.digest != binding.manifest_digest:
            _raise("dependency_error")
        from ai_office.engine.artifact import _ensure_required_artifacts_for_history

        _ensure_required_artifacts_for_history(store, binding, history)
    except PersistedExecutionOutcomeRoutingError:
        raise
    except Exception:
        _raise("dependency_error")


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
