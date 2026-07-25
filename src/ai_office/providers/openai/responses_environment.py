"""Explicit environment acquisition for an OpenAI API key."""

import os
from collections.abc import Mapping

from ai_office.providers.openai.responses_auth import OpenAIApiKey

OPENAI_API_KEY_ENVIRONMENT_VARIABLE = "OPENAI_API_KEY"


class OpenAIApiKeyEnvironmentError(ValueError):
    """Raised when the required OpenAI API-key variable is unavailable."""


def load_openai_api_key_from_environment(
    environment: Mapping[str, str] | None = None,
) -> OpenAIApiKey:
    """Load the explicit OpenAI API key without transforming its value."""
    source = os.environ if environment is None else environment
    try:
        value = source[OPENAI_API_KEY_ENVIRONMENT_VARIABLE]
    except KeyError as error:
        raise OpenAIApiKeyEnvironmentError(
            "Missing required environment variable: "
            f"{OPENAI_API_KEY_ENVIRONMENT_VARIABLE}"
        ) from error

    return OpenAIApiKey(value=value)
