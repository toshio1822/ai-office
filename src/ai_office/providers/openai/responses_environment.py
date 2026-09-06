"""Explicit environment acquisition for Responses-compatible API keys."""

import os
from collections.abc import Mapping

from ai_office.execution_target import (
    ModelExecutionTarget,
    validate_execution_target_for_provider,
)
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


def load_api_key_for_execution_target(
    target: ModelExecutionTarget,
    environment: Mapping[str, str] | None = None,
) -> OpenAIApiKey:
    """Load the secret named by one already-validated execution target.

    The target carries only the environment-variable *name*.  The returned
    Pydantic secret type is intentionally not part of any workflow or
    persistence model and its normal representation masks the value.
    """
    target = validate_execution_target_for_provider(target)
    source = os.environ if environment is None else environment
    try:
        value = source[target.credential_environment_variable]
    except KeyError as error:
        raise OpenAIApiKeyEnvironmentError(
            "Missing required environment variable: "
            f"{target.credential_environment_variable}"
        ) from error
    return OpenAIApiKey(value=value)


__all__ = [
    "OPENAI_API_KEY_ENVIRONMENT_VARIABLE",
    "OpenAIApiKeyEnvironmentError",
    "load_api_key_for_execution_target",
    "load_openai_api_key_from_environment",
]
