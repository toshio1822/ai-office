"""Unauthenticated HTTP request templates for OpenAI Responses JSON."""

from dataclasses import dataclass

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai.responses_json import (
    serialize_openai_responses_payload,
    serialize_openai_responses_payload_from_invocation,
)
from ai_office.providers.openai.responses_payload import OpenAIResponsesPayload
from ai_office.tools import ToolCatalog

OPENAI_RESPONSES_HTTP_METHOD = "POST"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_RESPONSES_CONTENT_TYPE = "application/json"


@dataclass(frozen=True)
class OpenAIResponsesHttpRequest:
    """Immutable unauthenticated HTTP request template for OpenAI Responses."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: str


def build_openai_responses_http_request(body: str) -> OpenAIResponsesHttpRequest:
    """Place an unchanged JSON string in an unauthenticated request template."""
    return OpenAIResponsesHttpRequest(
        method=OPENAI_RESPONSES_HTTP_METHOD,
        url=OPENAI_RESPONSES_URL,
        headers=(("Content-Type", OPENAI_RESPONSES_CONTENT_TYPE),),
        body=body,
    )


def build_openai_responses_http_request_from_payload(
    payload: OpenAIResponsesPayload,
) -> OpenAIResponsesHttpRequest:
    """Build a request template from a payload through the Phase 11 serializer."""
    return build_openai_responses_http_request(
        serialize_openai_responses_payload(payload)
    )


def build_openai_responses_http_request_from_invocation(
    invocation: ModelInvocationRequest,
    catalog: ToolCatalog,
) -> OpenAIResponsesHttpRequest:
    """Build a request template from an invocation through the Phase 11 serializer."""
    return build_openai_responses_http_request(
        serialize_openai_responses_payload_from_invocation(invocation, catalog)
    )
