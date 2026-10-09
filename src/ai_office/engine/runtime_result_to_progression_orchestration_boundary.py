"""Post-runtime durable commit and canonical persisted routing."""

# ruff: noqa: E501,E701,I001

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeError,
    validate_loaded_persisted_execution_history,
)
from ai_office.engine.persisted_execution_outcome_routing_reentry import (
    PersistedExecutionOutcomeRoutingError,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    binding_of,
)
from ai_office.runtime.executed_step_transition_persistence import (
    ExecutedStepTransitionPersistenceError,
    persist_executed_step_transition,
)
from ai_office.storage import (
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceRollbackError,
    WorkflowExecutionPersistenceTargets,
    WorkflowExecutionPersistenceResult,
    load_workflow_execution_history,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "persistence_contract",
    "routing_contract",
    "dependency_error",
    "committed_mutation",
    "rollback_failure",
]
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class RuntimeResultToProgressionOrchestrationBoundaryFailureDetail:
    """Safe classification for one Phase 172 orchestration failure."""

    classification: Classification


class RuntimeResultToProgressionOrchestrationBoundaryError(ValueError):
    """Base error for the Phase 172 boundary."""


class RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError(
    RuntimeResultToProgressionOrchestrationBoundaryError
):
    """Raised when a post-runtime orchestration cannot safely complete."""

    def __init__(self, classification: Classification) -> None:
        super().__init__("post-runtime orchestration boundary inputs are incompatible")
        self.detail = RuntimeResultToProgressionOrchestrationBoundaryFailureDetail(
            classification
        )


def route_runtime_result_to_progression_orchestration_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Persist one runtime result and route the committed targets once.

    The persisted-history owner validates the running state and predecessor
    transcript. The transition owner checks the exact runtime result and commits
    the terminal state/event pair. This function preserves that committed pair
    while the canonical persisted route classifies and progresses it.
    """
    _check_inputs(
        result,
        workflow,
        state_path,
        events_path,
    )
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    assert type(result) in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
    )

    precommit = _capture_targets(state_path, events_path)
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(
                state_path,
                events_path,
                binding=binding_of(result),
            ),
            require_terminal_evidence=True,
        )
        validate_loaded_persisted_execution_history(
            workflow,
            history,
            allow_unstarted_running_current_step=True,
            require_execution_provenance=True,
        )
        if history.state.status != "running" or history.state.last_failure_category is not None:
            _fail("persistence_contract")
        value = persist_executed_step_transition(
            result, state_path, events_path
        )
    except (
        ExecutedStepTransitionPersistenceError,
        WorkflowExecutionLoadError,
        WorkflowExecutionPersistenceRollbackError,
        PersistedExecutionOutcomeError,
        RuntimeResultToProgressionOrchestrationBoundaryError,
    ):
        _restore_if_changed(state_path, events_path, precommit)
        raise
    except Exception:
        _restore_if_changed(state_path, events_path, precommit)
        _fail("dependency_error")

    if type(value) is not WorkflowExecutionPersistenceResult:
        _restore_if_changed(state_path, events_path, precommit)
        _fail("persistence_contract")

    committed = _capture_targets(state_path, events_path)
    try:
        routed = route_persisted_execution_outcome_reentry(
            workflow, state_path, events_path
        )
    except (
        WorkflowExecutionLoadError,
        PersistedExecutionOutcomeRoutingError,
        PersistedExecutionOutcomeError,
    ) as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_routed_result(routed):
        _restore_if_changed(state_path, events_path, committed)
        _fail("routing_contract")
    _require_unchanged(state_path, events_path, committed, "committed_mutation")
    return routed


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
) -> None:
    if type(result) not in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
    ):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition:
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")


def _valid_routed_result(value: object) -> bool:
    """Guard the canonical persisted-route result family and discriminator."""
    if type(value) is PersistedExecutionOutcome:
        return value.outcome == "persisted_failure"
    if type(value) is WorkflowProgressionDecision:
        return value.decision in {"prepare_next_step", "workflow_complete"}
    return False


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _fail("dependency_error")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]):
        _fail("rollback_failure")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _fail(classification: Classification) -> None:
    raise RuntimeResultToProgressionOrchestrationBoundaryCompatibilityError(
        classification
    ) from None
