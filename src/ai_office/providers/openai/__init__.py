"""OpenAI-specific pre-runtime request conversion."""

from ai_office.providers.openai.responses_payload import (
    OpenAIResponsesPayload,
    build_openai_responses_payload,
    build_openai_responses_payload_from_invocation,
)
from ai_office.providers.openai.responses_request import (
    OpenAIResponsesRequest,
    build_openai_responses_request,
)
from ai_office.providers.openai.responses_tool import (
    OpenAIResponsesFunctionParameters,
    OpenAIResponsesFunctionProperty,
    OpenAIResponsesFunctionTool,
    build_openai_responses_function_parameters,
    build_openai_responses_function_property,
    build_openai_responses_tool,
    build_openai_responses_tools,
)

__all__ = [
    "OpenAIResponsesFunctionParameters",
    "OpenAIResponsesFunctionProperty",
    "OpenAIResponsesFunctionTool",
    "OpenAIResponsesPayload",
    "OpenAIResponsesRequest",
    "build_openai_responses_function_parameters",
    "build_openai_responses_function_property",
    "build_openai_responses_payload",
    "build_openai_responses_payload_from_invocation",
    "build_openai_responses_request",
    "build_openai_responses_tool",
    "build_openai_responses_tools",
]
