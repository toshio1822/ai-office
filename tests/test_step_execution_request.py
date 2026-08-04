"""Tests for deterministic, detached step execution requests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_office.definitions.employee import EmployeeDefinition, LoadedEmployee
from ai_office.planning.execution_plan import ExecutionPlan, ExecutionPlanStep
from ai_office.planning.step_execution_request import (
    EmployeeSelectionError,
    StepSelectionError,
    build_step_execution_request,
    find_employee_by_id,
    find_plan_step_by_index,
)


def employees() -> list[LoadedEmployee]:
    return [
        LoadedEmployee(
            source_path=Path("employees/researcher.yaml"),
            definition=EmployeeDefinition(
                id="general-researcher",
                name="General Researcher",
                role="Organizes information.\nSeparates facts.",
                instructions="Work on the assigned step.\nKeep facts separate.",
                model="codex",
                allowed_tools=["search", "read"],
            ),
        )
    ]


def plan() -> ExecutionPlan:
    return ExecutionPlan(
        workflow_id="research-and-summarize",
        workflow_name="Research and Summarize",
        steps=(
            ExecutionPlanStep(
                index=1,
                step_id="research",
                step_name="Research",
                employee_id="general-researcher",
                instructions="Gather relevant information.",
            ),
            ExecutionPlanStep(
                index=2,
                step_id="summarize",
                step_name="Summarize",
                employee_id="general-researcher",
                instructions="Summarize the information.",
            ),
        ),
    )


def test_find_plan_step_by_index_selects_first_and_second_steps() -> None:
    execution_plan = plan()

    assert find_plan_step_by_index(execution_plan, 1) is execution_plan.steps[0]
    assert find_plan_step_by_index(execution_plan, 2) is execution_plan.steps[1]


@pytest.mark.parametrize("step_index", [0, -1, 3])
def test_find_plan_step_by_index_rejects_out_of_range_indexes(step_index: int) -> None:
    with pytest.raises(StepSelectionError, match="out of range"):
        find_plan_step_by_index(plan(), step_index)


def test_find_plan_step_by_index_rejects_empty_plan() -> None:
    empty_plan = ExecutionPlan(
        workflow_id="empty", workflow_name="Empty", steps=()
    )

    with pytest.raises(StepSelectionError, match="out of range"):
        find_plan_step_by_index(empty_plan, 1)


def test_find_employee_by_id_uses_exact_matching() -> None:
    loaded_employees = employees()

    assert (
        find_employee_by_id(loaded_employees, "general-researcher")
        is loaded_employees[0]
    )
    for employee_id in (
        " general-researcher",
        "general-researcher ",
        "GENERAL-RESEARCHER",
    ):
        with pytest.raises(EmployeeSelectionError, match="was not found"):
            find_employee_by_id(loaded_employees, employee_id)


def test_find_employee_by_id_rejects_missing_employee() -> None:
    with pytest.raises(EmployeeSelectionError, match="missing-employee"):
        find_employee_by_id(employees(), "missing-employee")


def test_build_step_execution_request_copies_values_and_separates_instructions(
) -> None:
    request = build_step_execution_request(plan(), 2, employees())

    assert request.workflow_id == "research-and-summarize"
    assert request.step_index == 2
    assert request.step_id == "summarize"
    assert request.employee_name == "General Researcher"
    assert request.employee_instructions == (
        "Work on the assigned step.\nKeep facts separate."
    )
    assert request.step_instructions == "Summarize the information."
    assert request.allowed_tools == ("search", "read")
    assert isinstance(request.allowed_tools, tuple)


def test_request_is_frozen_and_detached_from_definitions() -> None:
    loaded_employees = employees()
    request = build_step_execution_request(plan(), 1, loaded_employees)

    loaded_employees[0].definition.name = "Changed name"
    loaded_employees[0].definition.allowed_tools.append("write")

    assert request.employee_name == "General Researcher"
    assert request.allowed_tools == ("search", "read")
    assert "definition" not in type(request).model_fields
    assert "source_path" not in type(request).model_fields
    assert "plan" not in type(request).model_fields
    assert not hasattr(request, "plan")
    assert not hasattr(request, "provenance")
    with pytest.raises(ValidationError):
        request.employee_name = "Changed name"
    with pytest.raises(ValidationError):
        request.allowed_tools += ("write",)


def test_build_step_execution_request_is_equal_for_equal_inputs() -> None:
    assert (
        build_step_execution_request(plan(), 1, employees())
        == build_step_execution_request(plan(), 1, employees())
    )
