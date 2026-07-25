"""Pure preparation of one explicitly approved next workflow step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.storage.workflow_execution_history import LoadedWorkflowExecutionHistory

NextStepPreparationClassification = Literal[
    "decision_type",
    "workflow_identity",
    "current_step_identity",
    "next_step_identity",
    "employee_identity",
    "approval_required",
    "approval_identity",
]

_COMPATIBILITY_ERROR_MESSAGE = "next-step preparation inputs are incompatible"
_APPROVAL_ERROR_MESSAGE = "next-step preparation approval is invalid"


@dataclass(frozen=True)
class NextStepPreparationApproval:
    """Explicit approval bound to one exact workflow progression decision."""

    approved: bool
    workflow_id: str
    current_step_id: str
    current_step_index: int
    next_step_id: str
    next_step_index: int
    next_employee_id: str


@dataclass(frozen=True)
class PreparedWorkflowStep:
    """Provider-independent immutable data for one approved workflow step."""

    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    employee_instructions: str
    step_instructions: str
    model: str
    allowed_tool_names: tuple[str, ...]


@dataclass(frozen=True)
class NextStepPreparationFailureDetail:
    """Safe classification for an expected preparation failure."""

    classification: NextStepPreparationClassification


class NextStepPreparationError(ValueError):
    """Raised when a next step cannot be prepared safely."""


class NextStepPreparationCompatibilityError(NextStepPreparationError):
    """Raised when workflow, history, decision, or employee disagree."""

    def __init__(self, classification: NextStepPreparationClassification) -> None:
        super().__init__(_COMPATIBILITY_ERROR_MESSAGE)
        self.detail = NextStepPreparationFailureDetail(classification)


class NextStepPreparationApprovalError(NextStepPreparationError):
    """Raised when explicit approval is absent or bound to different identity."""

    def __init__(self, classification: NextStepPreparationClassification) -> None:
        super().__init__(_APPROVAL_ERROR_MESSAGE)
        self.detail = NextStepPreparationFailureDetail(classification)


def prepare_approved_next_workflow_step(
    workflow: WorkflowDefinition,
    history: LoadedWorkflowExecutionHistory,
    decision: WorkflowProgressionDecision,
    approval: NextStepPreparationApproval,
    employee: EmployeeDefinition,
) -> PreparedWorkflowStep:
    """Return detached data for the exact next step authorized by a decision."""
    _validate_decision_and_history(workflow, history, decision)
    _validate_approval(decision, approval)
    next_step = workflow.steps[decision.next_step_index - 1]  # type: ignore[index]
    if employee.id != next_step.employee:
        _raise_compatibility_error("employee_identity")
    return PreparedWorkflowStep(
        workflow_id=workflow.id,
        step_id=next_step.id,
        step_index=decision.next_step_index,  # type: ignore[arg-type]
        employee_id=employee.id,
        employee_instructions=employee.instructions,
        step_instructions=next_step.instructions,
        model=employee.model,
        allowed_tool_names=tuple(employee.allowed_tools),
    )


def _validate_decision_and_history(
    workflow: WorkflowDefinition,
    history: LoadedWorkflowExecutionHistory,
    decision: WorkflowProgressionDecision,
) -> None:
    state = history.state
    if decision.decision != "prepare_next_step":
        _raise_compatibility_error("decision_type")
    if not (workflow.id == state.workflow_id == decision.workflow_id):
        _raise_compatibility_error("workflow_identity")
    if (
        isinstance(state.current_step_index, bool)
        or not isinstance(state.current_step_index, int)
        or not 1 <= state.current_step_index <= len(workflow.steps)
    ):
        _raise_compatibility_error("current_step_identity")
    current_step = workflow.steps[state.current_step_index - 1]
    if (
        state.current_step_id != current_step.id
        or state.current_employee_id != current_step.employee
    ):
        _raise_compatibility_error("current_step_identity")
    if state.status != "succeeded" or state.current_step_index == len(workflow.steps):
        _raise_compatibility_error("next_step_identity")
    if not (
        decision.current_step_id == state.current_step_id
        and decision.current_step_index == state.current_step_index
        and decision.current_employee_id == state.current_employee_id
    ):
        _raise_compatibility_error("current_step_identity")
    if not _is_valid_next_identity(decision):
        _raise_compatibility_error("next_step_identity")
    if decision.next_step_index != state.current_step_index + 1:
        _raise_compatibility_error("next_step_identity")
    if not 1 <= decision.next_step_index <= len(workflow.steps):
        _raise_compatibility_error("next_step_identity")
    next_step = workflow.steps[decision.next_step_index - 1]
    if (
        decision.next_step_id != next_step.id
        or decision.next_employee_id != next_step.employee
    ):
        _raise_compatibility_error("next_step_identity")


def _is_valid_next_identity(decision: WorkflowProgressionDecision) -> bool:
    return (
        isinstance(decision.next_step_id, str)
        and bool(decision.next_step_id)
        and isinstance(decision.next_step_index, int)
        and not isinstance(decision.next_step_index, bool)
        and decision.next_step_index > 0
        and isinstance(decision.next_employee_id, str)
        and bool(decision.next_employee_id)
    )


def _validate_approval(
    decision: WorkflowProgressionDecision, approval: NextStepPreparationApproval
) -> None:
    if approval.approved is not True:
        _raise_approval_error("approval_required")
    if not _has_valid_approval_values(approval):
        _raise_approval_error("approval_identity")
    if not (
        approval.workflow_id == decision.workflow_id
        and approval.current_step_id == decision.current_step_id
        and approval.current_step_index == decision.current_step_index
        and approval.next_step_id == decision.next_step_id
        and approval.next_step_index == decision.next_step_index
        and approval.next_employee_id == decision.next_employee_id
    ):
        _raise_approval_error("approval_identity")


def _has_valid_approval_values(approval: NextStepPreparationApproval) -> bool:
    return all(
        isinstance(value, str) and bool(value)
        for value in (
            approval.workflow_id,
            approval.current_step_id,
            approval.next_step_id,
            approval.next_employee_id,
        )
    ) and all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (approval.current_step_index, approval.next_step_index)
    )


def _raise_compatibility_error(
    classification: NextStepPreparationClassification,
) -> None:
    raise NextStepPreparationCompatibilityError(classification) from None


def _raise_approval_error(classification: NextStepPreparationClassification) -> None:
    raise NextStepPreparationApprovalError(classification) from None
