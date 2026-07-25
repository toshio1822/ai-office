"""OpenAI-specific pre-runtime request conversion."""

from ai_office.providers.openai.responses_auth import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesAuthenticationError,
    authenticate_openai_responses_http_request,
)
from ai_office.providers.openai.responses_dict_payload import (
    build_openai_responses_parameters_dict,
    build_openai_responses_payload_dict,
    build_openai_responses_payload_dict_from_invocation,
    build_openai_responses_property_dict,
    build_openai_responses_tool_dict,
    build_openai_responses_tool_dicts,
)
from ai_office.providers.openai.responses_http import (
    OPENAI_RESPONSES_CONTENT_TYPE,
    OPENAI_RESPONSES_HTTP_METHOD,
    OPENAI_RESPONSES_URL,
    OpenAIResponsesHttpRequest,
    build_openai_responses_http_request,
    build_openai_responses_http_request_from_invocation,
    build_openai_responses_http_request_from_payload,
)
from ai_office.providers.openai.responses_json import (
    serialize_openai_responses_payload,
    serialize_openai_responses_payload_dict,
    serialize_openai_responses_payload_dict_pretty,
    serialize_openai_responses_payload_from_invocation,
)
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
    "OPENAI_RESPONSES_CONTENT_TYPE",
    "OPENAI_RESPONSES_HTTP_METHOD",
    "OPENAI_RESPONSES_URL",
    "OpenAIApiKey",
    "OpenAIResponsesAuthenticatedHttpRequest",
    "OpenAIResponsesAuthenticationError",
    "OpenAIResponsesFunctionParameters",
    "OpenAIResponsesFunctionProperty",
    "OpenAIResponsesFunctionTool",
    "OpenAIResponsesHttpRequest",
    "OpenAIResponsesPayload",
    "OpenAIResponsesRequest",
    "authenticate_openai_responses_http_request",
    "build_openai_responses_function_parameters",
    "build_openai_responses_function_property",
    "build_openai_responses_http_request",
    "build_openai_responses_http_request_from_invocation",
    "build_openai_responses_http_request_from_payload",
    "build_openai_responses_parameters_dict",
    "build_openai_responses_payload",
    "build_openai_responses_payload_dict",
    "build_openai_responses_payload_dict_from_invocation",
    "build_openai_responses_payload_from_invocation",
    "build_openai_responses_property_dict",
    "build_openai_responses_request",
    "build_openai_responses_tool",
    "build_openai_responses_tool_dict",
    "build_openai_responses_tool_dicts",
    "build_openai_responses_tools",
    "serialize_openai_responses_payload",
    "serialize_openai_responses_payload_dict",
    "serialize_openai_responses_payload_dict_pretty",
    "serialize_openai_responses_payload_from_invocation",
]
