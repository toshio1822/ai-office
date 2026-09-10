"""Tests for pure prepared-step execution start preparation."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from ai_office.engine import (
    PreparedStepExecutionStartCompatibilityError,
    prepare_prepared_step_execution_start,
)
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.invocation import EMPTY_RUNTIME_FACTS, ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    serialize_workflow_execution_state_json,
)


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "next", 2, "employee", "system", "task", "model", ("a", "b")
    )


def history(status: str = "succeeded") -> LoadedWorkflowExecutionHistory:
    event = RuntimeStepEvent(
        "step_succeeded",
        "workflow",
        "current",
        1,
        "old",
        "running",
        "succeeded",
        "openai",
        None,
        "response",
        "request",
        "predecessor output",
        None,
    )
    return LoadedWorkflowExecutionHistory(
        WorkflowExecutionState(
            "workflow",
            status,
            "current",
            1,
            "old",
            ("current",),
            "api_error" if status == "failed" else None,
        ),
        (event,),
    )


def test_returns_exact_request_and_proposed_running_state_immutably() -> None:
    result = prepare_prepared_step_execution_start(
        prepared(), history(), state_source_sha256="a" * 64
    )
    assert (
        result.request.system_instructions,
        result.request.task_instructions,
        result.request.model,
        result.request.allowed_tools,
    ) == ("system", "task", "model", ("a", "b"))
    assert isinstance(result.request, ModelInvocationRequest)
    assert result.request.runtime_facts != EMPTY_RUNTIME_FACTS
    assert {
        fact.key: fact.value for fact in result.request.runtime_facts.facts
    } == {
        "workflow.status": "succeeded",
        "workflow.completed_step_count": 1,
        "predecessor.step_id": "current",
        "predecessor.step_index": 1,
        "predecessor.employee_id": "old",
        "predecessor.provider": "openai",
    }
    assert result.running_state == WorkflowExecutionState(
        "workflow", "running", "next", 2, "employee", ("current",), None
    )
    assert prepare_prepared_step_execution_start(
        prepared(), history(), state_source_sha256="a" * 64
    ) == result
    with pytest.raises(FrozenInstanceError):
        result.running_state = history().state  # type: ignore[misc]


def state_digest(value: LoadedWorkflowExecutionHistory) -> str:
    return sha256(
        serialize_workflow_execution_state_json(value.state).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize("status", ["ready", "running", "failed"])
def test_non_succeeded_history_is_rejected(status: str) -> None:
    with pytest.raises(PreparedStepExecutionStartCompatibilityError):
        value = history(status)
        prepare_prepared_step_execution_start(
            prepared(), value, state_source_sha256=state_digest(value)
        )


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
        prepare_prepared_step_execution_start(  # type: ignore[operator]
            changed(prepared()), history(), state_source_sha256="a" * 64
        )
    assert str(caught.value) == "prepared-step execution start inputs are incompatible"
