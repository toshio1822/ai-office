"""Continue exact Phase 83 persistence results through the Phase 77 boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_running_execution_routing_phase_bridge_cycle_continuation import (  # noqa: E501
    PersistedRunningExecutionRoutingPhaseBridgeCycleContinuationCompatibilityError,
    PersistedRunningExecutionRoutingPhaseBridgeCycleContinuationError,
    route_persisted_running_execution_routing_phase_bridge_cycle_continuation,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import StepRuntimeExecutionFailure, StepRuntimeExecutionSuccess

Classification = Literal[
    "result_type",
    "workflow_definition",
    "execution_inputs",
    "persistence_result_contract",
    "start_contract",
    "employee_contract",
    "tools_contract",
    "credential_contract",
    "approval_contract",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "execution_result_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase77Function = Callable[
    [
        object,
        object | None,
        object,
        object | None,
        object,
        object,
        object | None,
        object | None,
        object | None,
        object | None,
    ],
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]


@dataclass(frozen=True)
class PersistedRunningExecutionRoutingPhaseBridgeCycleReentryContinuationFailureDetail:
    classification: Classification


class PersistedRunningExecutionRoutingPhaseBridgeCycleReentryContinuationError(
    ValueError
):
    """Raised when Phase 77 cannot safely continue its supplied result."""


class PersistedRunningExecutionRoutingPhaseBridgeCycleReentryContinuationCompatibilityError(  # noqa: E501
    PersistedRunningExecutionRoutingPhaseBridgeCycleReentryContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted-running execution routing phase bridge cycle reentry "
            "continuation inputs are incompatible"
        )
        self.detail = (
            PersistedRunningExecutionRoutingPhaseBridgeCycleReentryContinuationFailureDetail(
                classification
            )
        )


def route_persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation(
    result: object,
    start: object | None,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    resolved_tools: object | None,
    api_key: object | None,
    approval: object | None,
    transport: object | None,
    *,
    phase77_function: Phase77Function = (
        route_persisted_running_execution_routing_phase_bridge_cycle_continuation
    ),
) -> (
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route one exact Phase 83 result, stopping after the single Phase 77 call."""
    try:
        return route_persisted_running_execution_routing_phase_bridge_cycle_continuation(  # noqa: E501
            result,
            start,
            workflow,
            employee,
            state_path,
            events_path,
            resolved_tools,
            api_key,
            approval,
            transport,
            phase70_function=phase77_function,
        )
    except (  # noqa: E501
        PersistedRunningExecutionRoutingPhaseBridgeCycleContinuationCompatibilityError
    ) as error:
        raise (  # noqa: E501
            PersistedRunningExecutionRoutingPhaseBridgeCycleReentryContinuationCompatibilityError(
            error.detail.classification
            )
        ) from None
    except PersistedRunningExecutionRoutingPhaseBridgeCycleContinuationError:
        # Safe dependency errors are intentionally preserved by Phase 77.
        raise
