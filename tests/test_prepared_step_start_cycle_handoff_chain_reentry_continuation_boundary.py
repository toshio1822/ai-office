"""Focused Phase 124 prepared-step start cycle handoff chain tests."""

# ruff: noqa: E501

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_start_cycle_handoff_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffReentryContinuationError,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"id": "workflow", "name": "Workflow", "description": "test", "steps": [
        {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
        {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
        {"id": "third", "name": "Third", "employee": "three", "instructions": "c"},
    ]})


def employee(index: int = 3) -> EmployeeDefinition:
    step = workflow().steps[index - 1]
    return EmployeeDefinition.model_validate({"id": step.employee, "name": step.name, "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})


def prepared(index: int = 3) -> PreparedWorkflowStep:
    step = workflow().steps[index - 1]
    return PreparedWorkflowStep("workflow", step.id, index, step.employee, "employee", step.instructions, "model", ("tool",))


def started(index: int = 3) -> PreparedStepExecutionStart:
    value = prepared(index)
    return PreparedStepExecutionStart(ModelInvocationRequest(value.model, value.employee_instructions, value.step_instructions, value.allowed_tool_names), WorkflowExecutionState("workflow", "running", value.step_id, value.step_index, value.employee_id, tuple(item.id for item in workflow().steps[: index - 1]), None))


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "workflow", "third", 3, "three", None, None, None, "last_step_succeeded")


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")


def targets(tmp_path: Path, status: str = "succeeded", index: int = 2) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow()
    step = definition.steps[index - 1]
    completed = tuple(item.id for item in definition.steps[:index]) if status == "succeeded" else ()
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee, completed, None if status == "succeeded" else "api_error")
    events = [RuntimeStepEvent("step_succeeded", "workflow", step.id, index, step.employee, "running", "succeeded", "openai", None, "response", "request", "output", None)]
    if status == "succeeded":
        events = [RuntimeStepEvent("step_succeeded", "workflow", item.id, position, item.employee, "running", "succeeded", "openai", None, "response", "request", "output", None) for position, item in enumerate(definition.steps[:index], 1)]
    else:
        events = [RuntimeStepEvent("step_failed", "workflow", step.id, index, step.employee, "running", "failed", "openai", "api_error", None, "request", None, "safe")]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events), encoding="utf-8")
    return state_path, events_path


def invoke(result: object, person: object | None, state: Path, events: Path, function=None):
    kwargs = {} if function is None else {"phase117_function": function}
    return route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary(result, workflow(), person, state, events, **kwargs)


def test_public_signature_and_public_dependency_source_audit() -> None:
    function = route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary
    parameters = tuple(inspect.signature(function).parameters.values())
    assert tuple(parameter.name for parameter in parameters[:5]) == ("result", "workflow", "employee", "state_path", "events_path")
    assert all(parameter.annotation is object for parameter in parameters[:5])
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parameters[:5])
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default.__name__ == "route_prepared_step_start_cycle_handoff_reentry_continuation_boundary"
    source = Path("src/ai_office/engine/prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary.py").read_text()
    assert "route_prepared_step_start_cycle_handoff_reentry_continuation_boundary" in source
    assert "phase110" not in source.lower()
    assert "prepared_step_start_cycle_reentry_continuation_boundary" not in source
    assert "._validate_" not in source and "._top" not in source and "._raise" not in source


