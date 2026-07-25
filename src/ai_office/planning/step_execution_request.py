"""Structured, detached requests for one planned workflow step."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ai_office.definitions.employee import LoadedEmployee
from ai_office.planning.execution_plan import ExecutionPlan, ExecutionPlanStep


class StepExecutionRequest(BaseModel):
    """Immutable input for a future adapter to execute one planned step."""

    model_config = ConfigDict(frozen=True)

    workflow_id: str
    # A persisted-start execution has no workflow display name.  Planning still
    # supplies it, while later execution boundaries must not invent one.
    workflow_name: str | None = None
    step_index: int = Field(ge=1)
    step_id: str
    # A persisted-start execution has no step display name.  Planning still
    # supplies it, while later execution boundaries must not invent one.
    step_name: str | None = None
    employee_id: str
    employee_name: str
    employee_role: str
    model: str
    allowed_tools: tuple[str, ...]
    employee_instructions: str
    step_instructions: str


class StepSelectionError(ValueError):
    """An error that can be shown to a step-request user."""


class EmployeeSelectionError(ValueError):
    """An error that can be shown to a step-request user."""


def find_plan_step_by_index(
    plan: ExecutionPlan, step_index: int
) -> ExecutionPlanStep:
    """Select one plan step using an explicit one-based index."""
    if step_index < 1 or step_index > len(plan.steps):
        raise StepSelectionError(
            f"step index {step_index} is out of range for a plan with "
            f"{len(plan.steps)} step(s)"
        )
    return plan.steps[step_index - 1]


def find_employee_by_id(
    employees: Sequence[LoadedEmployee], employee_id: str
) -> LoadedEmployee:
    """Select an employee only when its ID exactly matches the request."""
    for employee in employees:
        if employee.definition.id == employee_id:
            return employee
    raise EmployeeSelectionError(f"employee {employee_id!r} was not found")


def build_step_execution_request(
    plan: ExecutionPlan,
    step_index: int,
    employees: Sequence[LoadedEmployee],
) -> StepExecutionRequest:
    """Build a detached immutable request from one plan step and employee."""
    step = find_plan_step_by_index(plan, step_index)
    employee = find_employee_by_id(employees, step.employee_id)
    definition = employee.definition

    return StepExecutionRequest(
        workflow_id=plan.workflow_id,
        workflow_name=plan.workflow_name,
        step_index=step.index,
        step_id=step.step_id,
        step_name=step.step_name,
        employee_id=definition.id,
        employee_name=definition.name,
        employee_role=definition.role,
        model=definition.model,
        allowed_tools=tuple(definition.allowed_tools),
        employee_instructions=definition.instructions,
        step_instructions=step.instructions,
    )
