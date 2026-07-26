"""Tests for Phase 42 with an injected Phase 35 fake."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionReentryCompatibilityError,
    PersistedRunningExecutionRoutingCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_reentry,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import StepRuntimeExecutionFailure, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition


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


def resolved_tools() -> tuple[ToolDefinition, ...]:
    return (ToolDefinition("tool", "Tool", ()),)


def key() -> OpenAIApiKey:
    return OpenAIApiKey(value=SecretStr("synthetic"))


def approval():
    return approve_model_invocation_execution(
        start().request,
        resolved_tools(),
        provider="openai",
        approved_by="test",
        approval_id="id",
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None),
    )


def setup(tmp_path: Path) -> tuple[Path, Path, bytes]:
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    before = serialize_workflow_execution_state_json(start().running_state).encode()
    state.write_bytes(before)
    events.write_bytes(b"")
    return state, events, before


def call(state: Path, events: Path, phase35: object, *, result: object | None = None):
    return route_persisted_running_execution_reentry(
        RunningStatePersistenceResult(len(state.read_bytes()))
        if result is None
        else result,
        start(),
        workflow(),
        employee(),
        state,
        events,
        resolved_tools(),
        key(),
        approval(),
        lambda _: None,
        execution_reentry_function=phase35,  # type: ignore[arg-type]
    )


def test_routes_once_returns_same_result_and_keeps_targets(tmp_path: Path) -> None:
    state, events, before = setup(tmp_path)
    calls = 0
    expected = failure()

    def phase35(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        assert args == (
            start(),
            state,
            workflow(),
            employee(),
            resolved_tools(),
            key(),
            approval(),
        )
        assert set(kwargs) == {"transport"} and callable(kwargs["transport"])
        return expected

    assert call(state, events, phase35) is expected
    assert calls == 1
    assert state.read_bytes() == before and events.read_bytes() == b""


def test_completion_stops_without_execution_and_returns_same_decision(
    tmp_path: Path,
) -> None:
    state, events, before = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "step",
        2,
        "employee",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    calls = 0

    def phase35(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    value = route_persisted_running_execution_reentry(
        decision,
        None,
        workflow(),
        None,
        state,
        events,
        None,
        None,
        None,
        None,
        execution_reentry_function=phase35,
    )
    assert value is decision and calls == 0
    assert state.read_bytes() == before and events.read_bytes() == b""


@pytest.mark.parametrize(
    "result",
    [
        object(),
        RunningStatePersistenceResult(True),
        RunningStatePersistenceResult(0),
        RunningStatePersistenceResult(999),
    ],
)
def test_invalid_persistence_contract_does_not_execute(
    tmp_path: Path, result: object
) -> None:
    state, events, _ = setup(tmp_path)
    calls = 0

    def phase35(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return failure()

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        call(state, events, phase35, result=result)
    assert caught.value.detail.classification in {"result_type", "persistence_contract"}
    assert calls == 0


@pytest.mark.parametrize("mode", ["state", "event", "delete"])
def test_changed_targets_are_restored_and_rejected(tmp_path: Path, mode: str) -> None:
    state, events, before = setup(tmp_path)

    def phase35(*_: object, **__: object) -> object:
        if mode == "state":
            state.write_bytes(b"changed")
        elif mode == "event":
            events.write_bytes(b"changed")
        else:
            state.unlink()
            events.unlink()
        return failure()

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        call(state, events, phase35)
    assert caught.value.detail.classification == "execution_contract"
    assert state.read_bytes() == before and events.read_bytes() == b""


def test_safe_phase35_error_is_reraised_after_compensation(tmp_path: Path) -> None:
    state, events, before = setup(tmp_path)
    error = PersistedRunningExecutionReentryCompatibilityError("state_data")

    def phase35(*_: object, **__: object) -> object:
        state.write_bytes(b"changed")
        raise error

    with pytest.raises(PersistedRunningExecutionReentryCompatibilityError) as caught:
        call(state, events, phase35)
    assert caught.value is error and state.read_bytes() == before


def test_unexpected_error_is_safe_and_does_not_disclose_secret(tmp_path: Path) -> None:
    state, events, _ = setup(tmp_path)

    def phase35(*_: object, **__: object) -> object:
        raise RuntimeError("credential=secret")

    with pytest.raises(PersistedRunningExecutionRoutingCompatibilityError) as caught:
        call(state, events, phase35)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
