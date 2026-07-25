"""OpenAI-specific pre-runtime request conversion."""

from ai_office.providers.openai.responses_request import (
    OpenAIResponsesRequest,
    build_openai_responses_request,
)

__all__ = ["OpenAIResponsesRequest", "build_openai_responses_request"]
