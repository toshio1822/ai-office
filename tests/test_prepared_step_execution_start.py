"""Tests for pure prepared-step execution start preparation."""

from dataclasses import FrozenInstanceError, replace

import pytest

from ai_office.engine import (
    PreparedStepExecutionStartCompatibilityError,
    prepare_prepared_step_execution_start,
)
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import LoadedWorkflowExecutionHistory


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "next", 2, "employee", "system", "task", "model", ("a", "b")
    )


def history(status: str = "succeeded") -> LoadedWorkflowExecutionHistory:
    return LoadedWorkflowExecutionHistory(
        WorkflowExecutionState(
            "workflow",
            status,
            "current",
            1,
            "old",
            ("old", "old"),
            "api_error" if status == "failed" else None,
        ),
        (),  # type: ignore[arg-type]
    )


def test_returns_exact_request_and_proposed_running_state_immutably() -> None:
    result = prepare_prepared_step_execution_start(prepared(), history())
    assert (
        result.request.system_instructions,
        result.request.task_instructions,
        result.request.model,
        result.request.allowed_tools,
    ) == ("system", "task", "model", ("a", "b"))
    assert isinstance(result.request, ModelInvocationRequest)
    assert result.running_state == WorkflowExecutionState(
        "workflow", "running", "next", 2, "employee", ("old", "old"), None
    )
    assert prepare_prepared_step_execution_start(prepared(), history()) == result
    with pytest.raises(FrozenInstanceError):
        result.running_state = history().state  # type: ignore[misc]


@pytest.mark.parametrize("status", ["ready", "running", "failed"])
def test_non_succeeded_history_is_rejected(status: str) -> None:
    with pytest.raises(PreparedStepExecutionStartCompatibilityError):
        prepare_prepared_step_execution_start(prepared(), history(status))


@pytest.mark.parametrize(
    "changed",
    [
        lambda value: replace(value, workflow_id="other"),
        lambda value: replace(value, step_index=1),
        lambda value: replace(value, step_index=True),
        lambda value: replace(value, step_index=0),
        lambda value: replace(value, step_id=""),
        lambda value: replace(value, model=""),
    ],
)
def test_incompatible_prepared_data_is_rejected(changed: object) -> None:
    with pytest.raises(PreparedStepExecutionStartCompatibilityError) as caught:
        prepare_prepared_step_execution_start(changed(prepared()), history())  # type: ignore[operator]
    assert str(caught.value) == "prepared-step execution start inputs are incompatible"
