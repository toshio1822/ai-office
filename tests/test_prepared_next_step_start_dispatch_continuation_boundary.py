"""Focused Phase 89 strict-boundary tests."""

# ruff: noqa: E501

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedNextStepStartDispatchContinuationCompatibilityError,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_next_step_start_dispatch_continuation_boundary,
)
from ai_office.engine.prepared_next_step_start_dispatch_phase_bridge_cycle_reentry_continuation import (
    PreparedNextStepStartDispatchPhaseBridgeCycleReentryContinuationError,
    route_prepared_next_step_start_dispatch_phase_bridge_cycle_reentry_continuation,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PreparedSubclass(PreparedWorkflowStep):
    pass


class StartSubclass(PreparedStepExecutionStart):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"id": "workflow", "name": "Workflow", "description": "test", "steps": [
        {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
        {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
    ]})


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "two", "name": "Two", "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep("workflow", "second", 2, "two", "employee", "b", "model", ("tool",))


def started() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(ModelInvocationRequest("model", "employee", "b", ("tool",)), WorkflowExecutionState("workflow", "running", "second", 2, "two", ("first",), None))


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded")


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow()
    step = definition.steps[index - 1]
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee, tuple(item.id for item in definition.steps[:index]) if status == "succeeded" else (), None if status == "succeeded" else "api_error")
    events = []
    if index == 2:
        events.append(RuntimeStepEvent("step_succeeded", "workflow", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None))
    events.append(RuntimeStepEvent("step_succeeded" if status == "succeeded" else "step_failed", "workflow", step.id, index, step.employee, "running", status, "openai", None if status == "succeeded" else "api_error", "response" if status == "succeeded" else None, "request", "output" if status == "succeeded" else None, None if status == "succeeded" else "safe"))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events), encoding="utf-8")
    return state_path, events_path


def invoke(result: object, person: object | None, state: Path, events: Path, function=None):
    kwargs = {} if function is None else {"phase89_function": function}
    return route_prepared_next_step_start_dispatch_continuation_boundary(result, workflow(), person, state, events, **kwargs)


def test_public_signature_and_phase89_signature_are_canonical() -> None:
    parameters = tuple(inspect.signature(route_prepared_next_step_start_dispatch_continuation_boundary).parameters)
    assert parameters[:5] == ("result", "workflow", "employee", "state_path", "events_path")
    assert tuple(inspect.signature(route_prepared_next_step_start_dispatch_phase_bridge_cycle_reentry_continuation).parameters)[:5] == parameters[:5]


