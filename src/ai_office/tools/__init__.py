"""Provider-independent tool catalog definitions and resolution."""

from ai_office.tools.catalog import (
    DEFAULT_TOOL_CATALOG,
    ToolCatalog,
    find_tool_by_name,
    resolve_tool_names,
    validate_tool_catalog,
)
from ai_office.tools.definitions import ToolDefinition, ToolParameterDefinition
from ai_office.tools.errors import (
    DuplicateToolNameError,
    ToolCatalogError,
    ToolNotFoundError,
)

__all__ = [
    "DEFAULT_TOOL_CATALOG",
    "DuplicateToolNameError",
    "ToolCatalog",
    "ToolCatalogError",
    "ToolDefinition",
    "ToolNotFoundError",
    "ToolParameterDefinition",
    "find_tool_by_name",
    "resolve_tool_names",
    "validate_tool_catalog",
]
