"""Dispatch one Phase 93 classified outcome through the Phase 87 boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    classified_outcome_routing_phase_bridge_cycle_reentry_continuation as _phase87,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision

Classification = Literal[
    "result_type", "workflow_definition", "success_contract", "failure_contract",
    "completion_contract", "state_target", "event_target", "target_conflict",
    "terminal_contract", "outcome_contract", "progression_contract", "dependency_error",
    "dependency_rollback",
]
Phase87Function = Callable[..., WorkflowProgressionDecision | PersistedExecutionOutcome]


@dataclass(frozen=True)
class ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationFailureDetail:
    classification: Classification


class ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationError(ValueError):
    """Raised when Phase 94 cannot safely dispatch the supplied result."""


class ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError(
    ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "classified outcome dispatch phase bridge cycle reentry continuation inputs are incompatible"
        )
        self.detail = ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationFailureDetail(classification)


def route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase87_function: Phase87Function = _phase87.route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    try:
        _phase87._inputs(result, workflow, state_path, events_path, phase87_function)
        assert type(workflow) is WorkflowDefinition
        assert type(state_path) is _phase87._PATH_TYPE and type(events_path) is _phase87._PATH_TYPE
        if type(result) is WorkflowProgressionDecision:
            _phase87._completion(result, workflow)
        else:
            assert type(result) is PersistedExecutionOutcome
            if result.outcome == "persisted_success":
                _phase87._success(result, workflow)
            else:
                _phase87._failure(result, workflow)
        _phase87._targets(state_path, events_path)
        original = _phase87._capture(state_path, events_path)
        if type(result) is WorkflowProgressionDecision:
            _phase87._terminal(result, workflow, state_path, events_path, "succeeded")
            _phase87._unchanged(state_path, events_path, original, "terminal_contract")
            return result
        assert type(result) is PersistedExecutionOutcome
        status = "succeeded" if result.outcome == "persisted_success" else "failed"
        _phase87._terminal(result, workflow, state_path, events_path, status)
        if result.outcome == "persisted_failure":
            _phase87._unchanged(state_path, events_path, original, "terminal_contract")
            return result
    except _phase87.ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as error:
        _raise_from_phase87(error)

    try:
        value = phase87_function(result, workflow, state_path, events_path)
    except (
        _phase87.ClassifiedOutcomeRoutingPhaseBridgeContinuationError,
        _phase87.ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationError,
    ) as error:
        try:
            _phase87._restore(state_path, events_path, original)
        except _phase87.ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as rollback:
            _raise_from_phase87(rollback)
        raise error
    except Exception:
        try:
            _phase87._restore(state_path, events_path, original)
        except _phase87.ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as rollback:
            _raise_from_phase87(rollback)
        _raise("dependency_error")

    try:
        _phase87._unchanged(state_path, events_path, original, "outcome_contract")
        _phase87._progression(value, result, workflow)
    except _phase87.ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            try:
                _phase87._restore(state_path, events_path, original)
            except _phase87.ClassifiedOutcomeRoutingPhaseBridgeCycleReentryContinuationCompatibilityError as rollback:
                _raise_from_phase87(rollback)
        _raise_from_phase87(error)
    return value


def _raise_from_phase87(error: Exception) -> None:
    classification = getattr(getattr(error, "detail", None), "classification", "dependency_error")
    if classification not in Classification.__args__:  # type: ignore[attr-defined]
        classification = "dependency_error"
    _raise(classification)  # type: ignore[arg-type]


def _raise(classification: Classification) -> None:
    raise ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError(classification) from None
