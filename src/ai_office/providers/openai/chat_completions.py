"""Minimal non-streaming text-only Chat Completions compatibility."""

import json

from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationResult,
    ModelInvocationSuccess,
    build_model_invocation_task_input,
)
from ai_office.providers.openai.responses_observability import (
    build_openai_responses_response_diagnostics,
)
from ai_office.providers.openai.responses_response import (
    OpenAIResponsesInvalidResponseError,
)
from ai_office.providers.openai.responses_transport import (
    OpenAIResponsesRawHttpResponse,
)


def serialize_openai_chat_completions_request(
    request: ModelInvocationRequest,
) -> str:
    """Serialize the supported text-only, no-tools request subset."""
    if request.allowed_tools:
        raise ValueError("Chat Completions compatibility does not support tools")
    value = {
        "messages": [
            {"content": request.system_instructions, "role": "system"},
            {"content": build_model_invocation_task_input(request), "role": "user"},
        ],
        "model": request.model,
        "stream": False,
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalize_openai_chat_completions_raw_response(
    response: OpenAIResponsesRawHttpResponse,
    *,
    provider: str,
) -> ModelInvocationResult:
    """Normalize the strict minimum Chat Completions response subset."""
    payload = _decode_payload(response)
    request_id = _request_id(response.headers)
    if not 200 <= response.status_code <= 299:
        error = payload.get("error")
        if type(error) is not dict:
            _raise_invalid(response)
        message = error.get("message")
        error_type = error.get("type")
        code = error.get("code")
        if (
            type(message) is not str
            or not message
            or (error_type is not None and type(error_type) is not str)
            or (code is not None and type(code) is not str)
        ):
            _raise_invalid(response)
        return ModelInvocationFailure(
            provider=provider,
            category="api_error",
            message=message,
            request_id=request_id,
            status_code=response.status_code,
            provider_error_type=error_type,
            provider_error_code=code,
        )

    response_id = payload.get("id")
    object_name = payload.get("object")
    choices = payload.get("choices")
    if (
        type(response_id) is not str
        or not response_id
        or object_name != "chat.completion"
        or type(choices) is not list
        or len(choices) != 1
    ):
        _raise_invalid(response)
    choice = choices[0]
    if type(choice) is not dict or choice.get("index") != 0:
        _raise_invalid(response)
    message = choice.get("message")
    finish_reason = choice.get("finish_reason")
    if (
        type(message) is not dict
        or message.get("role") != "assistant"
        or type(finish_reason) is not str
        or not finish_reason
    ):
        _raise_invalid(response)
    content = message.get("content")
    if finish_reason != "stop":
        if content is not None and type(content) is not str:
            _raise_invalid(response)
        return _finish_reason_failure(
            provider=provider,
            request_id=request_id,
            finish_reason=finish_reason,
        )
    if type(content) is not str:
        _raise_invalid(response)
    text = content
    return ModelInvocationSuccess(
        provider=provider,
        response_id=response_id,
        request_id=request_id,
        status=finish_reason,
        text_parts=(text,),
        text=text,
    )


def _finish_reason_failure(
    *,
    provider: str,
    request_id: str | None,
    finish_reason: str,
) -> ModelInvocationFailure:
    if finish_reason == "length":
        message = "Chat Completions output was truncated before normal completion"
    elif finish_reason == "content_filter":
        message = "Chat Completions output was stopped by content filtering"
    else:
        message = "Chat Completions returned an unsupported finish reason"
    return ModelInvocationFailure(
        provider=provider,
        category="invalid_output",
        message=message,
        request_id=request_id,
        status_code=None,
        provider_error_type=None,
        provider_error_code=None,
    )


def _decode_payload(response: OpenAIResponsesRawHttpResponse) -> dict[str, object]:
    try:
        payload = json.loads(
            response.body.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _raise_invalid(response)
    if type(payload) is not dict:
        _raise_invalid(response)
    return payload


def _request_id(headers: tuple[tuple[str, str], ...]) -> str | None:
    for name, value in headers:
        if name.lower() == "x-request-id":
            return value
    return None


def _raise_invalid(response: OpenAIResponsesRawHttpResponse) -> None:
    raise OpenAIResponsesInvalidResponseError(
        "invalid OpenAI-compatible Chat Completions response",
        response_diagnostics=build_openai_responses_response_diagnostics(
            response.status_code, response.headers, response.body
        ),
    ) from None


__all__ = [
    "normalize_openai_chat_completions_raw_response",
    "serialize_openai_chat_completions_request",
]
