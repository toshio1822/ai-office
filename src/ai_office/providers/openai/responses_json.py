"""Deterministic JSON serialization for OpenAI Responses dictionary payloads."""

import json

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai.responses_dict_payload import (
    JsonValue,
    build_openai_responses_payload_dict,
    build_openai_responses_payload_dict_from_invocation,
)
from ai_office.providers.openai.responses_payload import OpenAIResponsesPayload
from ai_office.tools import ToolCatalog


def serialize_openai_responses_payload_dict(
    payload_dict: dict[str, JsonValue],
) -> str:
    """Serialize a JSON-compatible payload dictionary in compact form."""
    return json.dumps(payload_dict, ensure_ascii=False, separators=(",", ":"))


def serialize_openai_responses_payload_dict_pretty(
    payload_dict: dict[str, JsonValue],
) -> str:
    """Serialize a JSON-compatible payload dictionary with two-space indentation."""
    return json.dumps(payload_dict, ensure_ascii=False, indent=2)


def serialize_openai_responses_payload(payload: OpenAIResponsesPayload) -> str:
    """Convert a payload model to compact JSON through the Phase 10 adapter."""
    return serialize_openai_responses_payload_dict(
        build_openai_responses_payload_dict(payload)
    )


def serialize_openai_responses_payload_from_invocation(
    invocation: ModelInvocationRequest,
    catalog: ToolCatalog,
) -> str:
    """Convert an invocation to compact JSON through the Phase 10 adapter."""
    return serialize_openai_responses_payload_dict(
        build_openai_responses_payload_dict_from_invocation(invocation, catalog)
    )
