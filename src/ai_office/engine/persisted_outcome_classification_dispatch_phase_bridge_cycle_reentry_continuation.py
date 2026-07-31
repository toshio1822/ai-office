"""Dispatch one Phase 92 persistence result through the Phase 86 boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    persisted_outcome_classification_routing_phase_bridge_cycle_reentry_continuation as _phase86,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.storage import WorkflowExecutionPersistenceResult

Classification = Literal[
    "result_type", "workflow_definition", "completion_contract", "failure_contract",
    "state_target", "event_target", "target_conflict", "terminal_contract",
    "persistence_contract", "outcome_contract", "dependency_error", "dependency_rollback",
]
Phase86Function = Callable[..., PersistedExecutionOutcome]


@dataclass(frozen=True)
class PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationFailureDetail:
    classification: Classification


class PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationError(ValueError):
    """Raised when Phase 93 cannot safely dispatch the supplied result."""


class PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError(
    PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted outcome classification dispatch phase bridge cycle reentry continuation inputs are incompatible"
        )
        self.detail = PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationFailureDetail(classification)


def route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase86_function: Phase86Function = _phase86.route_persisted_outcome_classification_routing_phase_bridge_cycle_reentry_continuation,
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    try:
        _phase86._top_level(result, workflow, state_path, events_path, phase86_function)
        assert type(workflow) is WorkflowDefinition
        assert type(state_path) is _phase86._PATH_TYPE and type(events_path) is _phase86._PATH_TYPE
        if type(result) is WorkflowProgressionDecision:
            _phase86._validate_completion(result, workflow)
        elif type(result) is PersistedExecutionOutcome:
            _phase86._validate_failure(result, workflow)
        _phase86._validate_targets(state_path, events_path)
        original = _phase86._capture(state_path, events_path)
        if type(result) is WorkflowProgressionDecision:
            _phase86._validate_terminal(result, workflow, state_path, events_path, "succeeded")
            _phase86._require_unchanged(state_path, events_path, original, "terminal_contract")
            return result
        if type(result) is PersistedExecutionOutcome:
            _phase86._validate_terminal(result, workflow, state_path, events_path, "failed")
            _phase86._require_unchanged(state_path, events_path, original, "terminal_contract")
            return result
        assert type(result) is WorkflowExecutionPersistenceResult
        state = _phase86._validate_persistence(result, workflow, state_path, events_path)
    except _phase86.PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as error:
        _raise_from_phase86(error)

    try:
        value = phase86_function(result, workflow, state_path, events_path)
    except (
        _phase86.PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationError,
        _phase86.PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationError,
    ) as error:
        try:
            _phase86._restore_if_changed(state_path, events_path, original)
        except _phase86.PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as rollback:
            _raise_from_phase86(rollback)
        raise error
    except Exception:
        try:
            _phase86._restore_if_changed(state_path, events_path, original)
        except _phase86.PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as rollback:
            _raise_from_phase86(rollback)
        _raise("dependency_error")

    try:
        _phase86._require_unchanged(state_path, events_path, original, "outcome_contract")
        _phase86._validate_outcome(value, state)
    except _phase86.PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            try:
                _phase86._restore_if_changed(state_path, events_path, original)
            except _phase86.PersistedOutcomeClassificationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as rollback:
                _raise_from_phase86(rollback)
        _raise_from_phase86(error)
    return value


def _raise_from_phase86(error: Exception) -> None:
    classification = getattr(getattr(error, "detail", None), "classification", "dependency_error")
    if classification not in Classification.__args__:  # type: ignore[attr-defined]
        classification = "dependency_error"
    _raise(classification)  # type: ignore[arg-type]


def _raise(classification: Classification) -> None:
    raise PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError(classification) from None
