"""Validation boundary for completed OpenAI Responses HTTP responses."""

import json
from dataclasses import dataclass
from types import MappingProxyType

from ai_office.providers.openai.responses_transport import (
    OpenAIResponsesRawHttpResponse,
)


@dataclass(frozen=True)
class OpenAIResponsesSuccessResponse:
    """Immutable minimal representation of a successful OpenAI response."""

    status_code: int
    request_id: str | None
    response_id: str
    object: str
    status: str
    output: tuple[object, ...]
    payload: MappingProxyType


@dataclass(frozen=True)
class OpenAIResponsesApiErrorResponse:
    """Immutable minimal representation of a completed OpenAI API error."""

    status_code: int
    request_id: str | None
    message: str
    error_type: str | None
    param: str | None
    code: str | None
    payload: MappingProxyType


type OpenAIResponsesHttpResponse = (
    OpenAIResponsesSuccessResponse | OpenAIResponsesApiErrorResponse
)


class OpenAIResponsesInvalidResponseError(ValueError):
    """Raised when a completed response cannot be represented safely."""


def _freeze_json_value(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {
                key: _freeze_json_value(nested_value)
                for key, nested_value in value.items()
            }
        )
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _find_request_id(headers: tuple[tuple[str, str], ...]) -> str | None:
    for name, value in headers:
        if name.lower() == "x-request-id":
            return value
    return None


def _reject_nonstandard_json_constant(constant: str) -> object:
    raise ValueError(constant)


def _decode_response_payload(
    response: OpenAIResponsesRawHttpResponse,
) -> dict[str, object]:
    try:
        decoded_body = response.body.decode("utf-8")
    except UnicodeDecodeError:
        raise OpenAIResponsesInvalidResponseError(
            "invalid UTF-8 response body"
        ) from None

    try:
        payload = json.loads(
            decoded_body,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (json.JSONDecodeError, ValueError):
        raise OpenAIResponsesInvalidResponseError(
            "invalid JSON response body"
        ) from None

    if not isinstance(payload, dict):
        raise OpenAIResponsesInvalidResponseError(
            "response JSON must be an object"
        )
    return payload


def _parse_success_response(
    response: OpenAIResponsesRawHttpResponse,
    payload: dict[str, object],
) -> OpenAIResponsesSuccessResponse:
    response_id = payload.get("id")
    object_name = payload.get("object")
    status = payload.get("status")
    output = payload.get("output")
    if (
        not isinstance(response_id, str)
        or not response_id
        or object_name != "response"
        or not isinstance(status, str)
        or not status
        or not isinstance(output, list)
    ):
        raise OpenAIResponsesInvalidResponseError(
            "invalid OpenAI success response"
        )

    frozen_payload = _freeze_json_value(payload)
    assert isinstance(frozen_payload, MappingProxyType)
    return OpenAIResponsesSuccessResponse(
        status_code=response.status_code,
        request_id=_find_request_id(response.headers),
        response_id=response_id,
        object=object_name,
        status=status,
        output=tuple(_freeze_json_value(item) for item in output),
        payload=frozen_payload,
    )


def _parse_api_error_response(
    response: OpenAIResponsesRawHttpResponse,
    payload: dict[str, object],
) -> OpenAIResponsesApiErrorResponse:
    error = payload.get("error")
    if not isinstance(error, dict):
        raise OpenAIResponsesInvalidResponseError(
            "invalid OpenAI API error response"
        )

    message = error.get("message")
    optional_fields = tuple(error.get(name) for name in ("type", "param", "code"))
    if not isinstance(message, str) or not message or any(
        value is not None and not isinstance(value, str) for value in optional_fields
    ):
        raise OpenAIResponsesInvalidResponseError(
            "invalid OpenAI API error response"
        )

    frozen_payload = _freeze_json_value(payload)
    assert isinstance(frozen_payload, MappingProxyType)
    error_type, param, code = optional_fields
    return OpenAIResponsesApiErrorResponse(
        status_code=response.status_code,
        request_id=_find_request_id(response.headers),
        message=message,
        error_type=error_type,
        param=param,
        code=code,
        payload=frozen_payload,
    )


def parse_openai_responses_http_response(
    response: OpenAIResponsesRawHttpResponse,
) -> OpenAIResponsesHttpResponse:
    """Decode one completed raw response into an immutable explicit outcome."""
    payload = _decode_response_payload(response)
    if 200 <= response.status_code <= 299:
        return _parse_success_response(response, payload)
    return _parse_api_error_response(response, payload)
