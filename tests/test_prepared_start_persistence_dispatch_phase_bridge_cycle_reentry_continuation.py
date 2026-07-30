"""Focused Phase 90 strict-boundary tests."""

# ruff: noqa: E501

import inspect
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_dispatch_phase_bridge_cycle_reentry_continuation,
)
from ai_office.engine.prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation import (
    PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"id": "workflow", "name": "Workflow", "description": "test", "steps": [
        {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
        {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
    ]})


def person() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "two", "name": "Two", "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(ModelInvocationRequest("model", "employee", "b", ("tool",)), WorkflowExecutionState("workflow", "running", "second", 2, "two", ("first",), None))


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    workflow = definition()
    step = workflow.steps[index - 1]
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee, tuple(item.id for item in workflow.steps[:index]) if status == "succeeded" else (), None if status == "succeeded" else "api_error")
    events = []
    if index == 2:
        events.append(RuntimeStepEvent("step_succeeded", "workflow", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None))
    events.append(RuntimeStepEvent("step_succeeded" if status == "succeeded" else "step_failed", "workflow", step.id, index, step.employee, "running", status, "openai", None if status == "succeeded" else "api_error", "response" if status == "succeeded" else None, "request", "output" if status == "succeeded" else None, None if status == "succeeded" else "safe"))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events), encoding="utf-8")
    return state_path, events_path


def invoke(result: object, employee: object | None, state: Path, events: Path, function=None):
    kwargs = {} if function is None else {"phase83_function": function}
    return route_prepared_start_persistence_dispatch_phase_bridge_cycle_reentry_continuation(result, definition(), employee, state, events, **kwargs)


def test_signature_is_canonical() -> None:
    assert tuple(inspect.signature(route_prepared_start_persistence_dispatch_phase_bridge_cycle_reentry_continuation).parameters)[:5] == ("result", "workflow", "employee", "state_path", "events_path")


def test_prepared_route_calls_phase83_once_and_returns_identity(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    received: list[object] = []
    expected = RunningStatePersistenceResult(len(serialize_workflow_execution_state_json(start().running_state).encode()))

    def dependency(*args: object) -> object:
        received.extend(args)
        state.write_bytes(serialize_workflow_execution_state_json(start().running_state).encode())
        return expected

    actual = invoke(start(), person(), state, events, dependency)
    assert actual is expected
    assert len(received) == 5 and received[0] is not None and received[1] is not None and received[2] is not None
    assert received[3] is state and received[4] is events


@pytest.mark.parametrize("result, status", [
    (WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded"), "succeeded"),
    (PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error"), "failed"),
])
def test_stop_routes_return_same_object_without_call(tmp_path: Path, result: object, status: str) -> None:
    state, events = targets(tmp_path, status, 2 if isinstance(result, WorkflowProgressionDecision) else 1)
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(result, None, state, events, dependency) is result
    assert calls == 0


def test_stop_routes_reject_employee_and_prepared_requires_exact_employee(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    completion = WorkflowProgressionDecision("workflow_complete", "workflow", "first", 1, "one", None, None, None, "last_step_succeeded")
    with pytest.raises(PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(completion, person(), state, events)
    with pytest.raises(PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(start(), None, state, events)


def test_invalid_return_restores_both_targets_and_does_not_retry(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    original = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"bad")
        events.write_bytes(b"also bad")
        return object()

    with pytest.raises(PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(start(), person(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == original


def test_safe_phase83_error_preserves_identity_after_compensation(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    error = PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError("dependency_error")

    def dependency(*args: object) -> object:
        state.write_bytes(b"bad")
        raise error

    with pytest.raises(PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(start(), person(), state, events, dependency)
    assert caught.value is error


def test_unexpected_error_is_sanitized(tmp_path: Path) -> None:
    state, events = targets(tmp_path)

    def dependency(*args: object) -> object:
        raise RuntimeError("secret")

    with pytest.raises(PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(start(), person(), state, events, dependency)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
