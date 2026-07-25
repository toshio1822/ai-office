"""Tests for OpenAI Responses payload model assembly."""

from dataclasses import FrozenInstanceError, fields

import pytest

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import (
    OpenAIResponsesPayload,
    OpenAIResponsesRequest,
    build_openai_responses_payload,
    build_openai_responses_payload_from_invocation,
    build_openai_responses_tool,
)
from ai_office.tools import (
    ToolCatalog,
    ToolDefinition,
    ToolNotFoundError,
    ToolParameterDefinition,
)


def tool(name: str = "web_search") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="  Search description.  ",
        parameters=(
            ToolParameterDefinition("query", "Search query.", "string", True),
        ),
    )


def invocation(
    *,
    model: str = " model ",
    instructions: str = "\n  指示  ✨\n",
    input: str = "  Input\n\n  ",
    allowed_tools: tuple[str, ...] = ("web_search",),
) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model=model,
        system_instructions=instructions,
        task_instructions=input,
        allowed_tools=allowed_tools,
    )


def test_payload_is_frozen_and_has_only_payload_fields() -> None:
    payload = OpenAIResponsesPayload("model", "instructions", "input", ())

    assert tuple(field.name for field in fields(payload)) == (
        "model",
        "instructions",
        "input",
        "tools",
    )
    assert isinstance(payload.tools, tuple)
    assert not hasattr(payload, "allowed_tool_names")
    assert not hasattr(payload, "headers")
    assert not hasattr(payload, "runtime")
    with pytest.raises(FrozenInstanceError):
        payload.model = "other"  # type: ignore[misc]


def test_build_payload_preserves_values_and_tools() -> None:
    request = OpenAIResponsesRequest(
        model=" model ",
        instructions="\n  指示  ✨\n",
        input="  Input\n\n  ",
        allowed_tool_names=("unresolved",),
    )
    tools = (build_openai_responses_tool(tool()),) * 2

    payload = build_openai_responses_payload(request, tools)

    assert payload.model == request.model
    assert payload.instructions == request.instructions
    assert payload.input == request.input
    assert payload.tools == tools
    assert payload.tools[0].name == "web_search"
    assert payload.tools[0] == payload.tools[1]
    assert request.allowed_tool_names == ("unresolved",)


def test_build_payload_preserves_empty_values_and_empty_tools() -> None:
    payload = build_openai_responses_payload(
        OpenAIResponsesRequest("", "", "", ()), ()
    )

    assert payload == OpenAIResponsesPayload("", "", "", ())


def test_build_payload_from_invocation_uses_supplied_catalog() -> None:
    catalog = ToolCatalog(tools=(tool("catalog_only"),))
    source = invocation(allowed_tools=("catalog_only",))

    payload = build_openai_responses_payload_from_invocation(source, catalog)

    assert payload.model == source.model
    assert payload.instructions == source.system_instructions
    assert payload.input == source.task_instructions
    assert tuple(item.name for item in payload.tools) == ("catalog_only",)
    assert payload.tools[0].parameters.required == ("query",)
    assert source.allowed_tools == ("catalog_only",)
    assert catalog.tools == (tool("catalog_only"),)


def test_build_payload_from_invocation_preserves_order_duplicates_and_empty_tools(
) -> None:
    catalog = ToolCatalog(tools=(tool("first"), tool("second")))

    payload = build_openai_responses_payload_from_invocation(
        invocation(allowed_tools=("second", "first", "second")), catalog
    )
    empty_payload = build_openai_responses_payload_from_invocation(
        invocation(allowed_tools=()), catalog
    )

    assert tuple(item.name for item in payload.tools) == ("second", "first", "second")
    assert empty_payload.tools == ()


def test_build_payload_from_invocation_propagates_missing_tool_error() -> None:
    with pytest.raises(ToolNotFoundError, match="Tool not found: missing"):
        build_openai_responses_payload_from_invocation(
            invocation(allowed_tools=("missing",)), ToolCatalog(tools=())
        )
