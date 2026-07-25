"""Tests for explicit provider-independent paid-execution approval."""

from dataclasses import FrozenInstanceError, replace

import pytest

from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationExecutionApprovalError,
    ModelInvocationRequest,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
    validate_model_invocation_execution_approval,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition


def request(
    *,
    model: str = "model",
    system_instructions: str = "system",
    task_instructions: str = "task",
    allowed_tools: tuple[str, ...] = ("search",),
) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model=model,
        system_instructions=system_instructions,
        task_instructions=task_instructions,
        allowed_tools=allowed_tools,
    )


def tool(
    *,
    name: str = "search",
    description: str = "search description",
    parameter_name: str = "query",
    parameter_description: str = "query description",
    parameter_type: str = "string",
    required: bool = True,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        parameters=(
            ToolParameterDefinition(
                parameter_name,
                parameter_description,
                parameter_type,
                required,
            ),
        ),
    )


def approval(
    invocation: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
) -> ModelInvocationExecutionApproval:
    return approve_model_invocation_execution(
        invocation,
        resolved_tools,
        provider="openai",
        approved_by="reviewer",
        approval_id="approval-123",
    )


def test_approval_is_immutable_and_helper_binds_nonempty_metadata() -> None:
    invocation = request()
    value = approval(invocation, (tool(),))

    assert value.approved is True
    assert value.provider == "openai"
    assert value.approved_by == "reviewer"
    assert value.approval_id == "approval-123"
    assert value.request_fingerprint == build_model_invocation_execution_fingerprint(
        invocation,
        (tool(),),
    )
    with pytest.raises(FrozenInstanceError):
        value.approved_by = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["provider", "approved_by", "approval_id"])
def test_helper_rejects_empty_required_metadata(field: str) -> None:
    values = {"provider": "openai", "approved_by": "reviewer", "approval_id": "id"}
    values[field] = ""

    with pytest.raises(
        ModelInvocationExecutionApprovalError,
        match="^model invocation execution is not approved$",
    ):
        approve_model_invocation_execution(request(), (tool(),), **values)


def test_fingerprint_is_deterministic_and_order_sensitive_without_exposing_inputs() -> (
    None
):
    invocation = request(
        system_instructions="system secret 日本語",
        task_instructions="task secret 😀",
        allowed_tools=("search", "read", "search"),
    )
    tools = (tool(name="search"), tool(name="read"), tool(name="search"))
    fingerprint = build_model_invocation_execution_fingerprint(invocation, tools)

    assert fingerprint == build_model_invocation_execution_fingerprint(
        invocation, tools
    )
    assert len(fingerprint) == 64
    assert fingerprint != build_model_invocation_execution_fingerprint(
        replace(invocation, model="other"), tools
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        replace(invocation, system_instructions="other"), tools
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        replace(invocation, task_instructions="other"), tools
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        replace(invocation, allowed_tools=("read", "search", "search")), tools
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        invocation, (tools[1], tools[0], tools[2])
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        invocation, (replace(tools[0], description="other"), tools[1], tools[2])
    )
    assert fingerprint != build_model_invocation_execution_fingerprint(
        invocation,
        (
            ToolDefinition(
                name="search",
                description="search description",
                parameters=(
                    ToolParameterDefinition("query", "other", "integer", False),
                ),
            ),
            tools[1],
            tools[2],
        ),
    )
    assert "system secret" not in fingerprint
    assert "task secret" not in fingerprint
    assert "synthetic-key" not in fingerprint


@pytest.mark.parametrize(
    "change",
    [
        lambda value: replace(value, approved=False),
        lambda value: replace(value, approved=1),
        lambda value: replace(value, provider="OpenAI"),
        lambda value: replace(value, request_fingerprint="stale"),
        lambda value: replace(value, approved_by=""),
        lambda value: replace(value, approval_id=""),
    ],
)
def test_validation_rejects_every_invalid_approval_without_details(
    change: object,
) -> None:
    invocation = request()
    tools = (tool(),)
    value = approval(invocation, tools)

    with pytest.raises(
        ModelInvocationExecutionApprovalError,
        match="^model invocation execution is not approved$",
    ) as error:
        validate_model_invocation_execution_approval(
            invocation,
            tools,
            change(value),  # type: ignore[operator]
            provider="openai",
        )

    assert error.value.__cause__ is None


def test_validation_accepts_current_approval_without_mutating_inputs() -> None:
    invocation = request()
    tools = (tool(),)
    value = approval(invocation, tools)

    validate_model_invocation_execution_approval(
        invocation,
        tools,
        value,
        provider="openai",
    )

    assert invocation == request()
    assert tools == (tool(),)
    assert value == approval(invocation, tools)


def test_non_json_tool_definition_value_propagates_standard_type_error() -> None:
    invalid_tool = ToolDefinition(
        name="search",
        description="description",
        parameters=(
            ToolParameterDefinition("query", "description", "string", object()),
        ),
    )

    with pytest.raises(TypeError):
        build_model_invocation_execution_fingerprint(request(), (invalid_tool,))
