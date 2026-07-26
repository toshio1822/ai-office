"""Tests for Phase 35 with fake Phase 29 execution."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionReentryCompatibilityError,
    PreparedStepExecutionStart,
    execute_persisted_running_openai_step,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import StepRuntimeExecutionFailure, WorkflowExecutionState
from ai_office.storage import serialize_workflow_execution_state_json


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "first", "name": "F", "employee": "one", "instructions": "a"},
                {
                    "id": "step",
                    "name": "S",
                    "employee": "employee",
                    "instructions": "task",
                },
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "employee",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "system", "task", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "step", 2, "employee", ("first",), None
        ),
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic"))


def approval():
    return approve_model_invocation_execution(
        start().request, (), provider="openai", approved_by="test", approval_id="id"
    )


def test_delegates_once_returns_exact_result_and_keeps_state(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    before = serialize_workflow_execution_state_json(start().running_state).encode()
    target.write_bytes(before)
    calls = 0
    expected = failure()

    def execute(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    result = execute_persisted_running_openai_step(
        start(),
        target,
        workflow(),
        employee(),
        (),
        key(),
        approval(),
        transport=lambda _: None,
        execution_function=execute,
    )  # type: ignore[arg-type]
    assert result is expected
    assert calls == 1
    assert target.read_bytes() == before


def test_mutated_state_is_restored_after_one_call(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    before = serialize_workflow_execution_state_json(start().running_state).encode()
    target.write_bytes(before)

    def execute(*_args: object, **_kwargs: object) -> object:
        target.write_bytes(b"changed")
        return failure()

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError) as caught:
        execute_persisted_running_openai_step(
            start(),
            target,
            workflow(),
            employee(),
            (),
            key(),
            approval(),
            transport=lambda _: None,
            execution_function=execute,
        )  # type: ignore[arg-type]
    assert caught.value.detail.classification == "state_immutability"
    assert target.read_bytes() == before
