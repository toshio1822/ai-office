"""Deterministic resolution of tool names through a static catalog."""

from dataclasses import dataclass

from ai_office.tools.definitions import ToolDefinition, ToolParameterDefinition
from ai_office.tools.errors import DuplicateToolNameError, ToolNotFoundError


@dataclass(frozen=True)
class ToolCatalog:
    """An immutable, provider-independent collection of tool definitions."""

    tools: tuple[ToolDefinition, ...]


DEFAULT_TOOL_CATALOG = ToolCatalog(
    tools=(
        ToolDefinition(
            name="web_search",
            description="Search the web for relevant information.",
            parameters=(
                ToolParameterDefinition(
                    name="query",
                    description="The search query.",
                    type="string",
                    required=True,
                ),
            ),
        ),
        ToolDefinition(
            name="FileRead",
            description="Read the contents of a file.",
            parameters=(
                ToolParameterDefinition(
                    name="path",
                    description="The path of the file to read.",
                    type="string",
                    required=True,
                ),
            ),
        ),
    ),
)


def validate_tool_catalog(catalog: ToolCatalog) -> None:
    """Reject duplicate exact tool names without changing catalog values."""
    seen_names: set[str] = set()
    for tool in catalog.tools:
        if tool.name in seen_names:
            raise DuplicateToolNameError(f"Duplicate tool name: {tool.name}")
        seen_names.add(tool.name)


def _find_tool_by_name(catalog: ToolCatalog, tool_name: str) -> ToolDefinition:
    for tool in catalog.tools:
        if tool.name == tool_name:
            return tool
    raise ToolNotFoundError(f"Tool not found: {tool_name}")


def find_tool_by_name(catalog: ToolCatalog, tool_name: str) -> ToolDefinition:
    """Return the definition whose name exactly matches ``tool_name``."""
    validate_tool_catalog(catalog)
    return _find_tool_by_name(catalog, tool_name)


def resolve_tool_names(
    catalog: ToolCatalog,
    tool_names: tuple[str, ...],
) -> tuple[ToolDefinition, ...]:
    """Resolve names in input order without normalizing or deduplicating them."""
    validate_tool_catalog(catalog)
    return tuple(_find_tool_by_name(catalog, tool_name) for tool_name in tool_names)
