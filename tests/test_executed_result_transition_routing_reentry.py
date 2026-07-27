"""Tests for Phase 43 with injected Phase 36 fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionReentryCompatibilityError,
    ExecutedResultTransitionRoutingCompatibilityError,
    WorkflowProgressionDecision,
    persist_executed_result_transition_reentry,
    route_executed_result_transition_reentry,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import serialize_workflow_execution_state_json


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": "step",
                    "name": "S",
                    "employee": "employee",
                    "instructions": "do",
                }
            ],
        }
    )


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "workflow",
        "step",
        1,
        "employee",
        ModelInvocationSuccess(
            "openai", "response", "request", "done", ("out",), "out"
        ),
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "workflow",
        "step",
        1,
        "employee",
        ModelInvocationFailure(
            "openai", "api_error", "safe", "request", None, None, None
        ),
    )


def setup(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    before = serialize_workflow_execution_state_json(
        WorkflowExecutionState("workflow", "running", "step", 1, "employee", (), None)
    ).encode()
    state.write_bytes(before)
    events.write_bytes(b"")
    return state, events, before, b""


@pytest.mark.parametrize("result", [success(), failure()])
def test_runtime_result_routes_once_and_returns_exact_persistence_object(
    tmp_path: Path, result: object
) -> None:
    state, events, _, _ = setup(tmp_path)
    calls = 0
    expected = None

    def phase36(*args: object):
        nonlocal calls, expected
        calls += 1
        assert args == (result, workflow(), state, events)
        expected = persist_executed_result_transition_reentry(*args)
        return expected

    assert (
        route_executed_result_transition_reentry(
            result, workflow(), state, events, transition_reentry_function=phase36
        )
        is expected
    )
    assert calls == 1


def test_completion_returns_same_object_without_dependency_or_write(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "step",
        1,
        "employee",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    calls = 0

    def unexpected(*_: object):
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_executed_result_transition_reentry(
            decision, workflow(), state, events, transition_reentry_function=unexpected
        )
        is decision
    )
    assert calls == 0
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_malformed_return_is_compensated(tmp_path: Path) -> None:
    state, events, before_state, before_events = setup(tmp_path)

    def invalid(*_: object):
        state.write_bytes(b"bad")
        events.write_bytes(b"bad\n")
        return object()

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(), workflow(), state, events, transition_reentry_function=invalid
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_safe_dependency_error_is_preserved_after_compensation(tmp_path: Path) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionReentryCompatibilityError("result_type")

    def invalid(*_: object):
        state.write_bytes(b"bad")
        events.write_bytes(b"bad\n")
        raise expected

    with pytest.raises(ExecutedResultTransitionReentryCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(), workflow(), state, events, transition_reentry_function=invalid
        )
    assert caught.value is expected
    assert state.read_bytes() == before_state and events.read_bytes() == before_events
