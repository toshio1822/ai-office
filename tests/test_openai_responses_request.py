"""Tests for OpenAI Responses pre-runtime requests."""

from dataclasses import FrozenInstanceError, fields

import pytest

from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import (
    OpenAIResponsesRequest,
    build_openai_responses_request,
)


def invocation_request(
    *,
    model: str = " model with spaces ",
    system_instructions: str = "\n  You are a writer.\n\nKeep  spaces.\t\n",
    task_instructions: str = "  Write this.\n\nDo not trim.  \n",
    allowed_tools: tuple[str, ...] = (
        "web_search",
        "FileRead",
        "web_search",
        " custom-tool ",
    ),
) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model=model,
        system_instructions=system_instructions,
        task_instructions=task_instructions,
        allowed_tools=allowed_tools,
    )


def test_openai_responses_request_is_frozen_and_has_only_request_fields() -> None:
    request = build_openai_responses_request(invocation_request())

    assert isinstance(request, OpenAIResponsesRequest)
    assert tuple(field.name for field in fields(request)) == (
        "model",
        "instructions",
        "input",
        "allowed_tool_names",
    )
    with pytest.raises(FrozenInstanceError):
        request.model = "other"
    for field_name in (
        "workflow_id",
        "step_id",
        "employee_id",
        "invocation_request",
    ):
        assert not hasattr(request, field_name)


def test_openai_responses_adapter_copies_values_without_modification() -> None:
    source = invocation_request()

    request = build_openai_responses_request(source)

    assert request.model == source.model
    assert request.instructions == source.system_instructions
    assert request.input == source.task_instructions
    assert request.allowed_tool_names == source.allowed_tools
    assert isinstance(request.allowed_tool_names, tuple)
    assert request.instructions == "\n  You are a writer.\n\nKeep  spaces.\t\n"
    assert request.input == "  Write this.\n\nDo not trim.  \n"
    assert request.allowed_tool_names == (
        "web_search",
        "FileRead",
        "web_search",
        " custom-tool ",
    )


@pytest.mark.parametrize(
    ("model", "system_instructions", "task_instructions"),
    [
        ("gpt-4.1", "system", "task"),
        ("unknown-model", "", ""),
        ("", "\n", "\n"),
        (" model name ", "日本語と☃", "\tinput  "),
    ],
)
def test_openai_responses_adapter_does_not_validate_or_infer_models(
    model: str, system_instructions: str, task_instructions: str
) -> None:
    request = build_openai_responses_request(
        invocation_request(
            model=model,
            system_instructions=system_instructions,
            task_instructions=task_instructions,
        )
    )

    assert request.model == model
    assert request.instructions == system_instructions
    assert request.input == task_instructions


@pytest.mark.parametrize("allowed_tools", [(), ("one",), ("A", "a", "A", " x ")])
def test_openai_responses_adapter_preserves_tool_values_and_is_deterministic(
    allowed_tools: tuple[str, ...],
) -> None:
    source = invocation_request(allowed_tools=allowed_tools)

    first = build_openai_responses_request(source)
    second = build_openai_responses_request(source)

    assert first.allowed_tool_names == allowed_tools
    assert isinstance(first.allowed_tool_names, tuple)
    assert first == second
    assert first is not source
