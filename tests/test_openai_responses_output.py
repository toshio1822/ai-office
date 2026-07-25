"""Tests for the OpenAI Responses output text boundary."""

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from ai_office.providers.openai import (
    OpenAIResponsesInvalidOutputError,
    OpenAIResponsesOutputText,
    OpenAIResponsesSuccessResponse,
    extract_openai_responses_output_text,
)


def success_response(
    output: tuple[object, ...],
    *,
    response_id: str = "resp_test",
    request_id: str | None = "request_test",
    status: str = "completed",
) -> OpenAIResponsesSuccessResponse:
    return OpenAIResponsesSuccessResponse(
        status_code=200,
        request_id=request_id,
        response_id=response_id,
        object="response",
        status=status,
        output=output,
        payload=MappingProxyType({"output": output}),
    )


def test_extracts_only_supported_output_text_in_order() -> None:
    response = success_response(
        (
            MappingProxyType({"type": "reasoning", "summary": "ignored"}),
            MappingProxyType(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": (
                        MappingProxyType({"type": "refusal", "refusal": "ignored"}),
                        MappingProxyType({"type": "output_text", "text": " first\n"}),
                        MappingProxyType({"type": "output_text", "text": "日本語 😀"}),
                    ),
                }
            ),
            MappingProxyType(
                {
                    "type": "message",
                    "content": (MappingProxyType({"type": "output_text", "text": ""}),),
                }
            ),
        ),
        response_id="resp_123",
        request_id=None,
        status="in_progress",
    )

    result = extract_openai_responses_output_text(response)

    assert result == OpenAIResponsesOutputText(
        response_id="resp_123",
        request_id=None,
        status="in_progress",
        text_parts=(" first\n", "日本語 😀", ""),
        text=" first\n日本語 😀",
    )


def test_no_supported_text_returns_explicit_empty_text() -> None:
    result = extract_openai_responses_output_text(
        success_response(
            (
                MappingProxyType({"type": "reasoning"}),
                MappingProxyType(
                    {
                        "type": "message",
                        "content": (MappingProxyType({"type": "refusal"}),),
                    }
                ),
                "unknown scalar item",
            )
        )
    )

    assert result.text_parts == ()
    assert result.text == ""


@pytest.mark.parametrize(
    "output",
    [
        (MappingProxyType({"type": "message"}),),
        (MappingProxyType({"type": "message", "content": "not a sequence"}),),
        (
            MappingProxyType(
                {
                    "type": "message",
                    "content": (MappingProxyType({"type": "output_text"}),),
                }
            ),
        ),
        (
            MappingProxyType(
                {
                    "type": "message",
                    "content": (MappingProxyType({"type": "output_text", "text": 1}),),
                }
            ),
        ),
    ],
)
def test_invalid_claimed_output_text_structure_raises_safe_error(
    output: tuple[object, ...],
) -> None:
    with pytest.raises(OpenAIResponsesInvalidOutputError) as error:
        extract_openai_responses_output_text(success_response(output))

    assert str(error.value) == "invalid OpenAI output structure"
    assert "output_text" not in str(error.value)
    assert "secret" not in str(error.value)


def test_result_and_input_remain_immutable() -> None:
    output = (
        MappingProxyType(
            {
                "type": "message",
                "content": (
                    MappingProxyType({"type": "output_text", "text": "exact"}),
                ),
            }
        ),
    )
    response = success_response(output)

    result = extract_openai_responses_output_text(response)

    assert response.output is output
    assert response.payload["output"] is output
    assert result.text_parts == ("exact",)
    with pytest.raises(FrozenInstanceError):
        result.text = "changed"  # type: ignore[misc]


def test_extraction_is_deterministic() -> None:
    response = success_response(
        (
            MappingProxyType(
                {
                    "type": "message",
                    "content": (
                        MappingProxyType({"type": "output_text", "text": "same"}),
                    ),
                }
            ),
        )
    )

    assert extract_openai_responses_output_text(response) == (
        extract_openai_responses_output_text(response)
    )
