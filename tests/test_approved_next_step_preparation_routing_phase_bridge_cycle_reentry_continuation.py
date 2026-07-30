"""Focused Phase 88 strict-boundary tests."""

# ruff: noqa: E501

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationError,
    ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError,
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_approved_next_step_preparation_routing_phase_bridge_cycle_reentry_continuation,
)
from ai_office.engine.approved_next_step_preparation_phase_bridge_cycle_reentry_continuation import (
    route_approved_next_step_preparation_phase_bridge_cycle_reentry_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"id": "workflow", "name": "Workflow", "description": "test", "steps": [
        {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
        {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
    ]})


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "two", "name": "Two", "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})


def decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "workflow", "first", 1, "one", "second", 2, "two", "next_step_available")


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep("workflow", "second", 2, "two", "employee", "b", "model", ("tool",))


def complete() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded")


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow()
    step = definition.steps[index - 1]
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee,
                                   tuple(item.id for item in definition.steps[:index]) if status == "succeeded" else (),
                                   None if status == "succeeded" else "api_error")
    events = [RuntimeStepEvent("step_succeeded", "workflow", definition.steps[0].id, 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None)]
    if status == "succeeded" and index == 2:
        events.append(RuntimeStepEvent("step_succeeded", "workflow", step.id, index, step.employee, "running", "succeeded", "openai", None, "response", "request", "output", None))
    if status == "failed":
        events[0] = RuntimeStepEvent("step_failed", "workflow", step.id, index, step.employee, "running", "failed", "openai", "api_error", None, "request", None, "safe")
    state_path, event_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    event_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events))
    return state_path, event_path


def invoke(result: object, wf: object, approved: object, person: object, state: Path, events: Path, function=None):
    kwargs = {} if function is None else {"phase81_function": function}
    return route_approved_next_step_preparation_routing_phase_bridge_cycle_reentry_continuation(result, wf, approved, person, state, events, **kwargs)


def test_public_signature_and_default_dependency() -> None:
    parameters = tuple(inspect.signature(route_approved_next_step_preparation_routing_phase_bridge_cycle_reentry_continuation).parameters)
    assert parameters[:6] == ("result", "workflow", "approval", "employee", "state_path", "events_path")
    assert tuple(inspect.signature(route_approved_next_step_preparation_phase_bridge_cycle_reentry_continuation).parameters)[:6] == parameters[:6]


