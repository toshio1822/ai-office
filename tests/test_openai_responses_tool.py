"""Tests for OpenAI Responses function-tool schema adapters."""

from dataclasses import FrozenInstanceError, fields

import pytest

from ai_office.providers.openai import (
    OpenAIResponsesFunctionParameters,
    OpenAIResponsesFunctionProperty,
    OpenAIResponsesFunctionTool,
    build_openai_responses_function_parameters,
    build_openai_responses_function_property,
    build_openai_responses_tool,
    build_openai_responses_tools,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition


def test_openai_responses_function_models_are_frozen_with_expected_fields() -> None:
    property_definition = OpenAIResponsesFunctionProperty(
        name="query", type="string", description="A query."
    )
    parameters = OpenAIResponsesFunctionParameters(
        type="object",
        properties=(property_definition,),
        required=("query",),
        additional_properties=False,
    )
    tool = OpenAIResponsesFunctionTool(
        type="function",
        name="web_search",
        description="Search the web.",
        parameters=parameters,
        strict=False,
    )

    assert [field.name for field in fields(property_definition)] == [
        "name",
        "type",
        "description",
    ]
    assert [field.name for field in fields(parameters)] == [
        "type",
        "properties",
        "required",
        "additional_properties",
    ]
    assert [field.name for field in fields(tool)] == [
        "type",
        "name",
        "description",
        "parameters",
        "strict",
    ]

    with pytest.raises(FrozenInstanceError):
        property_definition.name = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        parameters.type = "array"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        tool.strict = True  # type: ignore[misc]


def test_build_function_property_preserves_parameter_values() -> None:
    parameter = ToolParameterDefinition(
        name="  名称  ",
        type=" custom type ",
        description="\n  説明  ✨\n",
        required=True,
    )

    property_definition = build_openai_responses_function_property(parameter)

    assert property_definition == OpenAIResponsesFunctionProperty(
        name="  名称  ",
        type=" custom type ",
        description="\n  説明  ✨\n",
    )
    assert parameter.required is True


def test_build_function_parameters_preserves_order_duplicates_and_required() -> None:
    parameters = (
        ToolParameterDefinition(" first ", "First.", " string ", True),
        ToolParameterDefinition("optional", "Optional.", "integer", False),
        ToolParameterDefinition(" first ", "Repeated.", "boolean", True),
    )

    schema = build_openai_responses_function_parameters(parameters)

    assert schema.type == "object"
    assert schema.additional_properties is False
    assert schema.properties == (
        OpenAIResponsesFunctionProperty(" first ", " string ", "First."),
        OpenAIResponsesFunctionProperty("optional", "integer", "Optional."),
        OpenAIResponsesFunctionProperty(" first ", "boolean", "Repeated."),
    )
    assert schema.required == (" first ", " first ")
    assert parameters[1].required is False


def test_build_function_parameters_handles_no_parameters() -> None:
    schema = build_openai_responses_function_parameters(())

    assert schema == OpenAIResponsesFunctionParameters(
        type="object",
        properties=(),
        required=(),
        additional_properties=False,
    )


@pytest.mark.parametrize("name", ["web_search", "FileRead"])
def test_build_tool_treats_catalog_tool_names_as_regular_functions(name: str) -> None:
    tool = ToolDefinition(
        name=name,
        description="  Static description.  ",
        parameters=(
            ToolParameterDefinition("query", "A value.", "string", True),
        ),
    )

    result = build_openai_responses_tool(tool)

    assert result.type == "function"
    assert result.name == name
    assert result.description == "  Static description.  "
    assert result.strict is False
    assert result.parameters.required == ("query",)


def test_build_tools_preserves_input_order_and_duplicates() -> None:
    first = ToolDefinition(" web_search ", "First.", ())
    second = ToolDefinition("FileRead", "Second.", ())

    result = build_openai_responses_tools((first, second, first))

    assert isinstance(result, tuple)
    assert tuple(tool.name for tool in result) == (
        " web_search ",
        "FileRead",
        " web_search ",
    )
    assert tuple(tool.description for tool in result) == ("First.", "Second.", "First.")
    assert build_openai_responses_tools(()) == ()
