"""Explicit persisted-terminal resume through one bounded continuation handoff."""

# ruff: noqa: E501,I001

from dataclasses import dataclass
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.bounded_approved_workflow_runner import (
    ApprovedWorkflowContinuationContext,
    route_bounded_approved_workflow_continuation,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
    classify_persisted_execution_outcome_reentry,
)
from ai_office.engine.persisted_execution_outcome_routing_reentry import (
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory

PersistedTerminalWorkflowBoundedRunnerClassification = Literal[
    "configuration",
    "classification_contract",
    "routing_contract",
    "contexts_type",
    "context_type",
    "bounded_continuation_contract",
    "dependency_error",
]

_ERROR_MESSAGE = "persisted terminal workflow bounded runner inputs are incompatible"
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
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
    *,
    classification_function=classify_persisted_execution_outcome_reentry,
    routing_function=route_persisted_execution_outcome_reentry,
    bounded_continuation_function=route_bounded_approved_workflow_continuation,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Resume one persisted terminal outcome and stop at one bounded handoff."""
    if not callable(classification_function) or not callable(routing_function):
        _fail("configuration")

    try:
        classified_outcome = classification_function(
            workflow,
            state_path,
            events_path,
        )
    except Exception:
        if classification_function is classify_persisted_execution_outcome_reentry:
            raise
        _fail("dependency_error")
    if not _valid_persisted_outcome(classified_outcome, workflow):
        _fail("classification_contract")

    try:
        routed_result = routing_function(
            classified_outcome,
            workflow,
            state_path,
            events_path,
        )
    except Exception:
        if routing_function is route_persisted_execution_outcome_reentry:
            raise
        _fail("dependency_error")

    if _exact(_attribute(classified_outcome, "outcome"), "persisted_failure"):
        if (
            routed_result is not classified_outcome
            or not _valid_persisted_outcome(routed_result, workflow)
        ):
            _fail("routing_contract")
        return routed_result

    if not _valid_routed_success(routed_result, workflow, classified_outcome):
        _fail("routing_contract")
    if _exact(_attribute(routed_result, "decision"), "workflow_complete"):
        return routed_result

    _validate_continuation_configuration(
        continuation_contexts,
        bounded_continuation_function,
    )
    try:
        bounded_result = bounded_continuation_function(
            routed_result,
            workflow,
            state_path,
            events_path,
            continuation_contexts,
        )
    except Exception:
        if bounded_continuation_function is route_bounded_approved_workflow_continuation:
            raise
        _fail("dependency_error")
    if not _valid_bounded_result(
        bounded_result,
        workflow,
        routed_result,
        continuation_contexts,
    ):
        _fail("bounded_continuation_contract")
    return bounded_result


def _validate_continuation_configuration(
    continuation_contexts: object,
    bounded_continuation_function: object,
) -> None:
    if type(continuation_contexts) is not tuple:
        _fail("contexts_type")
    if tuple(map(type, continuation_contexts)) != (
        ApprovedWorkflowContinuationContext,
    ) * len(continuation_contexts):
        _fail("context_type")
    if not callable(bounded_continuation_function):
        _fail("configuration")


def _valid_persisted_outcome(value: object, workflow: object) -> bool:
    if type(value) is not PersistedExecutionOutcome:
        return False
    outcome = _attribute(value, "outcome")
    if not (
        _exact(outcome, "persisted_success")
        or _exact(outcome, "persisted_failure")
    ):
        return False
    if not _valid_current_linkage(value, workflow):
        return False
    failure_category = _attribute(value, "failure_category")
    if _exact(outcome, "persisted_success"):
        return failure_category is None
    return (
        type(failure_category) is str
        and failure_category in _FAILURE_CATEGORIES
    )


def _valid_routed_success(
    value: object,
    workflow: object,
    classified_outcome: PersistedExecutionOutcome,
) -> bool:
    if type(value) is not WorkflowProgressionDecision:
        return False
    if not _valid_current_linkage(value, workflow):
        return False
    if not (
        _exact(_attribute(value, "workflow_id"), classified_outcome.workflow_id)
        and _exact(
            _attribute(value, "current_step_id"),
            classified_outcome.current_step_id,
        )
        and _exact(
            _attribute(value, "current_step_index"),
            classified_outcome.current_step_index,
        )
        and _exact(
            _attribute(value, "current_employee_id"),
            classified_outcome.current_employee_id,
        )
    ):
        return False
    decision = _attribute(value, "decision")
    if _exact(decision, "prepare_next_step"):
        return _valid_prepare(value, workflow)
    if _exact(decision, "workflow_complete"):
        return _valid_complete(value, workflow)
    return False


def _valid_bounded_result(
    value: object,
    workflow: object,
    previous: WorkflowProgressionDecision,
    contexts: tuple[object, ...],
) -> bool:
    if type(contexts) is not tuple:
        return False
    if not contexts:
        return value is previous and _valid_prepare(value, workflow)

    steps = _workflow_steps(workflow)
    start_index = _attribute(previous, "next_step_index")
    if (
        steps is _MISSING
        or type(start_index) is not int
    ):
        return False
    maximum_index = min(len(steps), start_index + len(contexts) - 1)
    if not _valid_bounded_position(value, workflow, start_index, maximum_index):
        return False

    if type(value) is WorkflowProgressionDecision:
        decision = _attribute(value, "decision")
        if _exact(decision, "prepare_next_step"):
            return _valid_exhausted_prepare(value, workflow, maximum_index)
        if _exact(decision, "workflow_complete"):
            return _valid_complete(value, workflow)
        return False
    if type(value) is PersistedExecutionOutcome:
        return _valid_persisted_failure(value, workflow)
    return False


def _valid_bounded_position(
    value: object,
    workflow: object,
    minimum_index: int,
    maximum_index: int,
) -> bool:
    current_index = _attribute(value, "current_step_index")
    return (
        type(current_index) is int
        and minimum_index <= current_index <= maximum_index
        and _valid_current_linkage(value, workflow)
    )


def _valid_exhausted_prepare(
    value: object,
    workflow: object,
    maximum_index: int,
) -> bool:
    return (
        _attribute(value, "current_step_index") == maximum_index
        and _valid_prepare(value, workflow)
    )


def _valid_persisted_failure(value: object, workflow: object) -> bool:
    return type(value) is PersistedExecutionOutcome and _exact(
        _attribute(value, "outcome"), "persisted_failure"
    ) and _valid_persisted_outcome(value, workflow)


def _valid_prepare(value: object, workflow: object) -> bool:
    steps = _workflow_steps(workflow)
    current_index = _attribute(value, "current_step_index")
    next_index = _attribute(value, "next_step_index")
    if (
        steps is _MISSING
        or type(current_index) is not int
        or type(next_index) is not int
    ):
        return False
    current = _step_at(workflow, current_index)
    next_step = _step_at(workflow, next_index)
    if current is _MISSING or next_step is _MISSING:
        return False
    return (
        _exact(_attribute(value, "decision"), "prepare_next_step")
        and current_index < len(steps)
        and next_index == current_index + 1
        and _valid_current_linkage(value, workflow)
        and _exact(_attribute(value, "next_step_id"), next_step.id)
        and _exact(_attribute(value, "next_employee_id"), next_step.employee)
        and _exact(_attribute(value, "reason"), "next_step_available")
    )


def _valid_complete(value: object, workflow: object) -> bool:
    steps = _workflow_steps(workflow)
    if steps is _MISSING or not steps:
        return False
    current_index = _attribute(value, "current_step_index")
    final = _step_at(workflow, len(steps))
    if final is _MISSING:
        return False
    return (
        _exact(_attribute(value, "decision"), "workflow_complete")
        and type(current_index) is int
        and current_index == len(steps)
        and _valid_current_linkage(value, workflow)
        and _attribute(value, "next_step_id") is None
        and _attribute(value, "next_step_index") is None
        and _attribute(value, "next_employee_id") is None
        and _exact(_attribute(value, "reason"), "last_step_succeeded")
    )


def _valid_current_linkage(value: object, workflow: object) -> bool:
    current_index = _attribute(value, "current_step_index")
    current = _step_at(workflow, current_index)
    return (
        current is not _MISSING
        and _exact(_attribute(value, "workflow_id"), _attribute(workflow, "id"))
        and _exact(_attribute(value, "current_step_id"), current.id)
        and _exact(_attribute(value, "current_employee_id"), current.employee)
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
    if (
        steps is _MISSING
        or type(index) is not int
        or not 1 <= index <= len(steps)
    ):
        return _MISSING
    step = steps[index - 1]
    if type(step) is not WorkflowStepDefinition:
        return _MISSING
    return step


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
    raise PersistedTerminalWorkflowBoundedRunnerCompatibilityError(classification) from None


__all__ = [
    "PersistedTerminalWorkflowBoundedRunnerClassification",
    "PersistedTerminalWorkflowBoundedRunnerFailureDetail",
    "PersistedTerminalWorkflowBoundedRunnerError",
    "PersistedTerminalWorkflowBoundedRunnerCompatibilityError",
    "route_persisted_terminal_workflow_bounded",
]
