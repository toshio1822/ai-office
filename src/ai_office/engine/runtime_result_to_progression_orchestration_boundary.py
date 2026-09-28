"""Post-runtime durable commit → canonical persisted routing boundary."""

# ruff: noqa: E501,E701,I001

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeError,
)
from ai_office.engine.persisted_execution_outcome_routing_reentry import (
    PersistedExecutionOutcomeRoutingError,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.persisted_success_progression import (
    PersistedSuccessProgressionError,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161Error,
    route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
)
from ai_office.storage import (
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceResult,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "phase161_contract",
    "phase38_contract",
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

    Phase 161 owns runtime/running-history validation, predecessor provenance,
    terminal transition persistence, and the durable commit point. Phase 172
    owns the post-commit composition and committed-snapshot safety boundary.
    Once that commit succeeds, the canonical three-input Phase 38 route
    classifies and progresses the committed targets. Any post-commit failure
    preserves the committed snapshot and never restores the pre-persistence
    running state.
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

    try:
        value = route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
            result, workflow, state_path, events_path
        )
    except Phase161Error as error:
        raise error
    except Exception:
        _fail("dependency_error")

    if not _valid_phase161_result(value):
        _fail("phase161_contract")

    committed = _capture_targets(state_path, events_path)
    try:
        routed = route_persisted_execution_outcome_reentry(
            workflow, state_path, events_path
        )
    except (
        WorkflowExecutionLoadError,
        PersistedExecutionOutcomeRoutingError,
        PersistedExecutionOutcomeError,
        PersistedSuccessProgressionError,
    ) as error:
        _restore_if_changed(state_path, events_path, committed)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, committed)
        _fail("dependency_error")

    if not _valid_phase38_result(routed):
        _restore_if_changed(state_path, events_path, committed)
        _fail("phase38_contract")
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


def _valid_phase161_result(value: object) -> bool:
    """Guard only the Phase-161 persistence-result family.

    Persistence evidence, target identity, byte counts, and terminal-event
    consistency remain owned by Phase 161; this boundary does not duplicate
    those semantic checks.
    """
    return type(value) is WorkflowExecutionPersistenceResult


def _valid_phase38_result(value: object) -> bool:
    """Guard only the canonical Phase 38 result family and route discriminator."""
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
