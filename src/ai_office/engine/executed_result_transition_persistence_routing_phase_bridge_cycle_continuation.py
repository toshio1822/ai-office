"""Continue exact Phase 77 results through the Phase 71 boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ai_office.engine.executed_result_transition_persistence_routing_phase_bridge_continuation import (
    ExecutedResultTransitionPersistenceRoutingPhaseBridgeContinuationCompatibilityError,
    route_executed_result_transition_persistence_routing_phase_bridge_continuation,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.storage import WorkflowExecutionPersistenceResult

Classification = Literal[
    "result_type",
    "workflow_definition",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "runtime_contract",
    "persistence_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase71Function = Callable[
    ...,
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]


@dataclass(frozen=True)
class ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationFailureDetail:
    classification: Classification


class ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationError(
    ValueError
):
    """Raised when Phase 78 cannot safely continue the supplied result."""


class ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationCompatibilityError(
    ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "executed-result transition persistence routing phase bridge cycle continuation inputs are incompatible"
        )
        self.detail = ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationFailureDetail(
            classification
        )


def route_executed_result_transition_persistence_routing_phase_bridge_cycle_continuation(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase71_function: Phase71Function = route_executed_result_transition_persistence_routing_phase_bridge_continuation,
) -> (
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    try:
        return route_executed_result_transition_persistence_routing_phase_bridge_continuation(
            result,
            workflow,
            state_path,
            events_path,
            phase64_function=phase71_function,
        )
    except ExecutedResultTransitionPersistenceRoutingPhaseBridgeContinuationCompatibilityError as error:
        raise ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationCompatibilityError(
            error.detail.classification
        ) from None


__all__ = [
    "ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationCompatibilityError",
    "ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationError",
    "ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationFailureDetail",
    "route_executed_result_transition_persistence_routing_phase_bridge_cycle_continuation",
]
