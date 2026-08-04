"""OpenAI Responses function-tool schema models and pure adapters."""

from dataclasses import dataclass

from ai_office.tools.definitions import ToolDefinition, ToolParameterDefinition


@dataclass(frozen=True)
class OpenAIResponsesFunctionProperty:
    """One OpenAI Responses function parameter property."""

    name: str
    type: str
    description: str


@dataclass(frozen=True)
class OpenAIResponsesFunctionParameters:
    """Immutable static schema information for a function tool's parameters."""

    type: str
    properties: tuple[OpenAIResponsesFunctionProperty, ...]
    required: tuple[str, ...]
    additional_properties: bool


@dataclass(frozen=True)
class OpenAIResponsesFunctionTool:
    """Immutable OpenAI Responses function-tool schema information."""

    type: str
    name: str
    description: str
    parameters: OpenAIResponsesFunctionParameters
    strict: bool


def build_openai_responses_function_property(
    parameter: ToolParameterDefinition,
) -> OpenAIResponsesFunctionProperty:
    """Copy one provider-independent parameter into an OpenAI property model."""
    return OpenAIResponsesFunctionProperty(
        name=parameter.name,
        type=parameter.type,
        description=parameter.description,
    )


def build_openai_responses_function_parameters(
    parameters: tuple[ToolParameterDefinition, ...],
) -> OpenAIResponsesFunctionParameters:
    """Build static function parameter schema information without serialization."""
    return OpenAIResponsesFunctionParameters(
        type="object",
        properties=tuple(
            build_openai_responses_function_property(parameter)
            for parameter in parameters
        ),
        required=tuple(
            parameter.name for parameter in parameters if parameter.required
        ),
        additional_properties=False,
    )


def build_openai_responses_tool(
    tool: ToolDefinition,
) -> OpenAIResponsesFunctionTool:
    """Convert one resolved tool into an OpenAI function-tool schema model."""
    return OpenAIResponsesFunctionTool(
        type="function",
        name=tool.name,
        description=tool.description,
        parameters=build_openai_responses_function_parameters(tool.parameters),
        strict=False,
    )


def build_openai_responses_tools(
    tools: tuple[ToolDefinition, ...],
) -> tuple[OpenAIResponsesFunctionTool, ...]:
    """Convert resolved tools in input order without deduplication."""
    return tuple(build_openai_responses_tool(tool) for tool in tools)