def test_prepared_route_delegates_exact_five_arguments_once_and_returns_identity(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied = (prepared(), workflow(), employee(), state, events)
    received: list[object] = []

    def phase89(*args: object) -> object:
        received.extend(args)
        return started()

    result = route_prepared_next_step_start_dispatch_continuation_boundary(*supplied, phase89_function=phase89)
    assert result.request.model == "model" and len(received) == 5
    assert all(actual is expected for actual, expected in zip(received, supplied, strict=True))


@pytest.mark.parametrize("result, status, index", [(completion(), "succeeded", 2), (failure(), "failed", 1)])
def test_stop_routes_are_zero_call_and_preserve_identity(tmp_path: Path, result: object, status: str, index: int) -> None:
    state, events = targets(tmp_path, status, index)
    calls = 0

    def phase89(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert invoke(result, None, state, events, phase89) is result and calls == 0


@pytest.mark.parametrize("result", [completion(), failure()])
def test_stop_routes_reject_employee(tmp_path: Path, result: object) -> None:
    state, events = targets(tmp_path, "failed" if type(result) is PersistedExecutionOutcome else "succeeded", 1 if type(result) is PersistedExecutionOutcome else 2)
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(result, employee(), state, events)
    assert caught.value.detail.classification in {"completion_contract", "failure_contract"}


@pytest.mark.parametrize("value", [object(), SimpleNamespace(step_id="second"), DecisionSubclass("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded"), OutcomeSubclass("persisted_failure", "workflow", "first", 1, "one", "api_error")])
def test_result_models_are_exact(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(value, None, state, events)
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize("field, value", [("workflow_id", "wrong"), ("step_id", "wrong"), ("step_index", True), ("employee_id", "wrong"), ("employee_instructions", 1), ("step_instructions", 1), ("model", 1), ("allowed_tool_names", ["tool"])])
def test_prepared_fields_are_strict(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(replace(prepared(), **{field: value}), employee(), state, events)
    assert caught.value.detail.classification in {"prepared_step_contract", "employee_contract"}


@pytest.mark.parametrize("bad", [None, EmployeeSubclass.model_validate(employee().model_dump()), employee().model_copy(update={"id": "wrong"}), employee().model_copy(update={"id": 2})])
def test_employee_is_exact_and_matches_prepared_step(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), bad, state, events)
    assert caught.value.detail.classification == "employee_contract"


@pytest.mark.parametrize("field, value", [("model", "wrong"), ("system_instructions", "wrong"), ("task_instructions", "wrong"), ("allowed_tools", ("wrong",))])
def test_every_start_request_field_is_validated(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    bad = replace(started(), request=replace(started().request, **{field: value}))
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, lambda *_: bad)
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize("field, value", [("workflow_id", "wrong"), ("status", "succeeded"), ("current_step_id", "wrong"), ("current_step_index", True), ("current_employee_id", "wrong"), ("completed_step_ids", ["first"]), ("last_failure_category", "api_error")])
def test_every_running_state_field_is_validated(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    bad = replace(started(), running_state=replace(started().running_state, **{field: value}))
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, lambda *_: bad)
    assert caught.value.detail.classification == "start_contract"


@pytest.mark.parametrize("bad", [PreparedSubclass("workflow", "second", 2, "two", "employee", "b", "model", ("tool",)), StartSubclass(started().request, started().running_state), SimpleNamespace(request=started().request, running_state=started().running_state), object()])
def test_dependency_return_is_exact(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, lambda *_: bad)
    assert caught.value.detail.classification == "start_contract"


def test_direct_success_and_terminal_predecessor_mismatch_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError):
        invoke(PersistedExecutionOutcome("persisted_success", "workflow", "first", 1, "one", None), None, state, events)
    state, events = targets(tmp_path / "mismatch", "succeeded", 2)
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("mutation", ["none", "state", "events", "both"])
def test_safe_error_identity_and_compensation(tmp_path: Path, mutation: str) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PreparedNextStepStartDispatchPhaseBridgeCycleReentryContinuationError("safe")

    def phase89(*_: object) -> object:
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        raise safe

    with pytest.raises(PreparedNextStepStartDispatchPhaseBridgeCycleReentryContinuationError) as caught:
        invoke(prepared(), employee(), state, events, phase89)
    assert caught.value is safe and (state.read_bytes(), events.read_bytes()) == before


def test_unexpected_error_is_sanitized_and_not_retried(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase89(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret")

    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, phase89)
    assert calls == 1 and caught.value.detail.classification == "dependency_error" and "secret" not in str(caught.value)


def test_mutation_and_malformed_return_are_restored_without_retry(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase89(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return object()

    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, phase89)
    assert caught.value.detail.classification == "start_contract" and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failing", ["state", "events", "both"])
def test_rollback_attempts_both_targets_and_classifies_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str) -> None:
    state, events = targets(tmp_path)
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def write(path: Path, data: bytes) -> int:
        if data.startswith(b"{"):
            attempts.append(path)
            if (path == state and failing in {"state", "both"}) or (path == events and failing in {"events", "both"}):
                raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)

    def phase89(*_: object) -> object:
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events, phase89)
    assert caught.value.detail.classification == "dependency_rollback" and attempts == [state, events]


def test_missing_target_and_target_conflict_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    events.unlink()
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        invoke(prepared(), employee(), state, events)
    assert caught.value.detail.classification == "event_target"
    with pytest.raises(PreparedNextStepStartDispatchContinuationCompatibilityError) as caught:
        route_prepared_next_step_start_dispatch_continuation_boundary(prepared(), workflow(), employee(), state, state)
    assert caught.value.detail.classification == "target_conflict"
