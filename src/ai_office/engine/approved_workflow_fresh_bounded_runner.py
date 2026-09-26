"""Explicit fresh-start composition with one bounded continuation handoff."""

# ruff: noqa: E501,I001

from dataclasses import dataclass
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.approved_workflow_fresh_start import (
    route_approved_workflow_fresh_start,
)
from ai_office.engine.bounded_approved_workflow_runner import (
    ApprovedWorkflowContinuationContext,
    route_bounded_approved_workflow_continuation,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory

ApprovedFreshWorkflowBoundedRunnerClassification = Literal[
    "contexts_type",
    "context_type",
    "fresh_start_contract",
    "bounded_continuation_contract",
]

_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_MISSING = object()


@dataclass(frozen=True)
class ApprovedFreshWorkflowBoundedRunnerFailureDetail:
    """Detail-safe classification for one Phase-210 boundary failure."""

    classification: ApprovedFreshWorkflowBoundedRunnerClassification


class ApprovedFreshWorkflowBoundedRunnerError(ValueError):
    """Base error for the fresh bounded workflow runner."""


class ApprovedFreshWorkflowBoundedRunnerCompatibilityError(
    ApprovedFreshWorkflowBoundedRunnerError
):
    """Raised when a Phase-210 input or dependency result is incompatible."""

    def __init__(
        self, classification: ApprovedFreshWorkflowBoundedRunnerClassification
    ) -> None:
        super().__init__(
            "approved fresh workflow bounded runner inputs are incompatible"
        )
        self.detail = ApprovedFreshWorkflowBoundedRunnerFailureDetail(classification)


def route_approved_fresh_workflow_bounded(
    workflow: object,
    state_path: object,
    events_path: object,
    bootstrap_context: object,
    continuation_contexts: object,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Compose one canonical fresh start with one bounded continuation handoff."""
    _check_preconfiguration(continuation_contexts)

    fresh_result = route_approved_workflow_fresh_start(
        workflow,
        state_path,
        events_path,
        bootstrap_context,
    )
    if not _valid_result(fresh_result, workflow):
        _fail("fresh_start_contract")
    if _is_terminal(fresh_result):
        return fresh_result

    bounded_result = route_bounded_approved_workflow_continuation(
        fresh_result,
        workflow,
        state_path,
        events_path,
        continuation_contexts,
    )
    if not _valid_result(bounded_result, workflow):
        _fail("bounded_continuation_contract")
    return bounded_result


def _check_preconfiguration(continuation_contexts: object) -> None:
    """Perform only the pre-Phase-208 continuation-context checks."""
    if type(continuation_contexts) is not tuple:
        _fail("contexts_type")
    if tuple(map(type, continuation_contexts)) != (
        ApprovedWorkflowContinuationContext,
    ) * len(continuation_contexts):
        _fail("context_type")


def _valid_result(value: object, workflow: object) -> bool:
    if type(value) is WorkflowProgressionDecision:
        decision = _attribute(value, "decision")
        if _exact(decision, "prepare_next_step"):
            return _valid_prepare(value, workflow)
        if _exact(decision, "workflow_complete"):
            return _valid_complete(value, workflow)
        return False
    if type(value) is PersistedExecutionOutcome:
        return _valid_failure(value, workflow)
    return False


def _valid_prepare(value: WorkflowProgressionDecision, workflow: object) -> bool:
    current_index = _attribute(value, "current_step_index")
    next_index = _attribute(value, "next_step_index")
    if type(current_index) is not int or type(next_index) is not int:
        return False
    current = _step_at(workflow, current_index)
    next_step = _step_at(workflow, next_index)
    if current is _MISSING or next_step is _MISSING:
        return False
    return (
        next_index == current_index + 1
        and _valid_current_linkage(value, workflow, current, current_index)
        and _exact(_attribute(value, "next_step_id"), _attribute(next_step, "id"))
        and _exact(
            _attribute(value, "next_employee_id"),
            _attribute(next_step, "employee"),
        )
        and _exact(_attribute(value, "reason"), "next_step_available")
    )


def _valid_complete(value: WorkflowProgressionDecision, workflow: object) -> bool:
    steps = _workflow_steps(workflow)
    if steps is _MISSING or not steps:
        return False
    current_index = _attribute(value, "current_step_index")
    final = _step_at(workflow, len(steps))
    if final is _MISSING:
        return False
    return (
        type(current_index) is int
        and current_index == len(steps)
        and _valid_current_linkage(value, workflow, final, current_index)
        and _attribute(value, "next_step_id") is None
        and _attribute(value, "next_step_index") is None
        and _attribute(value, "next_employee_id") is None
        and _exact(_attribute(value, "reason"), "last_step_succeeded")
    )


def _valid_failure(value: PersistedExecutionOutcome, workflow: object) -> bool:
    current_index = _attribute(value, "current_step_index")
    current = _step_at(workflow, current_index)
    failure_category = _attribute(value, "failure_category")
    return (
        current is not _MISSING
        and type(current_index) is int
        and _valid_current_linkage(value, workflow, current, current_index)
        and type(failure_category) is str
        and failure_category in _FAILURE_CATEGORIES
        and _exact(_attribute(value, "outcome"), "persisted_failure")
    )


def _valid_current_linkage(
    value: object,
    workflow: object,
    current: object,
    current_index: int,
) -> bool:
    return (
        _exact(_attribute(value, "workflow_id"), _attribute(workflow, "id"))
        and _exact(_attribute(value, "current_step_id"), _attribute(current, "id"))
        and _exact(
            _attribute(value, "current_step_index"),
            current_index,
        )
        and _exact(
            _attribute(value, "current_employee_id"),
            _attribute(current, "employee"),
        )
    )


def _workflow_steps(workflow: object) -> object:
    if type(workflow) is not WorkflowDefinition:
        return _MISSING
    steps = _attribute(workflow, "steps")
    if type(steps) is not list:
        return _MISSING
    return steps


def _step_at(workflow: object, index: object) -> object:
    steps = _workflow_steps(workflow)
    if steps is _MISSING or type(index) is not int or not 1 <= index <= len(steps):
        return _MISSING
    step = steps[index - 1]
    if type(step) is not WorkflowStepDefinition:
        return _MISSING
    return step


def _is_terminal(value: object) -> bool:
    return (
        type(value) is WorkflowProgressionDecision
        and _exact(_attribute(value, "decision"), "workflow_complete")
    ) or (
        type(value) is PersistedExecutionOutcome
        and _exact(_attribute(value, "outcome"), "persisted_failure")
    )


def _attribute(value: object, name: str) -> object:
    try:
        return getattr(value, name)
    except Exception:
        return _MISSING


def _exact(value: object, expected: object) -> bool:
    return type(value) is type(expected) and value == expected


def _fail(
    classification: ApprovedFreshWorkflowBoundedRunnerClassification,
) -> None:
    raise ApprovedFreshWorkflowBoundedRunnerCompatibilityError(classification) from None


__all__ = [
    "ApprovedFreshWorkflowBoundedRunnerClassification",
    "ApprovedFreshWorkflowBoundedRunnerCompatibilityError",
    "ApprovedFreshWorkflowBoundedRunnerError",
    "ApprovedFreshWorkflowBoundedRunnerFailureDetail",
    "route_approved_fresh_workflow_bounded",
]
