"""Output text extraction for validated OpenAI Responses success responses."""

from collections.abc import Mapping
from dataclasses import dataclass

from ai_office.providers.openai.responses_response import OpenAIResponsesSuccessResponse


@dataclass(frozen=True)
class OpenAIResponsesOutputText:
    """Immutable text extracted from supported OpenAI Responses output items."""

    response_id: str
    request_id: str | None
    status: str
    text_parts: tuple[str, ...]
    text: str


class OpenAIResponsesInvalidOutputError(ValueError):
    """Raised when a supported output item has an invalid structure."""


def _invalid_output_error() -> None:
    raise OpenAIResponsesInvalidOutputError("invalid OpenAI output structure")


def extract_openai_responses_output_text(
    response: OpenAIResponsesSuccessResponse,
) -> OpenAIResponsesOutputText:
    """Extract supported output text in response and content order."""
    text_parts: list[str] = []

    for output_item in response.output:
        if not isinstance(output_item, Mapping) or output_item.get("type") != "message":
            continue

        if "content" not in output_item:
            _invalid_output_error()
        content = output_item["content"]
        if not isinstance(content, (list, tuple)):
            _invalid_output_error()

        for content_item in content:
            if (
                not isinstance(content_item, Mapping)
                or content_item.get("type") != "output_text"
            ):
                continue

            if "text" not in content_item:
                _invalid_output_error()
            text = content_item["text"]
            if not isinstance(text, str):
                _invalid_output_error()
            text_parts.append(text)

    immutable_text_parts = tuple(text_parts)
    return OpenAIResponsesOutputText(
        response_id=response.response_id,
        request_id=response.request_id,
        status=response.status,
        text_parts=immutable_text_parts,
        text="".join(immutable_text_parts),
    )
