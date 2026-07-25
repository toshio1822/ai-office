"""Explicit, unauthenticated-input authentication for OpenAI Responses requests."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from ai_office.providers.openai.responses_http import OpenAIResponsesHttpRequest


class OpenAIApiKey(BaseModel):
    """An explicit OpenAI API key that remains masked in normal representations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: SecretStr

    @field_validator("value")
    @classmethod
    def must_be_nonempty_single_line(cls, value: SecretStr) -> SecretStr:
        """Reject unsafe values without changing or exposing the supplied secret."""
        secret = value.get_secret_value()
        if secret == "":
            raise ValueError("must not be empty")
        if "\r" in secret or "\n" in secret:
            raise ValueError("must not contain carriage returns or line feeds")
        return value


@dataclass(frozen=True)
class OpenAIResponsesAuthenticatedHttpRequest:
    """Immutable OpenAI request template after explicit Bearer authentication."""

    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: str


class OpenAIResponsesAuthenticationError(ValueError):
    """An authentication-boundary error that is safe to present to a user."""


def authenticate_openai_responses_http_request(
    request: OpenAIResponsesHttpRequest,
    api_key: OpenAIApiKey,
) -> OpenAIResponsesAuthenticatedHttpRequest:
    """Append one explicit Bearer Authorization header without altering the request."""
    if any(name.lower() == "authorization" for name, _ in request.headers):
        raise OpenAIResponsesAuthenticationError(
            "Request already contains an Authorization header"
        )

    return OpenAIResponsesAuthenticatedHttpRequest(
        method=request.method,
        url=request.url,
        headers=request.headers
        + (("Authorization", f"Bearer {api_key.value.get_secret_value()}"),),
        body=request.body,
    )
