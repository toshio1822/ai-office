"""Tests for deterministic execution-plan generation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_office.definitions.employee import EmployeeDefinition, LoadedEmployee
from ai_office.definitions.workflow import (
    LoadedWorkflow,
    WorkflowDefinition,
    WorkflowLoadError,
)
from ai_office.planning.execution_plan import (
    WorkflowSelectionError,
    build_execution_plan,
    find_workflow_by_id,
)


def loaded_employee(employee_id: str = "general-researcher") -> LoadedEmployee:
    return LoadedEmployee(
        source_path=Path(f"{employee_id}.yaml"),
        definition=EmployeeDefinition(
            id=employee_id,
            name="General Researcher",
            role="Organizes information.",
            instructions="Work on the assigned step.",
            model="codex",
            allowed_tools=[],
        ),
    )


def loaded_workflow(source_path: Path = Path("workflow.yaml")) -> LoadedWorkflow:
    return LoadedWorkflow(
        source_path=source_path,
        definition=WorkflowDefinition(
            id="research-and-summarize",
            name="Research and Summarize",
            description="Researches a topic and summarizes it.",
            steps=[
                {
                    "id": "research",
                    "name": "Research",
                    "employee": "general-researcher",
                    "instructions": "Gather relevant information.",
                },
                {
                    "id": "summarize",
                    "name": "Summarize",
                    "employee": "general-researcher",
                    "instructions": "Summarize the information.",
                },
            ],
        ),
    )


def test_build_execution_plan_copies_ordered_execution_values() -> None:
    plan = build_execution_plan(loaded_workflow(), [loaded_employee()])

    assert plan.workflow_id == "research-and-summarize"
    assert plan.workflow_name == "Research and Summarize"
    assert [step.index for step in plan.steps] == [1, 2]
    assert [step.step_id for step in plan.steps] == ["research", "summarize"]
    assert [step.step_name for step in plan.steps] == ["Research", "Summarize"]
    assert [step.employee_id for step in plan.steps] == [
        "general-researcher",
        "general-researcher",
    ]
    assert [step.instructions for step in plan.steps] == [
        "Gather relevant information.",
        "Summarize the information.",
    ]


def test_build_execution_plan_uses_one_based_index_for_one_step_workflow() -> None:
    workflow = loaded_workflow()
    workflow.definition.steps = workflow.definition.steps[:1]

    plan = build_execution_plan(workflow, [loaded_employee()])

    assert len(plan.steps) == 1
    assert plan.steps[0].index == 1


def test_build_execution_plan_is_equal_for_identical_definitions_at_paths() -> None:
    employees = [loaded_employee()]

    first = build_execution_plan(
        loaded_workflow(Path("first/workflow.yaml")), employees
    )
    second = build_execution_plan(
        loaded_workflow(Path("another/workflow.yaml")), employees
    )

    assert first == second
    assert "source_path" not in type(first).model_fields
    assert not hasattr(first, "source_path")


def test_build_execution_plan_is_detached_from_definition_models() -> None:
    workflow = loaded_workflow()
    plan = build_execution_plan(workflow, [loaded_employee()])

    workflow.definition.name = "Changed name"
    workflow.definition.steps[0].instructions = "Changed instructions"

    assert plan.workflow_name == "Research and Summarize"
    assert plan.steps[0].instructions == "Gather relevant information."
    assert not hasattr(plan, "definition")
    assert not hasattr(plan.steps[0], "definition")


def test_execution_plan_models_are_frozen() -> None:
    plan = build_execution_plan(loaded_workflow(), [loaded_employee()])

    with pytest.raises(ValidationError):
        plan.workflow_name = "Changed name"

    with pytest.raises(ValidationError):
        plan.steps[0].instructions = "Changed instructions"


def test_build_execution_plan_rejects_undefined_employee() -> None:
    with pytest.raises(WorkflowLoadError, match="general-researcher"):
        build_execution_plan(loaded_workflow(), [])


def test_find_workflow_by_id_selects_requested_workflow() -> None:
    first = loaded_workflow()
    second = LoadedWorkflow(
        source_path=Path("other.yaml"),
        definition=first.definition.model_copy(update={"id": "other-workflow"}),
    )

    assert find_workflow_by_id([first, second], "other-workflow") is second


@pytest.mark.parametrize(
    "workflow_id",
    [
        "Uppercase",
        "contains_underscore",
        " bad",
        "-workflow",
        "workflow-",
        "two--hyphens",
        "workflow ",
        " workflow",
    ],
)
def test_find_workflow_by_id_rejects_invalid_id(workflow_id: str) -> None:
    with pytest.raises(WorkflowSelectionError, match="invalid workflow id"):
        find_workflow_by_id([loaded_workflow()], workflow_id)


def test_find_workflow_by_id_rejects_missing_workflow_and_empty_list() -> None:
    with pytest.raises(WorkflowSelectionError, match="was not found"):
        find_workflow_by_id([loaded_workflow()], "missing-workflow")

    with pytest.raises(WorkflowSelectionError, match="was not found"):
        find_workflow_by_id([], "missing-workflow")