def test_prepared_route_uses_exact_five_arguments_once_and_preserves_return_identity(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied = (prepared(), workflow(), employee(), state, events)
    received: list[object] = []
    returned = started()

    def fake(*args: object) -> object:
        received.extend(args)
        return returned

    assert route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary(*supplied, phase117_function=fake) is returned
    assert all(actual is expected for actual, expected in zip(received, supplied, strict=True))
    assert len(received) == 5


def test_prepared_step_index_two_delegates_once_with_exact_contract(tmp_path: Path) -> None:
    supplied_workflow = workflow()
    supplied_employee = employee(2)
    value = prepared(2)
    state, events = targets(tmp_path, index=1)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    returned = started(2)
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PreparedStepExecutionStart:
        calls.append(args)
        return returned

    actual = route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary(
        value,
        supplied_workflow,
        supplied_employee,
        state,
        events,
        phase117_function=fake,
    )
    assert actual is returned
    assert calls == [(value, supplied_workflow, supplied_employee, state, events)]
    assert returned.request.model == value.model
    assert returned.request.system_instructions == value.employee_instructions
    assert returned.request.task_instructions == value.step_instructions
    assert returned.request.allowed_tools == value.allowed_tool_names
    assert returned.running_state.workflow_id == value.workflow_id
    assert returned.running_state.status == "running"
    assert returned.running_state.current_step_id == value.step_id
    assert returned.running_state.current_step_index == 2
    assert returned.running_state.current_employee_id == value.employee_id
    assert returned.running_state.completed_step_ids == ("first",)
    assert returned.running_state.last_failure_category is None
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("result, status, index", [(completion(), "succeeded", 3), (failure(), "failed", 1)])
def test_terminal_routes_are_zero_call_identity_preserving_stops(tmp_path: Path, result: object, status: str, index: int) -> None:
    state, events = targets(tmp_path, status, index)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert invoke(result, None, state, events, fake) is result and calls == 0


@pytest.mark.parametrize("result", [completion(), failure()])
def test_terminal_routes_reject_employee(tmp_path: Path, result: object) -> None:
    status, index = ("succeeded", 3) if type(result) is WorkflowProgressionDecision else ("failed", 1)
    state, events = targets(tmp_path, status, index)
    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError):
        invoke(result, employee(), state, events)


@pytest.mark.parametrize("bad", [object(), PreparedStepExecutionStart(ModelInvocationRequest("model", "employee", "c", ("tool",)), WorkflowExecutionState("workflow", "running", "third", 3, "three", ("first", "second"), None))])
def test_unsupported_direct_results_are_rejected(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        invoke(bad, None, state, events)
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize("field, value", [("workflow_id", "wrong"), ("step_id", "wrong"), ("step_index", True), ("employee_id", "wrong"), ("employee_instructions", 1), ("step_instructions", 1), ("model", 1), ("allowed_tool_names", ["tool"])])
def test_prepared_fields_are_strict(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError):
        invoke(replace(prepared(), **{field: value}), employee(), state, events)


@pytest.mark.parametrize("bad", [None, object(), employee().model_copy(update={"id": "wrong"})])
def test_employee_is_exact_and_linked(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        invoke(prepared(), bad, state, events)
    assert caught.value.detail.classification == "employee_contract"


def test_dependency_return_is_exact_and_nested_contract_is_checked(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    bad = replace(started(), request=replace(started().request, model="wrong"))
    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, lambda *_: bad)
    assert caught.value.detail.classification == "start_contract"

    class StartChild(PreparedStepExecutionStart):
        pass

    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError):
        invoke(prepared(), employee(), state, events, lambda *_: StartChild(started().request, started().running_state))


def test_predecessor_history_is_strict_and_malformed_history_stops_before_dependency(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0
    events.write_text(events.read_text() + events.read_text(), encoding="utf-8")

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return started()

    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, fake)
    assert caught.value.detail.classification == "terminal_contract" and calls == 0


def test_missing_and_conflicting_targets_are_separately_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    events.unlink()
    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events)
    assert caught.value.detail.classification == "event_target"
    state, events = targets(tmp_path / "conflict")
    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary(prepared(), workflow(), employee(), state, state)
    assert caught.value.detail.classification == "target_conflict"


@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
@pytest.mark.parametrize("mutation", ["none", "state", "events", "both"])
def test_dependency_paths_are_one_call_and_byte_for_byte_compensated(tmp_path: Path, kind: str, mutation: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0
    safe = PreparedStepStartCycleHandoffReentryContinuationError("safe")

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("secret")
        return object()

    expected = PreparedStepStartCycleHandoffReentryContinuationError if kind == "safe" else PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError
    with pytest.raises(expected) as caught:
        invoke(prepared(), employee(), state, events, fake)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before
    if kind == "unexpected":
        assert "secret" not in str(caught.value)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_dependency_mutation_is_rejected_after_restoration(tmp_path: Path, mutation: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def fake(*_: object) -> object:
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        return started()

    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, fake)
    assert caught.value.detail.classification == "start_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failing", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_and_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str) -> None:
    state, events = targets(tmp_path)
    original = Path.write_bytes
    attempts: list[Path] = []

    def write(path: Path, data: bytes) -> int:
        if data.startswith(b"{"):
            attempts.append(path)
            if (path == state and failing in {"state", "both"}) or (path == events and failing in {"events", "both"}):
                raise OSError("rollback")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)

    def fake(*_: object) -> object:
        original(state, b"changed-state")
        original(events, b"changed-events")
        return object()

    with pytest.raises(PreparedStepStartCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, fake)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts == [state, events]
