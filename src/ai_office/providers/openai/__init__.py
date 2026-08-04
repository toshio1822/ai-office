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
from ai_office.providers.openai.responses_environment import (
    OPENAI_API_KEY_ENVIRONMENT_VARIABLE,
    OpenAIApiKeyEnvironmentError,
    load_openai_api_key_from_environment,
)
from ai_office.providers.openai.responses_execution import (
    OpenAIResponsesTransport,
    execute_openai_model_invocation,
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
from ai_office.providers.openai.responses_output import (
    OpenAIResponsesInvalidOutputError,
    OpenAIResponsesOutputText,
    extract_openai_responses_output_text,
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
from ai_office.providers.openai.responses_response import (
    OpenAIResponsesApiErrorResponse,
    OpenAIResponsesHttpResponse,
    OpenAIResponsesInvalidResponseError,
    OpenAIResponsesSuccessResponse,
    parse_openai_responses_http_response,
)
from ai_office.providers.openai.responses_result import (
    OpenAIResponsesExecutionInputError,
    build_model_invocation_failure_from_execution_approval_error,
    build_model_invocation_failure_from_openai_api_error,
    build_model_invocation_failure_from_openai_execution_input_error,
    build_model_invocation_failure_from_openai_invalid_output_error,
    build_model_invocation_failure_from_openai_invalid_response_error,
    build_model_invocation_failure_from_openai_transport_error,
    build_model_invocation_success_from_openai,
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
from ai_office.providers.openai.responses_transport import (
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesTransportError,
    OpenAIResponsesTransportUrlError,
    send_openai_responses_http_request,
)

__all__ = [
    "OPENAI_RESPONSES_CONTENT_TYPE",
    "OPENAI_RESPONSES_HTTP_METHOD",
    "OPENAI_RESPONSES_URL",
    "OPENAI_API_KEY_ENVIRONMENT_VARIABLE",
    "OpenAIApiKey",
    "OpenAIApiKeyEnvironmentError",
    "OpenAIResponsesAuthenticatedHttpRequest",
    "OpenAIResponsesApiErrorResponse",
    "OpenAIResponsesAuthenticationError",
    "OpenAIResponsesExecutionInputError",
    "OpenAIResponsesFunctionParameters",
    "OpenAIResponsesFunctionProperty",
    "OpenAIResponsesFunctionTool",
    "OpenAIResponsesHttpRequest",
    "OpenAIResponsesHttpResponse",
    "OpenAIResponsesInvalidResponseError",
    "OpenAIResponsesInvalidOutputError",
    "OpenAIResponsesOutputText",
    "OpenAIResponsesPayload",
    "OpenAIResponsesRawHttpResponse",
    "OpenAIResponsesRequest",
    "OpenAIResponsesSuccessResponse",
    "OpenAIResponsesTransportError",
    "OpenAIResponsesTransport",
    "OpenAIResponsesTransportUrlError",
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
    "build_model_invocation_failure_from_openai_api_error",
    "build_model_invocation_failure_from_execution_approval_error",
    "build_model_invocation_failure_from_openai_execution_input_error",
    "build_model_invocation_failure_from_openai_invalid_output_error",
    "build_model_invocation_failure_from_openai_invalid_response_error",
    "build_model_invocation_failure_from_openai_transport_error",
    "build_model_invocation_success_from_openai",
    "extract_openai_responses_output_text",
    "execute_openai_model_invocation",
    "load_openai_api_key_from_environment",
    "parse_openai_responses_http_response",
    "serialize_openai_responses_payload",
    "serialize_openai_responses_payload_dict",
    "serialize_openai_responses_payload_dict_pretty",
    "serialize_openai_responses_payload_from_invocation",
    "send_openai_responses_http_request",
]