def test_prepare_delegates_exact_six_arguments_once_and_returns_same_object(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied = (decision(), workflow(), approval(), employee(), state, events)
    received: list[object] = []
    expected = prepared()

    def phase81(*args: object) -> object:
        received.extend(args)
        return expected

    assert invoke(*supplied, phase81) is expected
    assert all(left is right for left, right in zip(received, supplied, strict=True))


def test_stop_routes_are_zero_call_and_preserve_supplied_identity(tmp_path: Path) -> None:
    calls = 0

    def phase81(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    state, events = targets(tmp_path / "complete", index=2)
    supplied_complete = complete()
    assert invoke(supplied_complete, workflow(), None, None, state, events, phase81) is supplied_complete
    state, events = targets(tmp_path / "failure", "failed")
    supplied_failure = failure()
    assert invoke(supplied_failure, workflow(), None, None, state, events, phase81) is supplied_failure
    assert calls == 0


@pytest.mark.parametrize("bad", [object(), WorkflowProgressionDecision("prepare_next_step", "workflow", "first", 1, "one", "second", 2, "two", "next_step_available")])
def test_exact_result_models_are_required(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    if type(bad) is WorkflowProgressionDecision:
        class Subclass(WorkflowProgressionDecision):
            pass
        bad = Subclass(*(getattr(bad, field) for field in bad.__dataclass_fields__))
    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(bad, workflow(), approval(), employee(), state, events)


@pytest.mark.parametrize("field", ["decision", "workflow_id", "current_step_id", "current_step_index", "current_employee_id", "next_step_id", "next_step_index", "next_employee_id", "reason"])
def test_decision_fields_are_strictly_validated(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    values = {"decision": "workflow_complete", "workflow_id": "wrong", "current_step_id": "wrong", "current_step_index": True, "current_employee_id": "wrong", "next_step_id": "wrong", "next_step_index": 1, "next_employee_id": "wrong", "reason": "wrong"}
    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(replace(decision(), **{field: values[field]}), workflow(), approval(), employee(), state, events)


@pytest.mark.parametrize("context", [(approval(), None), (None, employee()), (approval(), employee())])
@pytest.mark.parametrize("result, status, index", [(complete(), "succeeded", 2), (failure(), "failed", 1)])
def test_stop_routes_reject_context(tmp_path: Path, context: tuple[object, object], result: object, status: str, index: int) -> None:
    state, events = targets(tmp_path, status, index)
    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(result, workflow(), *context, state, events)


def test_direct_persisted_success_and_malformed_targets_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(PersistedExecutionOutcome("persisted_success", "workflow", "first", 1, "one", None), workflow(), None, None, state, events)
    events.unlink()
    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(decision(), workflow(), approval(), employee(), state, events)
    assert caught.value.detail.classification == "event_target"


@pytest.mark.parametrize("field", ["workflow_id", "step_id", "step_index", "employee_id", "employee_instructions", "step_instructions", "model", "allowed_tool_names"])
def test_prepared_return_contract_is_exact(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    values = {"workflow_id": "wrong", "step_id": "wrong", "step_index": True, "employee_id": "wrong", "employee_instructions": 2, "step_instructions": 2, "model": 2, "allowed_tool_names": ["tool"]}
    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(decision(), workflow(), approval(), employee(), state, events, lambda *_: replace(prepared(), **{field: values[field]}))


def test_safe_error_identity_unexpected_sanitization_and_no_retry(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    safe = ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationError("safe")
    calls = 0

    def phase81(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise safe

    with pytest.raises(ApprovedNextStepPreparationPhaseBridgeCycleReentryContinuationError) as caught:
        invoke(decision(), workflow(), approval(), employee(), state, events, phase81)
    assert caught.value is safe and calls == 1

    def unexpected(*_: object) -> object:
        raise RuntimeError("secret")

    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(decision(), workflow(), approval(), employee(), state, events, unexpected)
    assert caught.value.detail.classification == "dependency_error" and "secret" not in str(caught.value)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_dependency_mutation_is_compensated(tmp_path: Path, mutation: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase81(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"changed-state")
        if mutation in ("events", "both"):
            events.write_bytes(b"changed-events")
        return object()

    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(decision(), workflow(), approval(), employee(), state, events, phase81)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_both_rollback_attempts_are_made_and_failure_is_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state, events = targets(tmp_path)
    original_write = Path.write_bytes
    attempted: list[Path] = []

    def write(path: Path, value: bytes) -> int:
        if value.startswith(b"{"):
            attempted.append(path)
            if path == state:
                raise OSError("rollback")
        return original_write(path, value)

    monkeypatch.setattr(Path, "write_bytes", write)

    def phase81(*_: object) -> object:
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(decision(), workflow(), approval(), employee(), state, events, phase81)
    assert caught.value.detail.classification == "dependency_rollback" and attempted == [state, events]


def test_terminal_mismatch_and_read_only_stops_preserve_bytes(tmp_path: Path) -> None:
    state, events = targets(tmp_path / "bad")
    data = json.loads(state.read_text())
    data["workflow_id"] = "other"
    state.write_text(json.dumps(data) + "\n")
    before = state.read_bytes(), events.read_bytes()
    with pytest.raises(ApprovedNextStepPreparationRoutingPhaseBridgeCycleReentryContinuationCompatibilityError):
        invoke(decision(), workflow(), approval(), employee(), state, events)
    assert (state.read_bytes(), events.read_bytes()) == before
