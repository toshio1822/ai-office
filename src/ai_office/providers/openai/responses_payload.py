"""OpenAI Responses payload model and pure assembly adapters."""

from dataclasses import dataclass

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai.responses_request import (
    OpenAIResponsesRequest,
    build_openai_responses_request,
)
from ai_office.providers.openai.responses_tool import (
    OpenAIResponsesFunctionTool,
    build_openai_responses_tools,
)
from ai_office.tools import ToolCatalog, resolve_tool_names


@dataclass(frozen=True)
class OpenAIResponsesPayload:
    """Immutable OpenAI request information after tool schema resolution.

    This is not a dictionary, JSON string, or HTTP request body.
    """

    model: str
    instructions: str
    input: str
    tools: tuple[OpenAIResponsesFunctionTool, ...]


def build_openai_responses_payload(
    request: OpenAIResponsesRequest,
    tools: tuple[OpenAIResponsesFunctionTool, ...],
) -> OpenAIResponsesPayload:
    """Combine an OpenAI request boundary and resolved tool schemas."""
    return OpenAIResponsesPayload(
        model=request.model,
        instructions=request.instructions,
        input=request.input,
        tools=tools,
    )


def build_openai_responses_payload_from_invocation(
    invocation: ModelInvocationRequest,
    catalog: ToolCatalog,
) -> OpenAIResponsesPayload:
    """Connect the existing request, resolution, schema, and payload adapters."""
    request = build_openai_responses_request(invocation)
    resolved_tools = resolve_tool_names(catalog, invocation.allowed_tools)
    tools = build_openai_responses_tools(resolved_tools)
    return build_openai_responses_payload(request, tools)
