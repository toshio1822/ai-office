"""OpenAI-specific pre-runtime request conversion."""

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
    "OpenAIResponsesRequest",
    "build_openai_responses_function_parameters",
    "build_openai_responses_function_property",
    "build_openai_responses_request",
    "build_openai_responses_tool",
    "build_openai_responses_tools",
]
