"""Bounded explicit-context continuation over the public Phase-190 cycle."""

# ruff: noqa: E501,I001

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.approved_workflow_continuation_cycle import (
    ApprovedWorkflowContinuationCycleError as Phase190Error,
    route_approved_workflow_continuation_cycle,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory

Classification = Literal[
    "result_type",
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "contexts_type",
    "context_type",
    "configuration",
    "phase190_contract",
    "dependency_error",
]

_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_MISSING = object()


@dataclass(frozen=True)
class ApprovedWorkflowContinuationContext:
    """The six caller-supplied values required by one Phase-190 cycle."""

    preparation_approval: object
    employee: object
    resolved_tools: object
    api_key: object
    execution_approval: object
    transport: object


@dataclass(frozen=True)
class BoundedApprovedWorkflowRunnerFailureDetail:
    """Safe local classification for one runner-owned failure."""

    classification: Classification


class BoundedApprovedWorkflowRunnerError(ValueError):
    """Base error for the bounded approved-workflow runner."""


class BoundedApprovedWorkflowRunnerCompatibilityError(
    BoundedApprovedWorkflowRunnerError
):
    """Raised when a runner input or Phase-190 output is incompatible."""

    def __init__(self, classification: Classification) -> None:
        super().__init__("bounded approved workflow runner inputs are incompatible")
        self.detail = BoundedApprovedWorkflowRunnerFailureDetail(classification)



def route_bounded_approved_workflow_continuation(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    contexts: object,
    *,
    phase190_function=route_approved_workflow_continuation_cycle,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Run one public Phase-190 cycle for each supplied context, at most."""
    _check_workflow(workflow)
    _check_result(result, workflow)
    assert type(workflow) is WorkflowDefinition

    if _is_terminal(result):
        return result

    if type(contexts) is not tuple:
        _fail("contexts_type")
    _check_targets(state_path, events_path)
    _check_contexts(contexts)
    if not callable(phase190_function):
        _fail("configuration")
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    assert type(contexts) is tuple

    current = result
    if not contexts:
        return current
    for context in contexts:
        if _is_terminal(current):
            return current
        assert type(current) is WorkflowProgressionDecision
        assert current.decision == "prepare_next_step"
        try:
            next_result = phase190_function(
                current,
                workflow,
                context.preparation_approval,
                context.employee,
                state_path,
                events_path,
                context.resolved_tools,
                context.api_key,
                context.execution_approval,
                context.transport,
            )
        except Phase190Error:
            raise
        except Exception:
            if phase190_function is route_approved_workflow_continuation_cycle:
                raise
            _fail("dependency_error")
        _check_phase190_result(next_result, workflow)
        _check_transition_result(next_result, workflow, current)
        current = next_result

    return current


def _check_workflow(workflow: object) -> None:
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")


def _check_result(
    result: object, workflow: WorkflowDefinition
) -> None:
    if type(result) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        _fail("result_type")
    if type(result) is WorkflowProgressionDecision:
        discriminator = _attribute(result, "decision")
        if _exact(discriminator, "prepare_next_step"):
            _check_prepare_result(result, workflow)
        elif _exact(discriminator, "workflow_complete"):
            _check_complete_result(result, workflow)
        else:
            _fail("result_type")
    else:
        assert type(result) is PersistedExecutionOutcome
        if not _exact(_attribute(result, "outcome"), "persisted_failure"):
            _fail("result_type")
        _check_failure_result(result, workflow)


def _check_transition_result(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    previous: WorkflowProgressionDecision,
) -> None:
    expected_index = previous.next_step_index
    assert type(expected_index) is int
    step = workflow.steps[expected_index - 1]
    if (
        type(result) is WorkflowProgressionDecision
        and result.decision == "workflow_complete"
    ):
        _check_complete_result(result, workflow)
    elif (
        type(result) is PersistedExecutionOutcome
        and result.outcome == "persisted_failure"
    ):
        _check_failure_result(result, workflow)
    else:
        assert type(result) is WorkflowProgressionDecision
        _check_prepare_result(result, workflow)
    if not (
        type(result.current_step_index) is int
        and result.current_step_index == expected_index
        and _exact(result.current_step_id, step.id)
        and _exact(result.current_employee_id, step.employee)
    ):
        _fail("phase190_contract")


def _check_phase190_result(
    result: object, workflow: WorkflowDefinition
) -> None:
    """Validate an injected Phase-190 result as the runner's seam contract."""
    try:
        _check_result(result, workflow)
    except BoundedApprovedWorkflowRunnerCompatibilityError:
        _fail("phase190_contract")
    except Exception:
        _fail("phase190_contract")


def _check_prepare_result(
    result: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    decision = _attribute(result, "decision")
    current_index = _attribute(result, "current_step_index")
    next_index = _attribute(result, "next_step_index")
    if not (
        _exact(decision, "prepare_next_step")
        and type(current_index) is int
        and 1 <= current_index < len(workflow.steps)
        and type(next_index) is int
        and next_index == current_index + 1
    ):
        _fail("result_type")
    current = workflow.steps[current_index - 1]
    next_step = workflow.steps[next_index - 1]
    if not (
        _exact(_attribute(result, "workflow_id"), workflow.id)
        and _exact(_attribute(result, "current_step_id"), current.id)
        and _exact(_attribute(result, "current_employee_id"), current.employee)
        and _exact(_attribute(result, "next_step_id"), next_step.id)
        and _exact(_attribute(result, "next_employee_id"), next_step.employee)
        and _exact(_attribute(result, "reason"), "next_step_available")
    ):
        _fail("result_type")


def _check_complete_result(
    result: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    current_index = _attribute(result, "current_step_index")
    if not (
        _exact(_attribute(result, "decision"), "workflow_complete")
        and _exact(_attribute(result, "workflow_id"), workflow.id)
        and _exact(_attribute(result, "current_step_id"), final.id)
        and type(current_index) is int
        and current_index == len(workflow.steps)
        and _exact(_attribute(result, "current_employee_id"), final.employee)
        and _attribute(result, "next_step_id") is None
        and _attribute(result, "next_step_index") is None
        and _attribute(result, "next_employee_id") is None
        and _exact(_attribute(result, "reason"), "last_step_succeeded")
    ):
        _fail("result_type")


def _check_failure_result(
    result: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    index = _attribute(result, "current_step_index")
    failure_category = _attribute(result, "failure_category")
    if not (
        _exact(_attribute(result, "outcome"), "persisted_failure")
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact(_attribute(result, "workflow_id"), workflow.id)
        and _exact(
            _attribute(result, "current_step_id"), workflow.steps[index - 1].id
        )
        and _exact(
            _attribute(result, "current_employee_id"),
            workflow.steps[index - 1].employee,
        )
        and type(failure_category) is str
        and failure_category in _FAILURE_CATEGORIES
    ):
        _fail("result_type")


def _check_targets(state_path: object, events_path: object) -> None:
    if type(state_path) is not _PATH_TYPE:
        _fail("state_target")
    if type(events_path) is not _PATH_TYPE:
        _fail("event_target")
    if state_path == events_path:
        _fail("target_conflict")
    try:
        if not state_path.is_file():
            _fail("state_target")
    except OSError:
        _fail("state_target")
    try:
        if not events_path.is_file():
            _fail("event_target")
    except OSError:
        _fail("event_target")


def _check_contexts(contexts: object) -> None:
    if type(contexts) is not tuple:
        _fail("contexts_type")
    if any(
        type(context) is not ApprovedWorkflowContinuationContext
        for context in contexts
    ):
        _fail("context_type")


def _is_terminal(value: object) -> bool:
    return (
        type(value) is WorkflowProgressionDecision
        and _exact(_attribute(value, "decision"), "workflow_complete")
    ) or (
        type(value) is PersistedExecutionOutcome
        and _exact(_attribute(value, "outcome"), "persisted_failure")
    )


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    workflow_id = _attribute(workflow, "id")
    name = _attribute(workflow, "name")
    description = _attribute(workflow, "description")
    steps = _attribute(workflow, "steps")
    if not (
        _nonempty(workflow_id)
        and _nonempty(name)
        and _nonempty(description)
        and type(steps) is list
        and bool(steps)
    ):
        return False
    if any(not _valid_step(step) for step in steps):
        return False
    ids = tuple(_attribute(step, "id") for step in steps)
    try:
        return len(ids) == len(set(ids))
    except Exception:
        return False


def _valid_step(step: object) -> bool:
    return (
        type(step) is WorkflowStepDefinition
        and _nonempty(_attribute(step, "id"))
        and _nonempty(_attribute(step, "name"))
        and _nonempty(_attribute(step, "employee"))
        and _nonempty(_attribute(step, "instructions"))
    )


def _attribute(value: object, name: str) -> object:
    """Read a boundary value without leaking malformed-model exceptions."""
    try:
        return getattr(value, name)
    except Exception:
        return _MISSING


def _exact(value: object, expected: object) -> bool:
    return type(value) is type(expected) and value == expected


def _nonempty(value: object) -> bool:
    return type(value) is str and bool(value)


def _fail(classification: Classification) -> None:
    raise BoundedApprovedWorkflowRunnerCompatibilityError(classification) from None


__all__ = [
    "ApprovedWorkflowContinuationContext",
    "BoundedApprovedWorkflowRunnerCompatibilityError",
    "BoundedApprovedWorkflowRunnerError",
    "BoundedApprovedWorkflowRunnerFailureDetail",
    "route_bounded_approved_workflow_continuation",
]
