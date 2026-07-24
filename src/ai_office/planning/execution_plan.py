"""Execution-plan models and deterministic planning functions."""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from ai_office.definitions.employee import LoadedEmployee
from ai_office.definitions.workflow import (
    LoadedWorkflow,
    validate_workflow_employee_references,
)

_WORKFLOW_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ExecutionPlanStep(BaseModel):
    """One immutable, ordered step in an execution plan."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=1)
    step_id: str
    step_name: str
    employee_id: str
    instructions: str


class ExecutionPlan(BaseModel):
    """An immutable execution plan independent of definition file locations."""

    model_config = ConfigDict(frozen=True)

    workflow_id: str
    workflow_name: str
    steps: tuple[ExecutionPlanStep, ...]


class WorkflowSelectionError(ValueError):
    """An error that can be shown to a workflow-plan user."""


def find_workflow_by_id(
    workflows: Sequence[LoadedWorkflow], workflow_id: str
) -> LoadedWorkflow:
    """Select one loaded workflow by an unmodified, valid workflow ID."""
    if not _WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
        raise WorkflowSelectionError(
            f"invalid workflow id {workflow_id!r}; expected lowercase letters, "
            "digits, and single hyphens"
        )

    for workflow in workflows:
        if workflow.definition.id == workflow_id:
            return workflow

    raise WorkflowSelectionError(f"workflow {workflow_id!r} was not found")


def build_execution_plan(
    workflow: LoadedWorkflow, employees: Sequence[LoadedEmployee]
) -> ExecutionPlan:
    """Build a detached plan after defensively validating employee references."""
    validate_workflow_employee_references([workflow], list(employees))

    return ExecutionPlan(
        workflow_id=workflow.definition.id,
        workflow_name=workflow.definition.name,
        steps=tuple(
            ExecutionPlanStep(
                index=index,
                step_id=step.id,
                step_name=step.name,
                employee_id=step.employee,
                instructions=step.instructions,
            )
            for index, step in enumerate(workflow.definition.steps, start=1)
        ),
    )
