"""Tests for deterministic OpenAI Responses JSON serialization."""

import json

import pytest

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import (
    OpenAIResponsesPayload,
    build_openai_responses_tool,
    serialize_openai_responses_payload,
    serialize_openai_responses_payload_dict,
    serialize_openai_responses_payload_dict_pretty,
    serialize_openai_responses_payload_from_invocation,
)
from ai_office.tools import (
    ToolCatalog,
    ToolDefinition,
    ToolNotFoundError,
    ToolParameterDefinition,
)


def test_compact_serializer_has_exact_order_and_no_extra_whitespace() -> None:
    payload_dict = {
        "model": "codex",
        "instructions": "Work.",
        "input": "Gather.",
        "tools": [],
    }

    result = serialize_openai_responses_payload_dict(payload_dict)

    assert result == (
        '{"model":"codex","instructions":"Work.","input":"Gather.","tools":[]}'
    )
    assert not result.endswith("\n")
    assert payload_dict["tools"] == []


def test_serializers_preserve_unicode_values_escapes_and_json_meaning() -> None:
    payload_dict = {
        "model": " codex ",
        "instructions": '日本語\n\t"quote" \\ path ✨',
        "input": "",
        "tools": [],
        "nested": {"none": None, "bool": False, "int": 2, "float": 1.5},
    }

    compact = serialize_openai_responses_payload_dict(payload_dict)
    pretty = serialize_openai_responses_payload_dict_pretty(payload_dict)

    assert "日本語" in compact
    assert "✨" in compact
    assert "\\n" in compact
    assert "\\t" in compact
    assert '\\"quote\\"' in compact
    assert "\\\\ path" in compact
    assert '"bool":false' in compact
    assert '"none":null' in compact
    assert "  \"model\": \" codex \"" in pretty
    assert not pretty.endswith("\n")
    assert json.loads(compact) == json.loads(pretty) == payload_dict


def test_serializers_preserve_nested_order_empty_collections_and_determinism() -> None:
    payload_dict = {
        "z": {"second": 2, "first": 1},
        "a": ["first", "second"],
        "empty_dict": {},
        "empty_list": [],
    }

    compact = serialize_openai_responses_payload_dict(payload_dict)

    assert compact == (
        '{"z":{"second":2,"first":1},"a":["first","second"],'
        '"empty_dict":{},"empty_list":[]}'
    )
    assert compact == serialize_openai_responses_payload_dict(payload_dict)
    assert list(payload_dict) == ["z", "a", "empty_dict", "empty_list"]


def test_serialize_payload_reuses_phase_ten_dictionary_conversion() -> None:
    tool = build_openai_responses_tool(
        ToolDefinition(
            name="web_search",
            description="Search.",
            parameters=(
                ToolParameterDefinition("query", "Query", "string", True),
            ),
        )
    )
    payload = OpenAIResponsesPayload("codex", "instructions", "input", (tool, tool))

    result = serialize_openai_responses_payload(payload)

    parsed = json.loads(result)
    assert [item["name"] for item in parsed["tools"]] == ["web_search", "web_search"]
    assert parsed["tools"][0]["parameters"]["required"] == ["query"]
    assert payload.tools == (tool, tool)


def test_serialize_from_invocation_uses_explicit_catalog_and_preserves_order() -> None:
    catalog = ToolCatalog(
        tools=(
            ToolDefinition(
                "first",
                "First",
                (ToolParameterDefinition("query", "Query", "string", True),),
            ),
            ToolDefinition("second", "Second", ()),
        )
    )
    invocation = ModelInvocationRequest(
        "codex", "日本語", "input", ("second", "first", "second")
    )

    result = serialize_openai_responses_payload_from_invocation(invocation, catalog)

    assert [item["name"] for item in json.loads(result)["tools"]] == [
        "second",
        "first",
        "second",
    ]
    assert invocation.allowed_tools == ("second", "first", "second")
    assert tuple(item.name for item in catalog.tools) == ("first", "second")


def test_serialize_from_invocation_propagates_missing_tool_error() -> None:
    invocation = ModelInvocationRequest("codex", "instructions", "input", ("missing",))

    with pytest.raises(ToolNotFoundError, match="Tool not found: missing"):
        serialize_openai_responses_payload_from_invocation(
            invocation, ToolCatalog(tools=())
        )


def test_compact_serializer_propagates_standard_json_errors() -> None:
    payload_dict = {"invalid": object()}

    with pytest.raises(TypeError):
        serialize_openai_responses_payload_dict(payload_dict)  # type: ignore[arg-type]
