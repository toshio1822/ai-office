"""Tests for provider-independent model invocation requests."""

from dataclasses import FrozenInstanceError, fields

import pytest

from ai_office.invocation import (
    ModelInvocationRequest,
    build_model_invocation_request,
)
from ai_office.planning.step_execution_request import StepExecutionRequest


def step_request() -> StepExecutionRequest:
    return StepExecutionRequest(
        workflow_id="research-and-summarize",
        workflow_name="Research and Summarize",
        step_index=1,
        step_id="research",
        step_name="Research",
        employee_id="general-researcher",
        employee_name="General Researcher",
        employee_role="Researcher",
        model=" codex-preview ",
        allowed_tools=("search", "read", "search"),
        employee_instructions="  System instruction.\n\nKeep whitespace.  ",
        step_instructions="  Task instruction.\n\nKeep this separate.  ",
    )


def test_model_invocation_request_is_frozen_and_preserves_tool_order() -> None:
    request = build_model_invocation_request(step_request())

    assert isinstance(request, ModelInvocationRequest)
    assert request.allowed_tools == ("search", "read", "search")
    assert request.upstream_inputs == ()
    assert isinstance(request.allowed_tools, tuple)
    with pytest.raises(FrozenInstanceError):
        request.model = "other"


def test_model_invocation_request_copies_values_without_combining_instructions(
) -> None:
    source = step_request()
    request = build_model_invocation_request(source)

    assert request.model == " codex-preview "
    assert request.system_instructions == source.employee_instructions
    assert request.task_instructions == source.step_instructions
    assert "Task instruction" not in request.system_instructions
    assert "System instruction" not in request.task_instructions
    assert tuple(field.name for field in fields(request)) == (
        "model",
        "system_instructions",
        "task_instructions",
        "allowed_tools",
        "upstream_inputs",
    )
    for field_name in (
        "workflow_id",
        "workflow_name",
        "step_index",
        "step_id",
        "step_name",
        "employee_id",
        "employee_name",
        "employee_role",
        "execution_plan",
        "execution_plan_step",
        "step_request",
        "workflow_definition",
        "employee_definition",
        "source_path",
        "provenance",
    ):
        assert not hasattr(request, field_name)


def test_model_invocation_request_handles_empty_tools_and_is_deterministic() -> None:
    source = step_request().model_copy(update={"allowed_tools": ()})

    first = build_model_invocation_request(source)
    second = build_model_invocation_request(source)

    assert first.allowed_tools == ()
    assert first == second
    assert first is not source
