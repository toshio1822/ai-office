"""Tests for deterministic OpenAI Responses dictionary payload conversion."""

from dataclasses import FrozenInstanceError, fields

import pytest

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import (
    OpenAIResponsesFunctionParameters,
    OpenAIResponsesFunctionProperty,
    OpenAIResponsesPayload,
    build_openai_responses_parameters_dict,
    build_openai_responses_payload_dict,
    build_openai_responses_payload_dict_from_invocation,
    build_openai_responses_property_dict,
    build_openai_responses_tool,
    build_openai_responses_tool_dict,
    build_openai_responses_tool_dicts,
)
from ai_office.tools import (
    ToolCatalog,
    ToolDefinition,
    ToolNotFoundError,
    ToolParameterDefinition,
)


def tool(
    name: str,
    parameters: tuple[ToolParameterDefinition, ...] = (),
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"  {name} description ✨\n",
        parameters=parameters,
    )


def property_definition(
    name: str = " query ",
    type_name: str = " string ",
    description: str = "  Description ✨\n",
) -> OpenAIResponsesFunctionProperty:
    return OpenAIResponsesFunctionProperty(name, type_name, description)


def test_property_dict_has_only_type_and_description_in_order() -> None:
    property_model = property_definition()

    result = build_openai_responses_property_dict(property_model)

    assert list(result) == ["type", "description"]
    assert result == {
        "type": " string ",
        "description": "  Description ✨\n",
    }
    assert "name" not in result
    assert property_model == property_definition()


def test_parameters_dict_preserves_order_required_duplicates_and_last_values() -> None:
    parameters = OpenAIResponsesFunctionParameters(
        type="object",
        properties=(
            property_definition("query", "string", "first"),
            property_definition("limit", "integer", "limit"),
            property_definition("query", "string", "second"),
        ),
        required=("query", "limit", "query"),
        additional_properties=False,
    )

    result = build_openai_responses_parameters_dict(parameters)

    assert list(result) == ["type", "properties", "required", "additionalProperties"]
    assert list(result["properties"]) == ["query", "limit"]
    assert result["properties"]["query"] == {
        "type": "string",
        "description": "second",
    }
    assert result["properties"]["limit"] == {
        "type": "integer",
        "description": "limit",
    }
    assert result["required"] == ["query", "limit", "query"]
    assert result["additionalProperties"] is False
    assert parameters.required == ("query", "limit", "query")


def test_parameters_dict_retains_empty_properties_and_required() -> None:
    parameters = OpenAIResponsesFunctionParameters("object", (), (), False)

    result = build_openai_responses_parameters_dict(parameters)

    assert result == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


@pytest.mark.parametrize("name", ["web_search", "FileRead"])
def test_tool_dict_converts_known_tools_as_ordinary_functions(name: str) -> None:
    source = tool(
        name,
        (
            ToolParameterDefinition("path", "  Path\n", "string", True),
        ),
    )
    tool_model = build_openai_responses_tool(source)

    result = build_openai_responses_tool_dict(tool_model)

    assert list(result) == ["type", "name", "description", "parameters", "strict"]
    assert result["type"] == "function"
    assert result["name"] == name
    assert result["description"] == source.description
    assert result["parameters"] == {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "  Path\n"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    assert result["strict"] is False
    assert tool_model == build_openai_responses_tool(source)


def test_tool_dicts_preserve_order_duplicates_and_empty_input() -> None:
    first = build_openai_responses_tool(tool("first"))
    second = build_openai_responses_tool(tool("second"))

    result = build_openai_responses_tool_dicts((second, first, second))

    assert [item["name"] for item in result] == ["second", "first", "second"]
    assert build_openai_responses_tool_dicts(()) == []


def test_payload_dict_is_ordered_and_preserves_values() -> None:
    tool_model = build_openai_responses_tool(tool("web_search"))
    payload = OpenAIResponsesPayload(
        model=" model ",
        instructions="\n  指示 ✨\n",
        input="  input\n\n  ",
        tools=(tool_model, tool_model),
    )

    result = build_openai_responses_payload_dict(payload)

    assert list(result) == ["model", "instructions", "input", "tools"]
    assert result["model"] == " model "
    assert result["instructions"] == "\n  指示 ✨\n"
    assert result["input"] == "  input\n\n  "
    assert [item["name"] for item in result["tools"]] == ["web_search", "web_search"]
    assert payload.tools == (tool_model, tool_model)


def test_payload_dict_retains_empty_strings_and_empty_tools() -> None:
    result = build_openai_responses_payload_dict(
        OpenAIResponsesPayload("", "", "", ())
    )

    assert result == {"model": "", "instructions": "", "input": "", "tools": []}


def test_payload_dict_from_invocation_uses_the_explicit_catalog() -> None:
    catalog = ToolCatalog(
        tools=(
            tool(
                "catalog_only",
                (ToolParameterDefinition("query", "Query", "string", True),),
            ),
        )
    )
    invocation = ModelInvocationRequest(
        model="model",
        system_instructions="instructions",
        task_instructions="input",
        allowed_tools=("catalog_only", "catalog_only"),
    )

    result = build_openai_responses_payload_dict_from_invocation(invocation, catalog)

    assert result["model"] == "model"
    assert result["instructions"] == "instructions"
    assert result["input"] == "input"
    assert [item["name"] for item in result["tools"]] == [
        "catalog_only",
        "catalog_only",
    ]
    assert result["tools"][0]["parameters"]["required"] == ["query"]
    assert invocation.allowed_tools == ("catalog_only", "catalog_only")
    assert catalog.tools[0].name == "catalog_only"


def test_payload_dict_from_invocation_propagates_missing_tool_error() -> None:
    invocation = ModelInvocationRequest("model", "instructions", "input", ("missing",))

    with pytest.raises(ToolNotFoundError, match="Tool not found: missing"):
        build_openai_responses_payload_dict_from_invocation(
            invocation, ToolCatalog(tools=())
        )


def test_payload_model_remains_immutable_and_has_only_payload_fields() -> None:
    payload = OpenAIResponsesPayload("model", "instructions", "input", ())

    assert tuple(field.name for field in fields(payload)) == (
        "model",
        "instructions",
        "input",
        "tools",
    )
    with pytest.raises(FrozenInstanceError):
        payload.model = "other"  # type: ignore[misc]
