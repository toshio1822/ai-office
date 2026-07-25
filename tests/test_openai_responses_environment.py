"""Tests for explicit OpenAI API-key environment acquisition."""

import pytest
from pydantic import ValidationError

from ai_office.providers.openai import (
    OPENAI_API_KEY_ENVIRONMENT_VARIABLE,
    OpenAIApiKey,
    OpenAIApiKeyEnvironmentError,
    load_openai_api_key_from_environment,
)


def test_environment_variable_name_is_exact() -> None:
    assert OPENAI_API_KEY_ENVIRONMENT_VARIABLE == "OPENAI_API_KEY"


def test_loader_uses_supplied_mapping_without_mutating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENAI_API_KEY_ENVIRONMENT_VARIABLE, "process-secret")
    environment = {
        OPENAI_API_KEY_ENVIRONMENT_VARIABLE: "  日本語 ✨  ",
        "UNRELATED_VALUE": "unchanged",
    }

    api_key = load_openai_api_key_from_environment(environment)

    assert isinstance(api_key, OpenAIApiKey)
    assert api_key.value.get_secret_value() == "  日本語 ✨  "
    assert environment == {
        OPENAI_API_KEY_ENVIRONMENT_VARIABLE: "  日本語 ✨  ",
        "UNRELATED_VALUE": "unchanged",
    }
    assert api_key == load_openai_api_key_from_environment(environment)


def test_missing_variable_raises_safe_provider_error() -> None:
    environment = {"UNRELATED_VARIABLE": "unrelated-secret"}

    with pytest.raises(OpenAIApiKeyEnvironmentError) as error:
        load_openai_api_key_from_environment(environment)

    assert OPENAI_API_KEY_ENVIRONMENT_VARIABLE in str(error.value)
    assert "UNRELATED_VARIABLE" not in str(error.value)
    assert "unrelated-secret" not in str(error.value)


def test_empty_value_reuses_phase_13_validation() -> None:
    with pytest.raises(ValidationError) as error:
        load_openai_api_key_from_environment(
            {OPENAI_API_KEY_ENVIRONMENT_VARIABLE: ""}
        )

    assert "must not be empty" in str(error.value)


@pytest.mark.parametrize("value", ["line\rbreak", "line\nbreak"])
def test_multiline_values_reuse_phase_13_validation_without_exposure(
    value: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        load_openai_api_key_from_environment(
            {OPENAI_API_KEY_ENVIRONMENT_VARIABLE: value}
        )

    assert value not in str(error.value)


def test_loader_reads_current_process_environment_when_mapping_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENAI_API_KEY_ENVIRONMENT_VARIABLE, "test-secret")

    api_key = load_openai_api_key_from_environment()

    assert api_key.value.get_secret_value() == "test-secret"
