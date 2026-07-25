"""Provider-independent static tool definitions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolParameterDefinition:
    """One static input parameter for a catalogued tool."""

    name: str
    description: str
    type: str
    required: bool


@dataclass(frozen=True)
class ToolDefinition:
    """Provider-independent static input definition for one tool."""

    name: str
    description: str
    parameters: tuple[ToolParameterDefinition, ...]
