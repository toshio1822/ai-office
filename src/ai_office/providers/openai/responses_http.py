"""Unauthenticated HTTP request templates for OpenAI Responses JSON."""

from dataclasses import dataclass

from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    ModelExecutionTarget,
    validate_execution_target_for_provider,
)
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


def build_openai_responses_http_request(
    body: str,
    *,
    execution_target: ModelExecutionTarget | None = None,
    target: ModelExecutionTarget | None = None,
) -> OpenAIResponsesHttpRequest:
    """Place an unchanged JSON string in an unauthenticated request template."""
    if (
        execution_target is not None
        and target is not None
        and execution_target != target
    ):
        raise ValueError("execution target arguments do not match")
    selected_target = execution_target if execution_target is not None else target
    if selected_target is None:
        selected_target = DIRECT_OPENAI_EXECUTION_TARGET
    selected_target = validate_execution_target_for_provider(selected_target)
    return OpenAIResponsesHttpRequest(
        method=OPENAI_RESPONSES_HTTP_METHOD,
        url=selected_target.endpoint,
        headers=(("Content-Type", OPENAI_RESPONSES_CONTENT_TYPE),),
        body=body,
    )


def build_openai_responses_http_request_from_payload(
    payload: OpenAIResponsesPayload,
    *,
    execution_target: ModelExecutionTarget | None = None,
    target: ModelExecutionTarget | None = None,
) -> OpenAIResponsesHttpRequest:
    """Build a request template from a payload through the Phase 11 serializer."""
    return build_openai_responses_http_request(
        serialize_openai_responses_payload(payload),
        execution_target=execution_target,
        target=target,
    )


def build_openai_responses_http_request_from_invocation(
    invocation: ModelInvocationRequest,
    catalog: ToolCatalog,
    *,
    execution_target: ModelExecutionTarget | None = None,
    target: ModelExecutionTarget | None = None,
) -> OpenAIResponsesHttpRequest:
    """Build a request template from an invocation through the Phase 11 serializer."""
    return build_openai_responses_http_request(
        serialize_openai_responses_payload_from_invocation(invocation, catalog),
        execution_target=execution_target,
        target=target,
    )
