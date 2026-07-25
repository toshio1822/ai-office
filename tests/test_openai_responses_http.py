"""Tests for immutable OpenAI Responses HTTP request templates."""

import json
from dataclasses import FrozenInstanceError, fields

import pytest

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import (
    OPENAI_RESPONSES_CONTENT_TYPE,
    OPENAI_RESPONSES_HTTP_METHOD,
    OPENAI_RESPONSES_URL,
    OpenAIResponsesFunctionParameters,
    OpenAIResponsesFunctionTool,
    OpenAIResponsesHttpRequest,
    OpenAIResponsesPayload,
    build_openai_responses_http_request,
    build_openai_responses_http_request_from_invocation,
    build_openai_responses_http_request_from_payload,
    build_openai_responses_tool,
)
from ai_office.tools import (
    ToolCatalog,
    ToolDefinition,
    ToolNotFoundError,
    ToolParameterDefinition,
)


def test_http_request_model_is_frozen_and_has_stable_fields() -> None:
    request = OpenAIResponsesHttpRequest("POST", "url", (("Name", "value"),), "body")

    assert [field.name for field in fields(request)] == [
        "method",
        "url",
        "headers",
        "body",
    ]
    assert request.headers == (("Name", "value"),)
    with pytest.raises(FrozenInstanceError):
        request.body = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "body",
    [
        '{"model":"codex","tools":[]}',
        '{\n  "model": "codex"\n}',
        "",
        "  \n日本語 ✨ \\\\ \"quote\"\n",
    ],
)
def test_build_http_request_preserves_body_without_validation(body: str) -> None:
    request = build_openai_responses_http_request(body)

    assert request.method == OPENAI_RESPONSES_HTTP_METHOD == "POST"
    assert request.url == OPENAI_RESPONSES_URL == "https://api.openai.com/v1/responses"
    assert request.headers == (("Content-Type", OPENAI_RESPONSES_CONTENT_TYPE),)
    assert request.headers == (("Content-Type", "application/json"),)
    assert request.body == body
    assert request == build_openai_responses_http_request(body)
    assert not hasattr(request, "authorization")
    assert not hasattr(request, "api_key")


def test_build_http_request_from_payload_uses_compact_phase_eleven_json() -> None:
    tool = build_openai_responses_tool(
        ToolDefinition(
            "search",
            "Search",
            (ToolParameterDefinition("query", "Query", "string", True),),
        )
    )
    payload = OpenAIResponsesPayload(
        "codex", "日本語 ✨", "input", (tool, tool)
    )

    request = build_openai_responses_http_request_from_payload(payload)

    assert request.body == (
        '{"model":"codex","instructions":"日本語 ✨","input":"input",'
        '"tools":[{"type":"function","name":"search","description":"Search",'
        '"parameters":{"type":"object","properties":{"query":{"type":"string",'
        '"description":"Query"}},"required":["query"],'
        '"additionalProperties":false},"strict":false},{"type":"function",'
        '"name":"search","description":"Search","parameters":{"type":"object",'
        '"properties":{"query":{"type":"string","description":"Query"}},'
        '"required":["query"],"additionalProperties":false},"strict":false}]}'
    )
    assert [tool_dict["name"] for tool_dict in json.loads(request.body)["tools"]] == [
        "search",
        "search",
    ]
    assert payload.tools == (tool, tool)


def test_build_http_request_from_invocation_uses_explicit_catalog() -> None:
    catalog = ToolCatalog(
        tools=(
            ToolDefinition("first", "First", ()),
            ToolDefinition("second", "Second", ()),
        )
    )
    invocation = ModelInvocationRequest(
        "codex", "", "", ("second", "first", "second")
    )

    request = build_openai_responses_http_request_from_invocation(invocation, catalog)

    assert [tool["name"] for tool in json.loads(request.body)["tools"]] == [
        "second",
        "first",
        "second",
    ]
    assert json.loads(request.body)["instructions"] == ""
    assert json.loads(request.body)["input"] == ""
    assert invocation.allowed_tools == ("second", "first", "second")
    assert tuple(tool.name for tool in catalog.tools) == ("first", "second")


def test_build_http_request_from_invocation_propagates_unknown_tool() -> None:
    invocation = ModelInvocationRequest("codex", "", "", ("missing",))

    with pytest.raises(ToolNotFoundError, match="Tool not found: missing"):
        build_openai_responses_http_request_from_invocation(
            invocation, ToolCatalog(tools=())
        )


def test_build_http_request_from_payload_propagates_json_type_error() -> None:
    invalid_tool = OpenAIResponsesFunctionTool(
        type="function",
        name="invalid",
        description=object(),  # type: ignore[arg-type]
        parameters=OpenAIResponsesFunctionParameters("object", (), (), False),
        strict=False,
    )
    payload = OpenAIResponsesPayload("codex", "", "", (invalid_tool,))

    with pytest.raises(TypeError):
        build_openai_responses_http_request_from_payload(payload)
