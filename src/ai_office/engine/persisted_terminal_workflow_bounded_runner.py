"""Explicit persisted-terminal resume through one bounded continuation handoff."""

# ruff: noqa: E501,I001

from dataclasses import dataclass
from typing import Literal

from ai_office.engine.bounded_approved_workflow_runner import (
    ApprovedWorkflowContinuationContext,
    route_bounded_approved_workflow_continuation,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_execution_outcome_routing_reentry import (
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision

PersistedTerminalWorkflowBoundedRunnerClassification = Literal[
    "routing_contract",
    "contexts_type",
    "context_type",
    "bounded_continuation_contract",
]

_ERROR_MESSAGE = "persisted terminal workflow bounded runner inputs are incompatible"
_MISSING = object()


@dataclass(frozen=True)
class PersistedTerminalWorkflowBoundedRunnerFailureDetail:
    """Detail-safe classification for one Phase-212 boundary failure."""

    classification: PersistedTerminalWorkflowBoundedRunnerClassification


class PersistedTerminalWorkflowBoundedRunnerError(ValueError):
    """Base error for the persisted-terminal bounded workflow runner."""


class PersistedTerminalWorkflowBoundedRunnerCompatibilityError(
    PersistedTerminalWorkflowBoundedRunnerError
):
    """Raised when a Phase-212 input or dependency result is incompatible."""

    def __init__(
        self, classification: PersistedTerminalWorkflowBoundedRunnerClassification
    ) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedTerminalWorkflowBoundedRunnerFailureDetail(
            classification
        )


def route_persisted_terminal_workflow_bounded(
    workflow: object,
    state_path: object,
    events_path: object,
    continuation_contexts: object,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Classify one persisted target and stop at one bounded handoff."""
    routed_result = route_persisted_execution_outcome_reentry(
        workflow,
        state_path,
        events_path,
    )

    if type(routed_result) is PersistedExecutionOutcome:
        if not _valid_route(routed_result):
            _fail("routing_contract")
        return routed_result

    if not _valid_route(routed_result):
        _fail("routing_contract")
    if _exact(_attribute(routed_result, "decision"), "workflow_complete"):
        return routed_result

    _validate_continuation_contexts(continuation_contexts)
    bounded_result = route_bounded_approved_workflow_continuation(
        routed_result,
        workflow,
        state_path,
        events_path,
        continuation_contexts,
    )
    if not _valid_route(bounded_result):
        _fail("bounded_continuation_contract")
    return bounded_result


def _validate_continuation_contexts(continuation_contexts: object) -> None:
    if type(continuation_contexts) is not tuple:
        _fail("contexts_type")
    if tuple(map(type, continuation_contexts)) != (
        ApprovedWorkflowContinuationContext,
    ) * len(continuation_contexts):
        _fail("context_type")


def _valid_route(value: object) -> bool:
    """Guard only the result family and route discriminator at this boundary."""
    if type(value) is PersistedExecutionOutcome:
        return _exact(_attribute(value, "outcome"), "persisted_failure")
    if type(value) is WorkflowProgressionDecision:
        return _exact(_attribute(value, "decision"), "prepare_next_step") or _exact(
            _attribute(value, "decision"), "workflow_complete"
        )
    return False


def _attribute(value: object, name: str) -> object:
    try:
        return getattr(value, name)
    except Exception:
        return _MISSING


def _exact(value: object, expected: object) -> bool:
    return type(value) is type(expected) and value == expected


def _fail(
    classification: PersistedTerminalWorkflowBoundedRunnerClassification,
) -> None:
    raise PersistedTerminalWorkflowBoundedRunnerCompatibilityError(
        classification
    ) from None


__all__ = [
    "PersistedTerminalWorkflowBoundedRunnerClassification",
    "PersistedTerminalWorkflowBoundedRunnerFailureDetail",
    "PersistedTerminalWorkflowBoundedRunnerError",
    "PersistedTerminalWorkflowBoundedRunnerCompatibilityError",
    "route_persisted_terminal_workflow_bounded",
]
