"""Tests for the OpenAI Responses HTTP response boundary."""

import json
from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

import ai_office.providers.openai.responses_response as response_boundary
from ai_office.providers.openai import (
    OpenAIResponsesApiErrorResponse,
    OpenAIResponsesInvalidResponseError,
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesSuccessResponse,
    parse_openai_responses_http_response,
)


def raw_response(
    status_code: int,
    payload: object,
    headers: tuple[tuple[str, str], ...] = (),
) -> OpenAIResponsesRawHttpResponse:
    return OpenAIResponsesRawHttpResponse(
        status_code=status_code,
        reason="reason",
        headers=headers,
        body=json.dumps(payload, ensure_ascii=False).encode(),
    )


def success_payload() -> dict[str, object]:
    return {
        "id": "resp_test",
        "object": "response",
        "status": "completed",
        "output": [{"type": "message", "items": ["first", "second"]}],
        "extra": {"nested": [1, {"value": "preserved"}]},
    }


def test_success_response_preserves_order_request_id_and_immutable_payload() -> None:
    result = parse_openai_responses_http_response(
        raw_response(
            200,
            success_payload(),
            (("X-Request-ID", "first"), ("x-request-id", "second")),
        )
    )

    assert isinstance(result, OpenAIResponsesSuccessResponse)
    assert result.status_code == 200
    assert result.request_id == "first"
    assert result.response_id == "resp_test"
    assert result.object == "response"
    assert result.status == "completed"
    assert result.output[0]["type"] == "message"  # type: ignore[index]
    assert result.payload["extra"]["nested"][1]["value"] == "preserved"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.payload["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.payload["extra"]["nested"][1]["value"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        result.status = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("status_code", [302, 404, 500])
def test_completed_non_2xx_error_response_is_data(status_code: int) -> None:
    result = parse_openai_responses_http_response(
        raw_response(
            status_code,
            {
                "error": {
                    "message": "synthetic failure",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "synthetic_code",
                }
            },
            (("x-request-id", "request_test"),),
        )
    )

    assert isinstance(result, OpenAIResponsesApiErrorResponse)
    assert result.status_code == status_code
    assert result.request_id == "request_test"
    assert result.message == "synthetic failure"
    assert (result.error_type, result.param, result.code) == (
        "invalid_request_error",
        None,
        "synthetic_code",
    )
    assert isinstance(result.payload, MappingProxyType)
    with pytest.raises(FrozenInstanceError):
        result.message = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "resp", "object": "not_response", "status": "completed", "output": []},
        {"id": "", "object": "response", "status": "completed", "output": []},
        {"id": "resp", "object": "response", "status": "", "output": []},
        {"id": "resp", "object": "response", "status": "completed", "output": {}},
    ],
)
def test_invalid_success_contract_is_rejected(payload: object) -> None:
    with pytest.raises(
        OpenAIResponsesInvalidResponseError,
        match="invalid OpenAI success response",
    ):
        parse_openai_responses_http_response(raw_response(200, payload))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"error": {"message": ""}},
        {"error": {"message": "synthetic", "type": 1}},
    ],
)
def test_invalid_api_error_contract_is_rejected(payload: object) -> None:
    with pytest.raises(
        OpenAIResponsesInvalidResponseError,
        match="invalid OpenAI API error response",
    ):
        parse_openai_responses_http_response(raw_response(400, payload))


@pytest.mark.parametrize(
    "response, message",
    [
        (
            OpenAIResponsesRawHttpResponse(200, "reason", (), b"\xff"),
            "invalid UTF-8 response body",
        ),
        (
            OpenAIResponsesRawHttpResponse(200, "reason", (), b"{not JSON}"),
            "invalid JSON response body",
        ),
        (raw_response(200, ["not", "an", "object"]), "response JSON must be an object"),
    ],
)
def test_invalid_body_errors_are_safe(
    response: OpenAIResponsesRawHttpResponse,
    message: str,
) -> None:
    with pytest.raises(OpenAIResponsesInvalidResponseError) as error:
        parse_openai_responses_http_response(response)

    assert str(error.value) == message
    assert "not JSON" not in str(error.value)
    assert "test-secret" not in str(error.value)


@pytest.mark.parametrize("status_code", [200, 400])
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_are_rejected_safely(
    status_code: int,
    constant: str,
) -> None:
    body = (
        b'{"id":"resp","object":"response","status":"completed",'
        b'"output":[],"error":{"message":"synthetic"},"value":'
        + constant.encode()
        + b"}"
    )
    response = OpenAIResponsesRawHttpResponse(status_code, "reason", (), body)

    with pytest.raises(OpenAIResponsesInvalidResponseError) as error:
        parse_openai_responses_http_response(response)

    assert str(error.value) == "invalid JSON response body"
    assert constant not in str(error.value)


def test_body_is_decoded_and_json_is_parsed_once_per_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CountingBytes(bytes):
        def __new__(cls, value: bytes) -> "CountingBytes":
            instance = super().__new__(cls, value)
            instance.decode_calls = 0
            return instance

        def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
            self.decode_calls += 1
            return super().decode(encoding, errors)

    calls = 0
    original_loads = response_boundary.json.loads

    def loads_once(value: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original_loads(value, **kwargs)

    monkeypatch.setattr(response_boundary.json, "loads", loads_once)
    body = CountingBytes(json.dumps(success_payload()).encode())

    parse_openai_responses_http_response(
        OpenAIResponsesRawHttpResponse(200, "reason", (), body)
    )

    assert body.decode_calls == 1
    assert calls == 1
