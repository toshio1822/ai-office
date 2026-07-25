"""Deterministic conversion from OpenAI payload models to Python dictionaries."""

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai.responses_payload import (
    OpenAIResponsesPayload,
    build_openai_responses_payload_from_invocation,
)
from ai_office.providers.openai.responses_tool import (
    OpenAIResponsesFunctionParameters,
    OpenAIResponsesFunctionProperty,
    OpenAIResponsesFunctionTool,
)
from ai_office.tools import ToolCatalog

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def build_openai_responses_property_dict(
    property_definition: OpenAIResponsesFunctionProperty,
) -> dict[str, JsonValue]:
    """Convert one function property without duplicating its parent dictionary key."""
    return {
        "type": property_definition.type,
        "description": property_definition.description,
    }


def build_openai_responses_parameters_dict(
    parameters: OpenAIResponsesFunctionParameters,
) -> dict[str, JsonValue]:
    """Convert parameters while retaining insertion order and required names."""
    properties: dict[str, JsonValue] = {}
    for property_definition in parameters.properties:
        properties[property_definition.name] = build_openai_responses_property_dict(
            property_definition
        )

    return {
        "type": parameters.type,
        "properties": properties,
        "required": list(parameters.required),
        "additionalProperties": parameters.additional_properties,
    }


def build_openai_responses_tool_dict(
    tool: OpenAIResponsesFunctionTool,
) -> dict[str, JsonValue]:
    """Convert one static function tool model to a JSON-compatible dictionary."""
    return {
        "type": tool.type,
        "name": tool.name,
        "description": tool.description,
        "parameters": build_openai_responses_parameters_dict(tool.parameters),
        "strict": tool.strict,
    }


def build_openai_responses_tool_dicts(
    tools: tuple[OpenAIResponsesFunctionTool, ...],
) -> list[dict[str, JsonValue]]:
    """Convert tools to a list while preserving their input order and duplicates."""
    return [build_openai_responses_tool_dict(tool) for tool in tools]


def build_openai_responses_payload_dict(
    payload: OpenAIResponsesPayload,
) -> dict[str, JsonValue]:
    """Convert a static OpenAI payload model to a JSON-compatible dictionary."""
    return {
        "model": payload.model,
        "instructions": payload.instructions,
        "input": payload.input,
        "tools": build_openai_responses_tool_dicts(payload.tools),
    }


def build_openai_responses_payload_dict_from_invocation(
    invocation: ModelInvocationRequest,
    catalog: ToolCatalog,
) -> dict[str, JsonValue]:
    """Compose the Phase 9 payload adapter with this dictionary conversion."""
    payload = build_openai_responses_payload_from_invocation(invocation, catalog)
    return build_openai_responses_payload_dict(payload)
