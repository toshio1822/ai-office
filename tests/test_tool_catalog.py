"""Tests for provider-independent tool catalog resolution."""

from dataclasses import FrozenInstanceError, fields

import pytest

from ai_office.tools import (
    DuplicateToolNameError,
    ToolCatalog,
    ToolDefinition,
    ToolNotFoundError,
    ToolParameterDefinition,
    find_tool_by_name,
    resolve_tool_names,
    validate_tool_catalog,
)


def parameter(name: str = "query") -> ToolParameterDefinition:
    return ToolParameterDefinition(
        name=name,
        description="  Describe  this.\t\n",
        type=" custom-type ",
        required=True,
    )


def tool(
    name: str,
    parameters: tuple[ToolParameterDefinition, ...] = (),
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="  Tool description.  \n",
        parameters=parameters,
    )


def catalog() -> ToolCatalog:
    return ToolCatalog(
        tools=(
            tool("web_search", (parameter(),)),
            tool("FileRead"),
            tool("web_search "),
        )
    )


def test_tool_models_are_frozen_and_preserve_values() -> None:
    tool_parameter = parameter()
    definition = tool("web_search", (tool_parameter, parameter("limit")))
    tool_catalog = ToolCatalog(tools=(definition,))

    assert tuple(field.name for field in fields(tool_parameter)) == (
        "name",
        "description",
        "type",
        "required",
    )
    assert tuple(field.name for field in fields(definition)) == (
        "name",
        "description",
        "parameters",
    )
    assert tuple(field.name for field in fields(tool_catalog)) == ("tools",)
    assert definition.parameters == (tool_parameter, parameter("limit"))
    assert isinstance(definition.parameters, tuple)
    assert isinstance(tool_catalog.tools, tuple)
    with pytest.raises(FrozenInstanceError):
        tool_parameter.name = "path"
    with pytest.raises(FrozenInstanceError):
        definition.name = "other"
    with pytest.raises(FrozenInstanceError):
        tool_catalog.tools = ()


def test_find_tool_by_name_uses_exact_matching_without_normalization() -> None:
    source = catalog()
    empty_name_catalog = ToolCatalog(tools=(tool(""),))

    assert find_tool_by_name(source, "FileRead").name == "FileRead"
    assert find_tool_by_name(source, "web_search ").name == "web_search "
    assert find_tool_by_name(empty_name_catalog, "").name == ""
    for name in ("fileread", " FileRead", "FileRead "):
        with pytest.raises(ToolNotFoundError, match=rf"Tool not found: {name}"):
            find_tool_by_name(source, name)
    assert tuple(item.name for item in source.tools) == (
        "web_search",
        "FileRead",
        "web_search ",
    )


def test_resolve_tool_names_preserves_order_and_duplicates_without_mutation() -> None:
    source = catalog()
    names = ("web_search", "FileRead", "web_search")

    resolved = resolve_tool_names(source, names)

    assert tuple(item.name for item in resolved) == names
    assert resolved[0] is resolved[2]
    assert resolve_tool_names(source, ()) == ()
    assert tuple(item.name for item in source.tools) == (
        "web_search",
        "FileRead",
        "web_search ",
    )


def test_resolve_tool_names_fails_without_returning_partial_results() -> None:
    with pytest.raises(ToolNotFoundError, match="Tool not found: UnknownTool"):
        resolve_tool_names(catalog(), ("web_search", "UnknownTool"))


@pytest.mark.parametrize(
    "tools",
    [
        (tool("web_search"), tool("web_search")),
        (tool("FileRead"), tool("FileRead")),
    ],
)
def test_validate_tool_catalog_rejects_exact_duplicate_names(
    tools: tuple[ToolDefinition, ...],
) -> None:
    with pytest.raises(DuplicateToolNameError, match="Duplicate tool name:") as error:
        validate_tool_catalog(ToolCatalog(tools=tools))

    assert str(error.value) == f"Duplicate tool name: {tools[0].name}"


def test_validate_tool_catalog_keeps_case_and_whitespace_distinct() -> None:
    source = ToolCatalog(
        tools=(tool("FileRead"), tool("fileread"), tool(" FileRead"))
    )

    validate_tool_catalog(source)
