"""Tests for provider-independent model invocation result contracts."""

from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationResult,
    ModelInvocationSuccess,
)


def test_result_models_are_immutable_and_union_is_exported() -> None:
    success = ModelInvocationSuccess(
        provider="provider",
        response_id="response",
        request_id=None,
        status="completed",
        text_parts=("text",),
        text="text",
    )
    failure = ModelInvocationFailure(
        provider="provider",
        category="api_error",
        message="safe message",
        request_id=None,
        status_code=None,
        provider_error_type=None,
        provider_error_code=None,
    )

    assert set(get_args(ModelInvocationResult)) == {
        ModelInvocationSuccess,
        ModelInvocationFailure,
    }
    assert set(get_args(ModelInvocationFailureCategory)) == {
        "api_error",
        "transport_error",
        "invalid_response",
        "invalid_output",
        "invalid_request",
    }
    with pytest.raises(FrozenInstanceError):
        success.text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        failure.message = "changed"  # type: ignore[misc]
