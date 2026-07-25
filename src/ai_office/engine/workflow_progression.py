"""Pure, provider-independent workflow progression decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage.workflow_execution_history import LoadedWorkflowExecutionHistory

WorkflowProgressionDecisionType = Literal[
    "prepare_next_step",
    "workflow_complete",
    "stopped_failed",
    "not_progressable",
]
WorkflowProgressionCompatibilityClassification = Literal[
    "workflow_identity",
    "current_step_index",
    "current_step_identity",
    "current_employee_identity",
    "completed_step_identity",
    "completed_step_order",
]

_COMPATIBILITY_ERROR_MESSAGE = "workflow progression inputs are incompatible"


@dataclass(frozen=True)
class WorkflowProgressionDecision:
    """An explicit, immutable decision about one loaded workflow state."""

    decision: WorkflowProgressionDecisionType
    workflow_id: str
    current_step_id: str
    current_step_index: int
    current_employee_id: str
    next_step_id: str | None
    next_step_index: int | None
    next_employee_id: str | None
    reason: str


@dataclass(frozen=True)
class WorkflowProgressionCompatibilityDetail:
    """Safe immutable classification for an incompatible workflow history."""

    classification: WorkflowProgressionCompatibilityClassification


class WorkflowProgressionDecisionError(ValueError):
    """Raised when workflow progression cannot be decided safely."""


class WorkflowProgressionCompatibilityError(WorkflowProgressionDecisionError):
    """Raised when loaded history does not match the workflow definition."""

    def __init__(
        self, classification: WorkflowProgressionCompatibilityClassification
    ) -> None:
        super().__init__(_COMPATIBILITY_ERROR_MESSAGE)
        self.detail = WorkflowProgressionCompatibilityDetail(classification)


def decide_workflow_progression(
    workflow: WorkflowDefinition,
    history: LoadedWorkflowExecutionHistory,
) -> WorkflowProgressionDecision:
    """Validate compatibility and decide whether one workflow may progress.

    This function only returns a decision; it neither changes state nor executes a step.
    """
    state = history.state
    _validate_compatibility(workflow, history)
    if state.status == "ready":
        return _decision(state, "not_progressable", "workflow_not_started")
    if state.status == "running":
        return _decision(state, "not_progressable", "step_execution_in_progress")
    if state.status == "failed":
        return _decision(state, "stopped_failed", "latest_step_failed")

    if state.current_step_index == len(workflow.steps):
        return _decision(state, "workflow_complete", "last_step_succeeded")
    next_step = workflow.steps[state.current_step_index]
    return WorkflowProgressionDecision(
        decision="prepare_next_step",
        workflow_id=state.workflow_id,
        current_step_id=state.current_step_id,
        current_step_index=state.current_step_index,
        current_employee_id=state.current_employee_id,
        next_step_id=next_step.id,
        next_step_index=state.current_step_index + 1,
        next_employee_id=next_step.employee,
        reason="next_step_available",
    )


def _validate_compatibility(
    workflow: WorkflowDefinition, history: LoadedWorkflowExecutionHistory
) -> None:
    state = history.state
    if state.workflow_id != workflow.id:
        _raise_compatibility_error("workflow_identity")
    if not 1 <= state.current_step_index <= len(workflow.steps):
        _raise_compatibility_error("current_step_index")
    current_step = workflow.steps[state.current_step_index - 1]
    if state.current_step_id != current_step.id:
        _raise_compatibility_error("current_step_identity")
    if state.current_employee_id != current_step.employee:
        _raise_compatibility_error("current_employee_identity")

    step_positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    completed_positions: list[int] = []
    for step_id in state.completed_step_ids:
        try:
            completed_positions.append(step_positions[step_id])
        except KeyError:
            _raise_compatibility_error("completed_step_identity")
    compressed_positions = _compress_consecutive_positions(completed_positions)
    expected_last_position = (
        state.current_step_index
        if state.status == "succeeded"
        else state.current_step_index - 1
    )
    if compressed_positions != tuple(range(1, expected_last_position + 1)):
        _raise_compatibility_error("completed_step_order")
    if (
        state.status == "succeeded"
        and state.completed_step_ids[-1] != state.current_step_id
    ):
        _raise_compatibility_error("completed_step_order")


def _compress_consecutive_positions(positions: list[int]) -> tuple[int, ...]:
    """Keep duplicate history while comparing its workflow-position prefix."""
    compressed: list[int] = []
    for position in positions:
        if not compressed or compressed[-1] != position:
            compressed.append(position)
    return tuple(compressed)


def _raise_compatibility_error(
    classification: WorkflowProgressionCompatibilityClassification,
) -> None:
    raise WorkflowProgressionCompatibilityError(classification) from None


def _decision(
    state: WorkflowExecutionState,
    decision: WorkflowProgressionDecisionType,
    reason: str,
) -> WorkflowProgressionDecision:
    """Build a decision with no stale next-step identity."""
    return WorkflowProgressionDecision(
        decision=decision,
        workflow_id=state.workflow_id,
        current_step_id=state.current_step_id,
        current_step_index=state.current_step_index,
        current_employee_id=state.current_employee_id,
        next_step_id=None,
        next_step_index=None,
        next_employee_id=None,
        reason=reason,
    )
