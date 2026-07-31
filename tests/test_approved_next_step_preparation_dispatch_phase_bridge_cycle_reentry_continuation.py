"""Focused Phase 95 dispatch-boundary tests."""

# ruff: noqa: E501, E702, F401

import inspect
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_approved_next_step_preparation_dispatch_phase_bridge_cycle_reentry_continuation,
)
from ai_office.engine.approved_next_step_preparation_dispatch_phase_bridge_cycle_reentry_continuation import (
    ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError,
    ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationError,
)
from ai_office.engine.approved_next_step_preparation_routing_phase_bridge_cycle_reentry_continuation import (
    ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationError,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def wf() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"id": "workflow", "name": "Workflow", "description": "test", "steps": [
        {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
        {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
    ]})


def decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "workflow", "first", 1, "one", "second", 2, "two", "next_step_available")


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "two", "name": "Two", "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep("workflow", "second", 2, "two", "employee", "b", "model", ("tool",))


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = wf(); step = definition.steps[index - 1]
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee,
        tuple(item.id for item in definition.steps[:index]) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error")
    event = RuntimeStepEvent("step_succeeded", "workflow", step.id, index, step.employee, "running", "succeeded", "openai", None, "response", "request", "output", None)
    events = [event]
    if status == "succeeded" and index == 2:
        events.insert(0, RuntimeStepEvent("step_succeeded", "workflow", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None))
    if status == "failed":
        events = [RuntimeStepEvent("step_failed", "workflow", step.id, index, step.employee, "running", "failed", "openai", "api_error", None, "request", None, "safe")]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events))
    return state_path, events_path


def call(*args: object, phase88_function=None) -> object:
    kwargs = {} if phase88_function is None else {"phase88_function": phase88_function}
    return route_approved_next_step_preparation_dispatch_phase_bridge_cycle_reentry_continuation(*args, **kwargs)


def test_public_signature_and_exact_six_argument_identity(tmp_path: Path) -> None:
    assert tuple(inspect.signature(route_approved_next_step_preparation_dispatch_phase_bridge_cycle_reentry_continuation).parameters)[:6] == ("result", "workflow", "approval", "employee", "state_path", "events_path")
    state, events = targets(tmp_path)
    supplied = (decision(), wf(), approval(), employee(), state, events); received: list[object] = []
    def fake(*args: object) -> object:
        received.extend(args); return prepared()
    assert call(*supplied, phase88_function=fake) is not None
    assert all(left is right for left, right in zip(received, supplied, strict=True))


def test_prepare_delegates_once_and_returns_exact_dependency_object(tmp_path: Path) -> None:
    state, events = targets(tmp_path); value = prepared(); calls = 0
    def fake(*_: object) -> object:
        nonlocal calls; calls += 1; return value
    assert call(decision(), wf(), approval(), employee(), state, events, phase88_function=fake) is value
    assert calls == 1


def test_completion_and_failure_are_identity_preserving_zero_call_stops(tmp_path: Path) -> None:
    calls = 0
    def fake(*_: object) -> object:
        nonlocal calls; calls += 1; raise AssertionError
    complete = WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded")
    state, events = targets(tmp_path / "complete", "succeeded", 2)
    assert call(complete, wf(), None, None, state, events, phase88_function=fake) is complete
    failed = PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")
    state, events = targets(tmp_path / "failed", "failed", 1)
    assert call(failed, wf(), None, None, state, events, phase88_function=fake) is failed
    assert calls == 0


@pytest.mark.parametrize("bad", [object(), "substitute"])
def test_exact_models_and_stop_context_rejected(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        call(bad, wf(), approval(), employee(), state, events)
    with pytest.raises(ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        call(decision(), wf(), None, employee(), state, events)


def test_safe_error_identity_and_unexpected_error_are_sanitized(tmp_path: Path) -> None:
    state, events = targets(tmp_path); safe = ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationError("safe")
    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationError) as caught:
        call(decision(), wf(), approval(), employee(), state, events, phase88_function=lambda *_: (_ for _ in ()).throw(safe))
    assert caught.value is safe
    with pytest.raises(ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        call(decision(), wf(), approval(), employee(), state, events, phase88_function=lambda *_: (_ for _ in ()).throw(RuntimeError("secret")))
    assert caught.value.detail.classification == "dependency_error"


def test_mutation_is_compensated_and_malformed_return_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path); before = (state.read_bytes(), events.read_bytes())
    def fake(*_: object) -> object:
        state.write_bytes(b"changed"); events.write_bytes(b"changed"); return object()
    with pytest.raises(ApprovedNextStepPreparationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        call(decision(), wf(), approval(), employee(), state, events, phase88_function=fake)
    assert (state.read_bytes(), events.read_bytes()) == before
