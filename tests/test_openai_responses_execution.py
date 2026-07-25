"""Tests for guarded composition of the OpenAI execution boundaries."""

import json
from collections.abc import Callable

import pytest
from pydantic import SecretStr

from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesTransportError,
    execute_openai_model_invocation,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition

type FakeTransport = Callable[
    [OpenAIResponsesAuthenticatedHttpRequest], OpenAIResponsesRawHttpResponse
]


def tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        parameters=(
            ToolParameterDefinition("query", "query description", "string", True),
        ),
    )


def request(allowed_tools: tuple[str, ...] = ()) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model="model",
        system_instructions="system instructions",
        task_instructions="task instructions",
        allowed_tools=allowed_tools,
    )


def api_key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic-key"))


def approval(
    invocation: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
):
    return approve_model_invocation_execution(
        invocation,
        resolved_tools,
        provider="openai",
        approved_by="test-user",
        approval_id="test-approval",
    )


def raw_response(status_code: int, payload: object) -> OpenAIResponsesRawHttpResponse:
    return OpenAIResponsesRawHttpResponse(
        status_code=status_code,
        reason="synthetic",
        headers=(("x-request-id", "request_123"),),
        body=json.dumps(payload, ensure_ascii=False).encode(),
    )


def success_payload(content: object) -> dict[str, object]:
    return {
        "id": "resp_123",
        "object": "response",
        "status": "completed",
        "output": [{"type": "message", "content": content}],
    }


def test_success_composes_boundaries_once_and_preserves_exact_output() -> None:
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []

    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return raw_response(
            200,
            success_payload(
                [
                    {"type": "output_text", "text": " first\n"},
                    {"type": "output_text", "text": "最後 😀"},
                ]
            ),
        )

    invocation = request(("search", "search"))
    resolved_tools = (tool("search"), tool("search"))
    result = execute_openai_model_invocation(
        invocation,
        resolved_tools,
        api_key(),
        approval(invocation, resolved_tools),
        transport=transport,
    )

    assert isinstance(result, ModelInvocationSuccess)
    assert result.provider == "openai"
    assert result.response_id == "resp_123"
    assert result.request_id == "request_123"
    assert result.status == "completed"
    assert result.text_parts == (" first\n", "最後 😀")
    assert result.text == " first\n最後 😀"
    assert len(calls) == 1
    assert calls[0].headers[-1] == ("Authorization", "Bearer synthetic-key")
    assert calls[0].body.count('"name":"search"') == 2
    assert invocation.allowed_tools == ("search", "search")
    assert resolved_tools == (tool("search"), tool("search"))


def test_empty_supported_output_text_remains_success() -> None:
    invocation = request()
    result = execute_openai_model_invocation(
        invocation,
        (),
        api_key(),
        approval(invocation, ()),
        transport=lambda _: raw_response(200, success_payload([])),
    )

    assert isinstance(result, ModelInvocationSuccess)
    assert result.text_parts == ()
    assert result.text == ""


def test_api_error_is_normalized_as_data() -> None:
    invocation = request()
    result = execute_openai_model_invocation(
        invocation,
        (),
        api_key(),
        approval(invocation, ()),
        transport=lambda _: raw_response(
            429,
            {
                "error": {
                    "message": "synthetic API error",
                    "type": "synthetic_type",
                    "param": "not copied",
                    "code": "synthetic_code",
                }
            },
        ),
    )

    assert result.category == "api_error"  # type: ignore[union-attr]
    assert result.request_id == "request_123"  # type: ignore[union-attr]
    assert result.status_code == 429  # type: ignore[union-attr]
    assert result.provider_error_type == "synthetic_type"  # type: ignore[union-attr]
    assert result.provider_error_code == "synthetic_code"  # type: ignore[union-attr]
    assert "not copied" not in repr(result)


@pytest.mark.parametrize(
    ("transport", "expected_category", "expected_message"),
    [
        (
            lambda _: (_ for _ in ()).throw(
                OpenAIResponsesTransportError("safe transport error")
            ),
            "transport_error",
            "safe transport error",
        ),
        (
            lambda _: OpenAIResponsesRawHttpResponse(200, "synthetic", (), b"not JSON"),
            "invalid_response",
            "invalid JSON response body",
        ),
        (
            lambda _: raw_response(200, success_payload([{"type": "output_text"}])),
            "invalid_output",
            "invalid OpenAI output structure",
        ),
    ],
)
def test_safe_errors_are_normalized(
    transport: FakeTransport,
    expected_category: str,
    expected_message: str,
) -> None:
    invocation = request()
    result = execute_openai_model_invocation(
        invocation,
        (),
        api_key(),
        approval(invocation, ()),
        transport=transport,
    )

    assert result.category == expected_category  # type: ignore[union-attr]
    assert result.message == expected_message  # type: ignore[union-attr]
    assert result.request_id is None  # type: ignore[union-attr]
    assert result.status_code is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("allowed_tools", "resolved_tools"),
    [
        (("search",), ()),
        ((), (tool("search"),)),
        (("search", "read"), (tool("read"), tool("search"))),
        (("search",), (tool("read"),)),
    ],
)
def test_tool_mismatch_fails_before_transport(
    allowed_tools: tuple[str, ...],
    resolved_tools: tuple[ToolDefinition, ...],
) -> None:
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    invocation = request(allowed_tools)
    result = execute_openai_model_invocation(
        invocation,
        resolved_tools,
        api_key(),
        approval(invocation, resolved_tools),
        transport=transport,
    )

    assert result.category == "invalid_request"  # type: ignore[union-attr]
    assert result.message == "resolved tools do not match invocation request"  # type: ignore[union-attr]
    assert calls == 0


def test_rejected_approval_fails_before_transport() -> None:
    invocation = request()
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    result = execute_openai_model_invocation(
        invocation,
        (),
        api_key(),
        ModelInvocationExecutionApproval(False, "openai", "stale", "reviewer", "id"),
        transport=transport,
    )

    assert result.category == "approval_required"  # type: ignore[union-attr]
    assert result.message == "model invocation execution is not approved"  # type: ignore[union-attr]
    assert result.request_id is None  # type: ignore[union-attr]
    assert result.status_code is None  # type: ignore[union-attr]
    assert calls == 0


def test_tool_mismatch_precedes_rejected_approval() -> None:
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    result = execute_openai_model_invocation(
        request(("search",)),
        (),
        api_key(),
        ModelInvocationExecutionApproval(False, "openai", "stale", "reviewer", "id"),
        transport=transport,
    )

    assert result.category == "invalid_request"  # type: ignore[union-attr]
    assert calls == 0


def test_arbitrary_transport_exception_is_not_swallowed() -> None:
    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError, match="unexpected"):
        invocation = request()
        execute_openai_model_invocation(
            invocation,
            (),
            api_key(),
            approval(invocation, ()),
            transport=transport,
        )
