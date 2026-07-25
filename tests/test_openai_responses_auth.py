"""Tests for the explicit OpenAI Responses authentication boundary."""

from dataclasses import FrozenInstanceError, fields

import pytest
from pydantic import ValidationError

from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesAuthenticationError,
    OpenAIResponsesHttpRequest,
    authenticate_openai_responses_http_request,
)


def test_api_key_is_frozen_masks_normal_representations_and_rejects_extra() -> None:
    api_key = OpenAIApiKey(value="test-secret")

    assert "test-secret" not in repr(api_key)
    assert "test-secret" not in str(api_key)
    with pytest.raises(ValidationError):
        api_key.value = "changed"  # type: ignore[assignment]
    with pytest.raises(ValidationError) as error:
        OpenAIApiKey(value="test-secret", extra_value="not allowed")

    assert "test-secret" not in str(error.value)


def test_api_key_rejects_empty_value() -> None:
    with pytest.raises(ValidationError) as error:
        OpenAIApiKey(value="")

    assert "must not be empty" in str(error.value)


@pytest.mark.parametrize("value", ["line\rbreak", "line\nbreak"])
def test_api_key_rejects_multiline_values_without_exposing_them(value: str) -> None:
    with pytest.raises(ValidationError) as error:
        OpenAIApiKey(value=value)

    assert value not in str(error.value)


def test_authenticated_request_is_frozen_and_preserves_request_values() -> None:
    request = OpenAIResponsesHttpRequest(
        method="POST",
        url="https://api.openai.com/v1/responses",
        headers=(
            ("Content-Type", "application/json"),
            ("X-Test", "first"),
            ("X-Test", "second"),
        ),
        body="  日本語 ✨\n",
    )
    api_key = OpenAIApiKey(value="test-secret")

    authenticated = authenticate_openai_responses_http_request(request, api_key)

    assert isinstance(authenticated, OpenAIResponsesAuthenticatedHttpRequest)
    assert [field.name for field in fields(authenticated)] == [
        "method",
        "url",
        "headers",
        "body",
    ]
    assert authenticated.method == request.method
    assert authenticated.url == request.url
    assert authenticated.body == request.body
    assert authenticated.headers == (
        ("Content-Type", "application/json"),
        ("X-Test", "first"),
        ("X-Test", "second"),
        ("Authorization", "Bearer test-secret"),
    )
    assert request.headers == (
        ("Content-Type", "application/json"),
        ("X-Test", "first"),
        ("X-Test", "second"),
    )
    assert api_key.value.get_secret_value() == "test-secret"
    assert authenticated == authenticate_openai_responses_http_request(request, api_key)
    with pytest.raises(FrozenInstanceError):
        authenticated.body = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "header_name", ["Authorization", "authorization", "AUTHORIZATION"]
)
def test_authentication_rejects_existing_authorization_case_insensitively(
    header_name: str,
) -> None:
    request = OpenAIResponsesHttpRequest(
        method="POST",
        url="url",
        headers=((header_name, "existing"),),
        body="body",
    )

    with pytest.raises(
        OpenAIResponsesAuthenticationError,
        match="Request already contains an Authorization header",
    ):
        authenticate_openai_responses_http_request(
            request, OpenAIApiKey(value="test-secret")
        )
